"""ReActVerifier — a smart second pass that confirms or corrects static analysis.

Given a `BandTask` and the static `SegmentReport`, it builds a digest seed and a
read-only `BandView` toolset, lets an `AgentRunner` drive the agent, and applies the
returned patch to the static report. It is a *verifier*: it never produces a result
from scratch and never raises — any agent failure returns the static report unchanged.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from mcg_swarm.schemas import SegmentReport
from mcg_swarm.source import as_source
from mcg_swarm.subagent.agent_runner import AgentRunner
from mcg_swarm.subagent.task import BandTask, build_digest
from mcg_swarm.subagent.tools import BandView, build_band_toolset

_VALID_DTYPES = ("number", "string", "boolean", "date")
_VALID_ROLES = ("key", "value", "computed")


class _ColumnPatch(BaseModel):
    name: str
    dtype: Optional[str] = None
    unit: Optional[str] = None
    role: Optional[str] = None


class SegmentReportPatch(BaseModel):
    """What the agent's `finalize` call returns: per-column corrections + notes."""
    columns: list[_ColumnPatch] = []
    anomalies: list[str] = []


def apply_column_patch(columns: list, patch: dict) -> list:
    """Return a new column list with the patch's dtype/unit/role corrections applied.

    Matches by column name; ignores unknown columns and invalid enum values. Shared by
    the band-level verifier (SegmentReport) and the table-level check (CanonicalTable).
    """
    by_name = {c["name"]: c for c in patch.get("columns", [])}
    new_cols = []
    for col in columns:
        p = by_name.get(col.name)
        if p:
            updates = {}
            if p.get("dtype") in _VALID_DTYPES:
                updates["dtype"] = p["dtype"]
            if p.get("unit") is not None:
                updates["unit"] = p["unit"]
            if p.get("role") in _VALID_ROLES:
                updates["role"] = p["role"]
            if updates:
                col = col.model_copy(update=updates)
        new_cols.append(col)
    return new_cols


def _apply_patch(report: SegmentReport, patch: dict) -> SegmentReport:
    new_cols = apply_column_patch(report.columns, patch)
    anomalies = list(report.anomalies) + list(patch.get("anomalies", []))
    return report.model_copy(update={"columns": new_cols, "anomalies": anomalies})


class ReActVerifier:
    """Drives an AgentRunner over a band's toolset to verify/correct the static report."""

    def __init__(self, runner: AgentRunner) -> None:
        self._runner = runner

    def verify(self, task: BandTask, static_report: SegmentReport) -> SegmentReport:
        try:
            view = BandView(task.source if task.source is not None else as_source(task.path), task.band)
            tools = build_band_toolset(view)
            seed = build_digest(task, static_report).to_prompt()
            patch = self._runner.run(seed, tools, schema=SegmentReportPatch)
            return _apply_patch(static_report, patch)
        except Exception:
            # Verifier never breaks the pipeline — keep the deterministic result.
            return static_report
