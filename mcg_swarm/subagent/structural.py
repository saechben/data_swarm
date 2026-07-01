# mcg_swarm/subagent/structural.py
"""Layer 2 — agent boundary alteration (verify-before-accept).

Phase 1 DETECTS a dropped table (`uncovered-data`) but keeps the deterministic single
region. Layer 2, when a runner is injected, lets the agent propose a whole-sheet re-cut
into multiple vertical tables. Every proposal is materialised into a real handle, scored
`(coverage, errors, gaps)` against the deterministic baseline, and accepted ONLY if strictly
better (more covered non-empty cells, no more errors, no more interior fragmentation) — and
then only if it survives live re-validation in run_swarm. A rejected or hallucinated re-cut
is a no-op: the deterministic handle is kept and the finding is annotated. Never raises.

Scope: vertical re-cuts only. Transposed proposals are never built (detection-only).
"""
from __future__ import annotations

from typing import Literal

from openpyxl.utils import range_boundaries
from pydantic import BaseModel

from mcg_swarm.coverage import coverage_score, nonempty_cells, region_cells, scan_handle
from mcg_swarm.orchestrator import orchestrate_table


class ProposedTable(BaseModel):
    """One table in an agent re-cut proposal (absolute coordinates)."""
    region: str
    header_row: int
    header_span: int = 1
    orientation: Literal["vertical", "transposed"] = "vertical"


class SheetRecutPatch(BaseModel):
    """The structural agent's `finalize` output: the full set of tables on the sheet."""
    tables: list[ProposedTable] = []
    rationale: str = ""


def _region_gaps(grid: list[tuple], handle) -> int:
    """Count fully-blank rows/cols strictly INSIDE a handle's region box.

    A coherent single table has none. A region that fuses two tables has >=1: a
    stacked pair leaves a blank separator row between them, a side-by-side pair
    leaves a blank gutter column. This is the deterministic guard against a greedy
    "one giant region" proposal that inflates coverage_score (a monotone count of
    claimed non-empty cells) while dropping uncovered-data residue errors — the two
    static signals that otherwise move together when you over-claim. Edge rows/cols
    are excluded so a tight cut scores 0; only interior blanks count.
    `range_boundaries` returns (min_col, min_row, max_col, max_row); grid[0] == row 1.
    """
    min_col, min_row, max_col, max_row = range_boundaries(handle.region)

    def cell(r: int, c: int):
        row = grid[r - 1] if 0 <= r - 1 < len(grid) else ()
        return row[c - 1] if 0 <= c - 1 < len(row) else None

    gaps = 0
    for r in range(min_row + 1, max_row):        # interior rows only
        if all(cell(r, c) in (None, "") for c in range(min_col, max_col + 1)):
            gaps += 1
    for c in range(min_col + 1, max_col):        # interior cols only
        if all(cell(r, c) in (None, "") for r in range(min_row, max_row + 1)):
            gaps += 1
    return gaps


def score_handles(source, grid: list[tuple], handles, sheet: str) -> tuple[int, int, int]:
    """Deterministic acceptance metric for a handle set: (coverage, error_count, gap_count).

    coverage:    non-empty cells covered by the union of handle regions (Phase-1 metric).
    error_count: residue-scan error findings + pure-static orchestration errors, summed.
    gap_count:   fully-blank interior rows/cols across handles — the over-claim guard.
    Orchestration runs WITHOUT a subagent/validator so Layer 2 cannot recurse; the
    accepted candidate is re-validated with the live pipeline later, in run_swarm.
    """
    coverage = coverage_score(grid, [h.region for h in handles])
    errors, gaps = 0, 0
    for i, h in enumerate(handles):
        errors += sum(1 for f in scan_handle(grid, h, sheet)
                      if f.severity == "error" and f.scope == "table")
        table = orchestrate_table(
            source, h, table_id=f"__score_{i}",
            llm=None, subagent=None, table_validator=None)
        errors += len(table.errors)
        gaps += _region_gaps(grid, h)
    return coverage, errors, gaps


import dataclasses
import json  # noqa: F401 — reserved for future seed serialisation

from mcg_swarm.schemas import Finding
from mcg_swarm.splitter import handle_from_region
from mcg_swarm.subagent.structural_tools import SheetView, build_sheet_toolset


STRUCTURAL_SYSTEM = (
    "You are correcting the TABLE BOUNDARIES of ONE spreadsheet. A fast deterministic pass "
    "cut the sheet into a single region, but a whole-sheet scan found tabular data OUTSIDE "
    "that region — a second table was likely dropped. Use the read-only tools to inspect the "
    "ENTIRE sheet, then call `finalize` with the COMPLETE set of vertically-oriented tables "
    "you can see. Each table needs its absolute A1 `region`, the absolute `header_row`, and "
    "`header_span` (1 unless there is a genuine two-row header). List EVERY real table on the "
    "sheet, not only the dropped one. If the single existing region is already correct, return "
    "an empty `tables` list. Never invent cells, regions, or tables."
)


@dataclasses.dataclass
class SheetReview:
    handles: list                       # TableHandle(s) to orchestrate
    detect_findings: list               # list[list[Finding]] aligned with handles (table scope)
    sheet_findings: list                # list[Finding] workbook/sheet scope, annotated
    recut: bool = False                 # True only when a candidate replaced the baseline


@dataclasses.dataclass
class StructuralPolicy:
    max_tables: int = 12                 # guard against runaway proposals


class StructuralReviewer:
    """Agent boundary alteration over one sheet, verify-before-accept. Never raises."""

    def __init__(self, runner, policy: "StructuralPolicy | None" = None) -> None:
        self._runner = runner
        self._policy = policy or StructuralPolicy()

    def review(self, source, handle, grid: list[tuple], scan) -> SheetReview:
        sheet_scope = [f for f in scan if f.scope == "sheet"]
        table_scope = [f for f in scan if f.scope != "sheet"]
        try:
            patch = self._run_agent(source, handle, grid)
            if not patch.tables:
                return self._declined(handle, sheet_scope, table_scope)
            candidate = self._build_candidate(grid, handle.sheet, patch)
            if candidate and len(candidate) <= self._policy.max_tables:
                base = score_handles(source, grid, [handle], handle.sheet)
                cand = score_handles(source, grid, candidate, handle.sheet)
                # three-way strict-better: more coverage, no new errors, no new gaps
                if cand[0] > base[0] and cand[1] <= base[1] and cand[2] <= base[2]:
                    return self._accept(candidate, grid, sheet_scope, base, cand)
            return self._reject(handle, sheet_scope, table_scope)
        except Exception:
            return SheetReview([handle], [table_scope], sheet_scope)

    # -- agent + candidate construction -------------------------------------

    def _run_agent(self, source, handle, grid) -> SheetRecutPatch:
        view = SheetView(source, handle.sheet)
        tools = build_sheet_toolset(view)
        seed = _structural_seed(handle)
        raw = self._runner.run(seed, tools, schema=SheetRecutPatch,
                               system=STRUCTURAL_SYSTEM)
        return SheetRecutPatch.model_validate(raw)

    def _build_candidate(self, grid, sheet, patch: SheetRecutPatch):
        out = []
        for pt in patch.tables:
            if pt.orientation != "vertical":
                continue  # transpose alteration is out of scope (detection-only)
            try:
                out.append(handle_from_region(
                    grid, sheet, pt.region, pt.header_row, pt.header_span))
            except Exception:
                continue  # a malformed region must not sink the whole proposal
        return out

    # -- outcomes -----------------------------------------------------------

    def _accept(self, candidate, grid, sheet_scope, base, cand) -> SheetReview:
        action = (f"re-cut sheet into {len(candidate)} tables "
                  f"[{', '.join(h.region for h in candidate)}]; "
                  f"coverage {base[0]}->{cand[0]}, errors {base[1]}->{cand[1]}, "
                  f"gaps {base[2]}->{cand[2]}")
        fixed = [f.model_copy(update={"resolution": "fixed", "agent_action": action})
                 for f in sheet_scope]

        # Union of all candidate regions — used to distinguish cross-handle artifacts
        # from genuine still-uncovered blocks.
        union_covered: set[tuple[int, int]] = set()
        for h in candidate:
            union_covered |= region_cells(h.region)
        ne = nonempty_cells(grid)

        def _is_artifact(f) -> bool:
            """True iff this uncovered-data finding's block is fully covered by the
            union of all candidate regions (a sibling handle covers it, so it is a
            cross-scan false positive, not a genuine dropped table)."""
            if "!" not in f.ref:
                return False  # can't parse ref — treat as genuine/keep
            a1range = f.ref.split("!", 1)[1]
            block_cells = region_cells(a1range) & ne
            return bool(block_cells) and block_cells <= union_covered

        per_handle, residual = [], []
        for h in candidate:                     # Fix 1: extend/append both inside loop
            s = scan_handle(grid, h, h.sheet)
            per_handle.append([f for f in s if f.scope != "sheet"])
            for f in s:
                if f.scope == "sheet":
                    # Fix 2: keep uncovered-data only when it is a genuine leftover
                    # (block NOT fully covered by the union), not a cross-handle artifact.
                    if f.category == "uncovered-data" and _is_artifact(f):
                        continue
                    residual.append(f)

        # Deduplicate by ref: a genuine leftover block is detected by every handle's
        # scan; keep only the first occurrence so it appears exactly once.
        seen_refs: set[str] = set()
        deduped: list = []
        for f in residual:
            if f.ref not in seen_refs:
                seen_refs.add(f.ref)
                deduped.append(f)

        return SheetReview(list(candidate), per_handle, fixed + deduped, recut=True)

    def _reject(self, handle, sheet_scope, table_scope) -> SheetReview:
        action = ("agent proposed a re-cut that did not strictly improve coverage without "
                  "adding errors — kept deterministic boundaries")
        rej = [f.model_copy(update={"resolution": "rejected", "agent_action": action})
               for f in sheet_scope]
        return SheetReview([handle], [table_scope], rej)

    def _declined(self, handle, sheet_scope, table_scope) -> SheetReview:
        action = "agent reviewed the whole sheet and proposed no re-cut"
        seen = [f.model_copy(update={"agent_action": action}) for f in sheet_scope]
        return SheetReview([handle], [table_scope], seen)


def _structural_seed(handle) -> str:
    return "\n".join([
        "A deterministic pass cut this sheet into a SINGLE table, but a whole-sheet scan "
        "found tabular data outside it (a dropped table).",
        f"Sheet: {handle.sheet}   deterministically-chosen region: {handle.region}   "
        f"header_row: {handle.header_row}",
        "Inspect the entire sheet with `dimensions`, `peek_rows`, and `peek_region`, then "
        "call `finalize` with the full set of vertical tables (region, header_row, "
        "header_span). If the single region is already correct, return empty `tables`.",
    ])
