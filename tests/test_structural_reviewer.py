# tests/test_structural_reviewer.py
from mcg_swarm.coverage import scan_handle
from mcg_swarm.splitter import handle_from_region
from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from mcg_swarm.subagent.structural import StructuralReviewer
from tests.fake_source import FakeSource


def _stacked():
    v = {(1, 1): "Region", (1, 2): "Revenue",
         (2, 1): "EMEA", (2, 2): 100,
         (3, 1): "APAC", (3, 2): 200,
         (5, 1): "Product", (5, 2): "Price",
         (6, 1): "Widget", (6, 2): 49}
    return FakeSource("Data", v, {})


def _setup():
    src = _stacked()
    grid = src.read_region("Data")
    handle = handle_from_region(grid, "Data", "A1:B3", header_row=1)
    scan = scan_handle(grid, handle, "Data")   # contains an uncovered-data finding
    assert any(f.category == "uncovered-data" for f in scan)
    return src, grid, handle, scan


def test_good_split_is_accepted_and_findings_marked_fixed():
    src, grid, handle, scan = _setup()
    runner = FakeAgentRunner(actions=[], final={"tables": [
        {"region": "A1:B3", "header_row": 1},
        {"region": "A5:B6", "header_row": 5}]})
    review = StructuralReviewer(runner).review(src, handle, grid, scan)
    assert len(review.handles) == 2
    assert {h.region for h in review.handles} == {"A1:B3", "A5:B6"}
    fixed = [f for f in review.sheet_findings if f.category == "uncovered-data"]
    assert fixed and all(f.resolution == "fixed" for f in fixed)
    assert all(f.agent_action for f in fixed)


def test_bad_split_is_rejected_baseline_kept():
    src, grid, handle, scan = _setup()
    # proposal that drops the lower table (no coverage gain) → must be rejected
    runner = FakeAgentRunner(actions=[], final={"tables": [
        {"region": "A1:B3", "header_row": 1}]})
    review = StructuralReviewer(runner).review(src, handle, grid, scan)
    assert [h.region for h in review.handles] == ["A1:B3"]
    rej = [f for f in review.sheet_findings if f.category == "uncovered-data"]
    assert rej and all(f.resolution == "rejected" for f in rej)


def test_empty_proposal_is_no_op_open():
    src, grid, handle, scan = _setup()
    runner = FakeAgentRunner(actions=[], final={"tables": []})
    review = StructuralReviewer(runner).review(src, handle, grid, scan)
    assert [h.region for h in review.handles] == ["A1:B3"]
    open_f = [f for f in review.sheet_findings if f.category == "uncovered-data"]
    assert open_f and all(f.resolution == "open" for f in open_f)


def test_transposed_proposal_not_built():
    src, grid, handle, scan = _setup()
    runner = FakeAgentRunner(actions=[], final={"tables": [
        {"region": "A1:B6", "header_row": 1, "orientation": "transposed"}]})
    review = StructuralReviewer(runner).review(src, handle, grid, scan)
    # nothing vertical to build → baseline kept
    assert [h.region for h in review.handles] == ["A1:B3"]


def test_overclaiming_recut_rejected():
    # a single giant vertical region that swallows the blank row + lower table:
    # more coverage, but an interior gap → fails the three-way gate, baseline kept.
    src, grid, handle, scan = _setup()
    runner = FakeAgentRunner(actions=[], final={"tables": [
        {"region": "A1:B6", "header_row": 1}]})
    review = StructuralReviewer(runner).review(src, handle, grid, scan)
    assert [h.region for h in review.handles] == ["A1:B3"]
    assert review.recut is False
    rej = [f for f in review.sheet_findings if f.category == "uncovered-data"]
    assert rej and all(f.resolution == "rejected" for f in rej)


def test_agent_failure_falls_back_to_baseline():
    src, grid, handle, scan = _setup()

    class Boom:
        def run(self, *a, **k):
            raise RuntimeError("sdk down")

    review = StructuralReviewer(Boom()).review(src, handle, grid, scan)
    assert [h.region for h in review.handles] == ["A1:B3"]
    # detection survives the failure
    assert any(f.category == "uncovered-data" for f in review.sheet_findings)
