"""The data that crosses the Subagent port.

`BandTask` is what the orchestrator hands to `Subagent.analyze`. It carries the band
geometry plus the structural signals the splitter already computed on the parent
`TableHandle` (column roles, header span, ambiguity) so a verifier can use them without
re-deriving anything. The legacy `analyze_band` shim builds a minimal task with no
handle signals (`handle_columns=None`), which can never trigger escalation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from mcg_swarm.schemas import ColumnSpec
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
