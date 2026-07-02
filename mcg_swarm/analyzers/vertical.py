from __future__ import annotations

from mcg_swarm.analyzers.base import LayoutCandidate
from mcg_swarm.coverage import coverage_score, nonempty_cells
from mcg_swarm.splitter import detect_table


class VerticalSplitAnalyzer:
    """The baseline lens: one clean vertical table per sheet.

    Wraps the unchanged detect_table so a single-candidate assessment reproduces
    today's behavior byte-for-byte. This is the neutrality anchor for Phase A.
    """

    name = "vertical"

    def analyze(self, grid: list[tuple], sheet: str) -> list[LayoutCandidate]:
        handle = detect_table(grid, sheet)
        total = len(nonempty_cells(grid))
        covered = coverage_score(grid, [handle.region])
        coverage = covered / total if total else 0.0
        return [LayoutCandidate(method="vertical", handles=(handle,), coverage=coverage)]
