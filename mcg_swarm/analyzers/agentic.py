"""Pure-agentic layout lens (design 2026-07-02): an agent with NO structural
assumptions maps a sheet's complete table layout. The agent proposes STRUCTURE
only — regions/header rows/orientation — never values: handles are re-materialized
deterministically and every downstream value flows through the existing
extraction + quality gate, so a hallucinated layout is caught, not ingested.
`try_layout` exposes the deterministic scorer as a sandbox tool so the agent
iterates until clean BEFORE finalizing. Policy caps bound the loop regardless
of the agent's behavior. Candidates compete in the ensemble like any lens
(confidence 0.7 < vertical's 1.0: identical interpretations dedup to the
vertical label — the "agreed by both approaches" signal)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from mcg_swarm.analyzers.base import LayoutCandidate
from mcg_swarm.coverage import coverage_score, nonempty_cells
from mcg_swarm.schemas import Finding
from mcg_swarm.splitter import handle_from_region
from mcg_swarm.views import TransposedView


class ProposedLayoutTable(BaseModel):
    """One table in an agent layout proposal (view coordinates when transposed)."""
    region: str
    header_row: int
    header_span: int = 1
    orientation: Literal["vertical", "transposed"] = "vertical"


class SheetLayoutPatch(BaseModel):
    """The layout agent's `finalize` output: the full set of tables on the sheet."""
    tables: list[ProposedLayoutTable] = []
    rationale: str = ""


@dataclass(frozen=True)
class AgenticLensPolicy:
    max_tables: int = 12              # guard against runaway proposals
    max_probe_iterations: int = 20    # try_layout calls per sheet


def _finding(sheet: str, message: str) -> Finding:
    return Finding(category="agentic-lens", severity="warning", scope="sheet",
                   source="agent", ref=f"{sheet}!A1", message=message)


def _materialize(patch: SheetLayoutPatch, grid, sheet: str, source,
                 policy: AgenticLensPolicy) -> list[LayoutCandidate]:
    """Proposal -> at most one LayoutCandidate, deterministically. Pure."""
    findings: list[Finding] = []
    tables = list(patch.tables)
    if len(tables) > policy.max_tables:
        findings.append(_finding(
            sheet, f"proposal had {len(tables)} tables; capped at "
                   f"{policy.max_tables}"))
        tables = tables[:policy.max_tables]
    if not tables:
        return []
    if len({t.orientation for t in tables}) > 1:
        findings.append(_finding(
            sheet, "mixed-orientation proposal; kept only the vertical tables "
                   "(one orientation per proposal in v1)"))
        tables = [t for t in tables if t.orientation == "vertical"]
        if not tables:
            # defensive only: unreachable while orientation is a two-value Literal
            return []
    orientation = tables[0].orientation
    view = TransposedView(source) if orientation == "transposed" else None
    eff_grid = view.read_region(sheet) if view is not None else grid
    handles = []
    for pt in tables:
        try:
            handles.append(handle_from_region(
                eff_grid, sheet, pt.region, pt.header_row, pt.header_span))
        except Exception as e:
            findings.append(_finding(
                sheet, f"malformed proposed region {pt.region!r} skipped ({e})"))
    if not handles:
        return []
    total = len(nonempty_cells(eff_grid))
    cov = (coverage_score(eff_grid, [h.region for h in handles]) / total
           if total else 0.0)
    return [LayoutCandidate(method="agentic", handles=tuple(handles),
                            coverage=cov, findings=tuple(findings),
                            confidence=0.7, view=view)]


def _score_proposal(source, grid, sheet: str, tables_arg,
                    policy: AgenticLensPolicy) -> dict:
    """Deterministic scorer behind the try_layout tool: same materialization
    as finalize, scored with the ensemble's own metric. Never raises."""
    try:
        patch = SheetLayoutPatch.model_validate({"tables": tables_arg})
    except Exception as e:
        return {"ok": False, "error": f"invalid proposal: {e}"}
    try:
        cands = _materialize(patch, grid, sheet, source, policy)
        if not cands:
            return {"ok": False, "error": "no valid tables in proposal"}
        c = cands[0]
        # Lazy: structural pulls in the orchestration stack.
        from mcg_swarm.subagent.structural import score_handles
        c_src = c.view if c.view is not None else source
        c_grid = c.view.read_region(sheet) if c.view is not None else grid
        cov, errors, gaps = score_handles(c_src, c_grid, list(c.handles), sheet)
        return {"ok": True, "tables": len(c.handles), "coverage_cells": cov,
                "errors": errors, "gaps": gaps,
                "notes": [f.message for f in c.findings]}
    except Exception as e:  # a hostile proposal must not sink the agent loop
        return {"ok": False, "error": f"scoring failed: {e}"}


def _build_agentic_toolset(source, grid, sheet: str, policy: AgenticLensPolicy,
                           counter: dict) -> list:
    """Read-only sheet probes + the try_layout sandbox scorer (budgeted)."""
    # Lazy: subagent pulls in the orchestration stack.
    from mcg_swarm.subagent.structural_tools import SheetView, build_sheet_toolset
    from mcg_swarm.subagent.tools import Tool

    tools = build_sheet_toolset(SheetView(source, sheet))

    def _try(args):
        counter["probes"] += 1
        if counter["probes"] > policy.max_probe_iterations:
            return {"ok": False,
                    "error": "probe budget exhausted — call finalize now with "
                             "your best layout"}
        return _score_proposal(source, grid, sheet,
                               (args or {}).get("tables", []), policy)

    tools.append(Tool(
        "try_layout",
        "Score a candidate layout WITHOUT committing it: pass the same `tables` "
        "list you would pass to `finalize`. Returns deterministic metrics "
        "(coverage_cells, errors, gaps) — iterate until errors and gaps are 0 "
        "and coverage stops improving, then finalize the same list.",
        {"type": "object",
         "properties": {"tables": {"type": "array"}},
         "required": ["tables"]},
        _try))
    return tools


AGENTIC_SYSTEM = (
    "You are mapping the COMPLETE table layout of ONE spreadsheet sheet with NO "
    "prior structural assumptions — the sheet may hold several tables, transposed "
    "tables, title banners, notes, or chart areas. Inspect the actual cells with "
    "the read-only tools. Iterate with `try_layout` until your layout scores "
    "clean (maximal coverage_cells, zero errors, zero gaps), then call `finalize` "
    "with the SAME tables list. Every table needs its A1 `region`, absolute "
    "`header_row`, `header_span` (1 unless a genuine multi-row header), and "
    "`orientation`: 'vertical' when headers run across the top, 'transposed' when "
    "they run down the first column. For transposed tables give region and "
    "header_row in TRANSPOSED coordinates (the sheet as if rows and columns were "
    "swapped). All tables in one proposal must share ONE orientation. Exclude "
    "banners, notes, and chart areas from every region. Never invent cells or "
    "tables."
)


def _agentic_seed(sheet: str, grid) -> str:
    n_rows = len(grid)
    n_cols = max((len(r) for r in grid), default=0)
    return "\n".join([
        f"Map the complete table layout of sheet {sheet!r} "
        f"(~{n_rows} used rows x {n_cols} used columns).",
        "Start with `dimensions` and `peek_rows`, probe candidate layouts with "
        "`try_layout`, and only `finalize` a layout you have scored.",
    ])


class AgenticLayoutLens:
    """The pure-agentic lens: just another SheetAnalyzer to the ensemble."""

    name = "agentic"
    needs_runner = True

    def __init__(self, runner=None, policy: AgenticLensPolicy | None = None):
        self._runner = runner
        self._policy = policy or AgenticLensPolicy()

    def analyze(self, grid, sheet: str, source=None) -> list[LayoutCandidate]:
        if self._runner is None or source is None:
            return []  # graceful degradation; run_swarm's validation build is runner-less
        counter = {"probes": 0}
        tools = _build_agentic_toolset(source, grid, sheet, self._policy, counter)
        raw = self._runner.run(_agentic_seed(sheet, grid), tools,
                               schema=SheetLayoutPatch, system=AGENTIC_SYSTEM)
        patch = SheetLayoutPatch.model_validate(raw)
        return _materialize(patch, grid, sheet, source, self._policy)
