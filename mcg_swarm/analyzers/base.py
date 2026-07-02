from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from mcg_swarm.schemas import Finding
from mcg_swarm.splitter import TableHandle


@dataclass(frozen=True)
class LayoutCandidate:
    """One analyzer's interpretation of a whole sheet.

    handles:    one or more tables (Phase A: exactly one, from detect_table).
    coverage:   fraction of the sheet's non-empty cells claimed by the handles
                (0.0-1.0; spec §4.2).
    findings:   excluded regions / warnings (Phase A: empty).
    confidence: analyzer self-report; advisory tie-breaker for the assessor.
    view:       normalizing WorkbookSource wrapper (e.g. TransposedView) whose
                coordinates the handles are expressed in; None = identity.
    """

    method: str
    handles: tuple[TableHandle, ...]
    coverage: float = 0.0
    findings: tuple[Finding, ...] = ()
    confidence: float = 1.0
    view: Any = None


@dataclass(frozen=True)
class SheetAnalysis:
    """The assessed result for one sheet — the analyze→orchestrate contract.

    handles:  winning candidate's tables, in view coordinates.
    view:     normalizing WorkbookSource wrapper (None = identity) — downstream
              must read through `view or source`.
    method:   which analyzer won ("fallback" = no candidate; ambiguous stub).
    findings: lens failures + winning candidate's findings (sheet scope).
    """

    sheet: str
    handles: tuple[TableHandle, ...]
    view: Any = None
    method: str = "vertical"
    findings: tuple[Finding, ...] = ()


@runtime_checkable
class SheetAnalyzer(Protocol):
    name: str

    def analyze(self, grid: list[tuple], sheet: str) -> list[LayoutCandidate]:
        ...
