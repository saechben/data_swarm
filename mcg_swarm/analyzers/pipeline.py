"""Per-sheet analysis pipeline: run the active lenses, assess, return rich results.

Never raises (spec §5): a lens exception becomes a warning Finding; zero
candidates becomes an ambiguous stub handle (today's no-header behavior).
"""
from __future__ import annotations

from mcg_swarm.analyzers.assess import assess
from mcg_swarm.analyzers.base import LayoutCandidate, SheetAnalysis
from mcg_swarm.analyzers.registry import build_analyzers
from mcg_swarm.config import SwarmConfig
from mcg_swarm.schemas import Finding
from mcg_swarm.source import as_source
from mcg_swarm.splitter import TableHandle


def _fallback_candidate(sheet: str) -> LayoutCandidate:
    stub = TableHandle(sheet, "A1:A1", 1, [], ambiguous=True,
                       reason="no analyzer produced a candidate")
    return LayoutCandidate(method="fallback", handles=(stub,))


def analyze_sheet(analyzers, grid: list[tuple], sheet: str, source=None) -> SheetAnalysis:
    candidates: list[LayoutCandidate] = []
    findings: list[Finding] = []
    for a in analyzers:
        try:
            candidates.extend(a.analyze(grid, sheet, source=source))
        except Exception as e:  # lens failure is a finding, never a crash (spec §5)
            findings.append(Finding(
                category="analyzer-error", severity="warning", scope="sheet",
                message=f"analyzer {a.name!r} failed: {e}", source="static",
                ref=f"{sheet}!A1"))
    if candidates:
        try:
            winner = assess(candidates)
        except Exception as e:  # malformed candidate from a lens — degrade, don't crash
            findings.append(Finding(
                category="analyzer-error", severity="warning", scope="sheet",
                message=f"assessment failed: {e}", source="static",
                ref=f"{sheet}!A1"))
            winner = _fallback_candidate(sheet)
    else:
        winner = _fallback_candidate(sheet)
    return SheetAnalysis(sheet=sheet, handles=winner.handles, view=winner.view,
                         method=winner.method,
                         findings=tuple(findings) + winner.findings)


def analyze_workbook(source, config: SwarmConfig | None = None) -> list[SheetAnalysis]:
    """Run the active analyzer lenses over every sheet. The rich counterpart of
    split_workbook — surfaces view/method/findings per sheet (spec §4.6)."""
    if config is None:
        config = SwarmConfig()
    src = as_source(source)
    analyzers = build_analyzers(config.analyzers)
    return [analyze_sheet(analyzers, src.read_region(name), name, source=src)
            for name in src.sheet_names()]
