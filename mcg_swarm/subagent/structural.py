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

from mcg_swarm.coverage import coverage_score, scan_handle
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
