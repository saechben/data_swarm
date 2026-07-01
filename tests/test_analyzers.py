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
