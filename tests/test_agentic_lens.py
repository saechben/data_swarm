"""Pure-agentic layout lens: propose STRUCTURE, verify deterministically."""
from mcg_swarm.analyzers.agentic import (
    AgenticLensPolicy, SheetLayoutPatch, _build_agentic_toolset, _materialize,
    _score_proposal,
)
from tests.test_views import _GridSource

_HORIZONTAL = {"S": [("Region", "North", "South"), ("Sales", 10, 20)]}
_STACKED = {"S": [("Region", "Sales"), ("North", 10), ("South", 20),
                  (None, None),
                  ("Dept", "Cost"), ("Eng", 100), ("Ops", 50)]}
_POLICY = AgenticLensPolicy()


def test_materialize_transposed_proposal():
    src = _GridSource(_HORIZONTAL)
    patch = SheetLayoutPatch(tables=[{"region": "A1:B3", "header_row": 1,
                                      "orientation": "transposed"}])
    out = _materialize(patch, src.read_region("S"), "S", src, _POLICY)
    assert len(out) == 1
    c = out[0]
    assert c.method == "agentic" and c.confidence == 0.7
    assert type(c.view).__name__ == "TransposedView"
    assert c.handles[0].region == "A1:B3"          # view coordinates
    assert [col.name for col in c.handles[0].columns] == ["Region", "Sales"]


def test_materialize_mixed_orientation_keeps_vertical_subset():
    src = _GridSource(_STACKED)
    patch = SheetLayoutPatch(tables=[
        {"region": "A1:B3", "header_row": 1, "orientation": "vertical"},
        {"region": "A5:B7", "header_row": 5, "orientation": "transposed"}])
    out = _materialize(patch, src.read_region("S"), "S", src, _POLICY)
    assert len(out) == 1 and len(out[0].handles) == 1
    assert out[0].handles[0].region == "A1:B3" and out[0].view is None
    assert any(f.category == "agentic-lens" and f.severity == "warning"
               for f in out[0].findings)


def test_materialize_malformed_region_skipped_with_finding():
    src = _GridSource(_STACKED)
    patch = SheetLayoutPatch(tables=[
        {"region": "NOT-A-RANGE", "header_row": 1},
        {"region": "A1:B3", "header_row": 1}])
    out = _materialize(patch, src.read_region("S"), "S", src, _POLICY)
    assert len(out) == 1 and len(out[0].handles) == 1
    assert any("NOT-A-RANGE" in f.message for f in out[0].findings)


def test_score_proposal_returns_deterministic_metrics():
    src = _GridSource(_STACKED)
    res = _score_proposal(src, src.read_region("S"), "S",
                          [{"region": "A1:B3", "header_row": 1},
                           {"region": "A5:B7", "header_row": 5}], _POLICY)
    assert res["ok"] is True and res["tables"] == 2
    assert res["coverage_cells"] == 12 and res["errors"] == 0
    bad = _score_proposal(src, src.read_region("S"), "S",
                          [{"region": "zzz"}], _POLICY)
    assert bad["ok"] is False


def test_try_layout_tool_enforces_probe_budget():
    src = _GridSource(_STACKED)
    counter = {"probes": 0}
    tools = _build_agentic_toolset(src, src.read_region("S"), "S",
                                   AgenticLensPolicy(max_probe_iterations=1),
                                   counter)
    try_layout = next(t for t in tools if t.name == "try_layout")
    ok = try_layout.handler({"tables": [{"region": "A1:B3", "header_row": 1}]})
    assert ok["ok"] is True
    blocked = try_layout.handler({"tables": [{"region": "A1:B3", "header_row": 1}]})
    assert blocked["ok"] is False and "budget" in blocked["error"]
    assert {t.name for t in tools} >= {"dimensions", "peek_rows", "peek_region",
                                       "try_layout"}
