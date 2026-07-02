"""Stage-2 layout arbiter (spec §4.5): on genuine lens disagreement, an agent
chooses among the top-K ranked candidates. Pick-one-of-K, never invent — the
same bounded-blast-radius discipline as the structural re-cut gate. May raise;
assess_sheet_full owns the never-raise policy and the K-bounds check."""
from __future__ import annotations

from pydantic import BaseModel


class ArbiterVerdict(BaseModel):
    """The arbiter agent's `finalize` output."""
    choice: int
    rationale: str = ""


ARBITER_SYSTEM = (
    "You are choosing between COMPETING LAYOUT INTERPRETATIONS of ONE spreadsheet "
    "sheet. Deterministic lenses disagree about how this sheet is structured. Use "
    "the read-only tools to inspect the actual cells, then call `finalize` with the "
    "`choice` index of the interpretation that best matches the real data layout. "
    "You MUST pick one of the listed candidates — never invent a new layout. Prefer "
    "the interpretation whose header placement, orientation, and table boundaries "
    "match what you can see in the cells."
)


class LayoutArbiter:
    """Runs the injected AgentRunner to pick among ranked candidates."""

    def __init__(self, runner) -> None:
        self._runner = runner

    def choose(self, ranked_topk, *, source, sheet: str) -> int:
        """ranked_topk: [(LayoutCandidate, (coverage, errors, gaps)), ...],
        best-first. Returns the agent's chosen index into that list."""
        # Lazy: subagent pulls in the orchestration stack; keep analyzers light.
        from mcg_swarm.subagent.structural_tools import SheetView, build_sheet_toolset
        tools = build_sheet_toolset(SheetView(source, sheet))
        seed = _arbiter_seed(ranked_topk, sheet)
        raw = self._runner.run(seed, tools, schema=ArbiterVerdict,
                               system=ARBITER_SYSTEM)
        return int(ArbiterVerdict.model_validate(raw).choice)


def _describe(i: int, candidate, score) -> str:
    orientation = (getattr(candidate.view, "orientation", "vertical")
                   if candidate.view is not None else "vertical")
    regions = ", ".join(
        f"{h.region} (header row {h.header_row}, span {h.header_span})"
        for h in candidate.handles)
    cov, errors, gaps = score
    return (f"[{i}] method={candidate.method!r} orientation={orientation} "
            f"tables=[{regions}] coverage={cov} errors={errors} gaps={gaps} "
            f"confidence={candidate.confidence}")


def _arbiter_seed(ranked_topk, sheet: str) -> str:
    lines = [
        f"Sheet {sheet!r} has {len(ranked_topk)} competing layout interpretations "
        "(deterministic scoring could not separate them):",
    ]
    lines += [_describe(i, c, s) for i, (c, s) in enumerate(ranked_topk)]
    lines += [
        "Regions of a 'transposed' interpretation are in TRANSPOSED coordinates "
        "(rows and columns swapped relative to the raw sheet you inspect).",
        "Inspect the sheet with `dimensions`, `peek_rows`, and `peek_region`, then "
        "call `finalize` with the index (`choice`) of the best interpretation.",
    ]
    return "\n".join(lines)
