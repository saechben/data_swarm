from mcg_swarm.analyzers.base import LayoutCandidate, SheetAnalyzer
from mcg_swarm.splitter import TableHandle


def test_layout_candidate_defaults():
    h = TableHandle("Sheet1", "A1:B3", 1)
    c = LayoutCandidate(method="vertical", handles=(h,))
    assert c.method == "vertical"
    assert c.handles == (h,)
    assert c.coverage == 0
    assert c.findings == ()
    assert c.confidence == 1.0


def test_layout_candidate_is_frozen():
    import dataclasses
    c = LayoutCandidate(method="x", handles=())
    try:
        c.method = "y"
        assert False, "expected FrozenInstanceError"
    except dataclasses.FrozenInstanceError:
        pass


class _Dummy:
    name = "dummy"
    def analyze(self, grid, sheet):
        return []


def test_sheet_analyzer_protocol_runtime_check():
    assert isinstance(_Dummy(), SheetAnalyzer)
    assert not isinstance(object(), SheetAnalyzer)


# Task 2: VerticalSplitAnalyzer tests
from mcg_swarm.analyzers.vertical import VerticalSplitAnalyzer
from mcg_swarm.splitter import detect_table
from mcg_swarm.coverage import coverage_score

_GRID = [("Region", "Sales"), ("North", 10), ("South", 20)]


def test_vertical_analyzer_wraps_detect_table():
    a = VerticalSplitAnalyzer()
    cands = a.analyze(_GRID, "Sheet1")
    assert len(cands) == 1
    c = cands[0]
    assert c.method == "vertical"
    assert len(c.handles) == 1
    assert c.handles[0] == detect_table(_GRID, "Sheet1")


def test_vertical_analyzer_sets_coverage():
    a = VerticalSplitAnalyzer()
    c = a.analyze(_GRID, "Sheet1")[0]
    assert c.coverage == coverage_score(_GRID, [c.handles[0].region])
    assert c.coverage > 0


def test_vertical_analyzer_name_attr():
    assert VerticalSplitAnalyzer().name == "vertical"


# Task 3: Registry + SwarmConfig.analyzers tests
import pytest
from mcg_swarm.analyzers.registry import register, build_analyzers
from mcg_swarm.config import SwarmConfig


def test_build_default_analyzer_set():
    analyzers = build_analyzers(("vertical",))
    assert len(analyzers) == 1
    assert analyzers[0].name == "vertical"


def test_build_analyzers_unknown_name_raises():
    with pytest.raises(KeyError):
        build_analyzers(("does_not_exist",))


def test_register_and_build_custom():
    class _Fake:
        name = "fake"
        def analyze(self, grid, sheet):
            return []
    register("fake", _Fake)
    built = build_analyzers(("vertical", "fake"))
    assert [a.name for a in built] == ["vertical", "fake"]


def test_swarmconfig_has_default_analyzers():
    assert SwarmConfig().analyzers == ("vertical",)


# Task 4: Deterministic assessor tests
from mcg_swarm.analyzers.assess import assess


def _cand(method, region, coverage, confidence=1.0):
    h = TableHandle("S", region, 1)
    return LayoutCandidate(method=method, handles=(h,),
                           coverage=coverage, confidence=confidence)


def test_assess_single_candidate_passthrough():
    c = _cand("vertical", "A1:B3", 6)
    assert assess([c]) is c  # same object — byte-identical downstream


def test_assess_empty_raises():
    with pytest.raises(ValueError):
        assess([])


def test_assess_picks_higher_coverage():
    lo = _cand("vertical", "A1:B3", 6)
    hi = _cand("multitable", "A1:C9", 20)
    assert assess([lo, hi]) is hi


def test_assess_dedups_same_region_by_confidence():
    weak = _cand("a", "A1:B3", 6, confidence=0.4)
    strong = _cand("b", "A1:B3", 6, confidence=0.9)
    # identical region signature → collapse to the higher-confidence one
    assert assess([weak, strong]) is strong


def test_assess_coverage_beats_confidence():
    # coverage is the primary key; a lower-confidence but higher-coverage wins
    big = _cand("a", "A1:C9", 20, confidence=0.5)
    small = _cand("b", "A1:B3", 6, confidence=1.0)
    assert assess([big, small]) is big
