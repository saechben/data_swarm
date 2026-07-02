"""analyze_workbook: rich per-sheet contract, never-raise, stub fallback."""
import pytest

from mcg_swarm.analyzers.pipeline import analyze_workbook, analyze_sheet
from mcg_swarm.analyzers.base import SheetAnalysis
from mcg_swarm.analyzers.registry import register, build_analyzers
from mcg_swarm.config import SwarmConfig
from mcg_swarm.splitter import split_workbook, detect_table
from tests.test_views import _GridSource

_SHEETS = {
    "Sales": [("Region", "Sales"), ("North", 10), ("South", 20)],
    "Costs": [("Dept", "Cost"), ("Eng", 100), ("Ops", 50)],
}


def test_analyze_workbook_default_vertical():
    out = analyze_workbook(_GridSource(_SHEETS))
    assert [sa.sheet for sa in out] == ["Sales", "Costs"]
    for sa in out:
        assert isinstance(sa, SheetAnalysis)
        assert sa.method == "vertical" and sa.view is None
        assert sa.handles == (detect_table(_SHEETS[sa.sheet], sa.sheet),)
        assert sa.findings == ()


def test_split_workbook_shim_still_flat_handles():
    src = _GridSource(_SHEETS)
    assert split_workbook(src) == [detect_table(g, n) for n, g in _SHEETS.items()]


class _RaisingLens:
    name = "raising"
    def analyze(self, grid, sheet):
        raise RuntimeError("boom")


class _EmptyLens:
    name = "empty"
    def analyze(self, grid, sheet):
        return []


def test_lens_exception_becomes_finding_not_crash():
    register("raising", _RaisingLens)
    analyzers = build_analyzers(("vertical", "raising"))
    sa = analyze_sheet(analyzers, _SHEETS["Sales"], "Sales")
    assert sa.method == "vertical"                       # vertical still wins
    cats = [(f.category, f.severity) for f in sa.findings]
    assert ("analyzer-error", "warning") in cats
    msg = next(f.message for f in sa.findings if f.category == "analyzer-error")
    assert "raising" in msg and "boom" in msg


def test_all_lenses_empty_falls_back_to_stub():
    register("empty", _EmptyLens)
    sa = analyze_sheet(build_analyzers(("empty",)), _SHEETS["Sales"], "Sales")
    assert sa.method == "fallback"
    assert len(sa.handles) == 1
    stub = sa.handles[0]
    assert stub.ambiguous and stub.region == "A1:A1"
    assert stub.reason == "no analyzer produced a candidate"
