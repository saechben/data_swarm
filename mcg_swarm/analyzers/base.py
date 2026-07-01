from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from mcg_swarm.schemas import Finding
from mcg_swarm.splitter import TableHandle


@dataclass(frozen=True)
class LayoutCandidate:
    """One analyzer's interpretation of a whole sheet.

    handles:    one or more tables (Phase A: exactly one, from detect_table).
    coverage:   non-empty cells claimed by the handles (deterministic score input).
    findings:   excluded regions / warnings (Phase A: empty).
    confidence: analyzer self-report; advisory tie-breaker for the assessor.
    """

    method: str
    handles: tuple[TableHandle, ...]
    coverage: int = 0
    findings: tuple[Finding, ...] = ()
    confidence: float = 1.0


@runtime_checkable
class SheetAnalyzer(Protocol):
    name: str

    def analyze(self, grid: list[tuple], sheet: str) -> list[LayoutCandidate]:
        ...
