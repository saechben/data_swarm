"""The data that crosses the Subagent port.

`BandTask` is what the orchestrator hands to `Subagent.analyze`. It carries the band
geometry plus the structural signals the splitter already computed on the parent
`TableHandle` (column roles, header span, ambiguity) so a verifier can use them without
re-deriving anything. The legacy `analyze_band` shim builds a minimal task with no
handle signals (`handle_columns=None`), which can never trigger escalation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from mcg_swarm.schemas import ColumnSpec, SegmentReport
from mcg_swarm.size_estimate import Band


@dataclass
class BandTask:
    path: str
    band: Band
    header: list[str]                                  # bare column names (static analysis input)
    handle_columns: Optional[list[ColumnSpec]] = None  # splitter specs w/ roles (digest + disagreement)
    header_span: int = 1
    ambiguous: bool = False
    reason: Optional[str] = None
    table_region: Optional[str] = None


def role_disagreements(static_columns, handle_columns) -> list[dict]:
    """Columns where the static-inferred role differs from the splitter's role.

    Returns [{name, static_role, handle_role}, ...]. Empty when handle_columns is None
    (legacy shim) — there is nothing to compare against.
    """
    if not handle_columns:
        return []
    handle_by_name = {c.name: c for c in handle_columns}
    out = []
    for col in static_columns:
        hc = handle_by_name.get(col.name)
        if hc is not None and hc.role != col.role:
            out.append({"name": col.name, "static_role": col.role, "handle_role": hc.role})
    return out


@dataclass
class BandDigest:
    """Deterministic seed handed to the ReAct verifier as its starting context."""
    geometry: dict
    header_span: int
    ambiguous: bool
    reason: Optional[str]
    static_columns: list[dict]                 # name/dtype/role/unit as inferred by static
    role_disagreements: list[dict] = field(default_factory=list)
    static_anomalies: list[str] = field(default_factory=list)

    def to_prompt(self) -> str:
        """Render a compact, human/agent-readable seed string."""
        lines = [
            "You are verifying one table band's column metadata produced by a fast "
            "deterministic pass. Use the read-only tools to inspect the actual cells, "
            "then call the finalize tool with any corrections (only changed columns).",
            "",
            f"Geometry: {json.dumps(self.geometry)}",
            f"header_span={self.header_span}  ambiguous={self.ambiguous}"
            + (f"  reason={self.reason!r}" if self.reason else ""),
            "Static columns: " + json.dumps(self.static_columns),
        ]
        if self.role_disagreements:
            lines.append("Role disagreements (splitter vs static): "
                         + json.dumps(self.role_disagreements))
        if self.static_anomalies:
            lines.append("Static anomalies: " + json.dumps(self.static_anomalies))
        return "\n".join(lines)


def build_digest(task: BandTask, static_report: SegmentReport) -> BandDigest:
    """Assemble the digest from forwarded handle signals ⊕ static-derived signals."""
    b = task.band
    static_columns = [
        {"name": c.name, "dtype": c.dtype, "role": c.role, "unit": c.unit}
        for c in static_report.columns
    ]
    return BandDigest(
        geometry={
            "sheet": b.sheet, "region": task.table_region or b.region,
            "header_row": b.header_row,
            "n_data_rows": b.row_end - b.row_start + 1,
        },
        header_span=task.header_span,
        ambiguous=task.ambiguous,
        reason=task.reason,
        static_columns=static_columns,
        role_disagreements=role_disagreements(static_report.columns, task.handle_columns),
        static_anomalies=list(static_report.anomalies),
    )
