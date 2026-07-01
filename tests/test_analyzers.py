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
