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


# ---------------------------------------------------------------------------
# Three-table fixture (two proposed regions, one genuine leftover)
# ---------------------------------------------------------------------------

def _three_tables():
    """Three stacked tables separated by blank rows.

    Table 1  A1:B3  (rows 1-3)
    Table 2  A5:B7  (rows 5-7)
    Table 3  A9:B10 (rows 9-10)
    """
    v = {
        (1, 1): "Region",   (1, 2): "Revenue",
        (2, 1): "EMEA",     (2, 2): 100,
        (3, 1): "APAC",     (3, 2): 200,
        # row 4 blank
        (5, 1): "Product",  (5, 2): "Price",
        (6, 1): "Widget",   (6, 2): 49,
        (7, 1): "Gadget",   (7, 2): 99,
        # row 8 blank
        (9, 1):  "Category", (9, 2):  "Count",
        (10, 1): "Alpha",    (10, 2): 5,
    }
    return FakeSource("Data", v, {})


def _setup_three():
    src = _three_tables()
    grid = src.read_region("Data")
    handle = handle_from_region(grid, "Data", "A1:B3", header_row=1)
    scan = scan_handle(grid, handle, "Data")
    # baseline must detect BOTH table 2 and table 3 as uncovered
    uncovered_findings = [f for f in scan if f.category == "uncovered-data"]
    assert len(uncovered_findings) >= 2, (
        f"expected >=2 uncovered-data findings, got {len(uncovered_findings)}: "
        f"{[f.ref for f in uncovered_findings]}"
    )
    return src, grid, handle, scan


def test_accepted_recut_preserves_genuine_leftover():
    """Agent proposes 2 of 3 tables; the 3rd genuine leftover must survive as open.

    Fixture reasoning:
    - Proposal [A1:B3, A5:B7] passes the three-way gate:
        coverage 6 → 12 (strictly more), table-scope errors 0→0, gaps 0→0.
    - A9:B10 is NOT covered by either proposed region, so every per-handle scan
      emits an uncovered-data finding for it.  The union-coverage test correctly
      classifies it as a genuine leftover (block_cells ⊄ union_covered) rather
      than a cross-handle artifact, so it survives in sheet_findings with
      resolution == "open", preserving the detection-never-lost invariant.
    """
    src, grid, handle, scan = _setup_three()
    runner = FakeAgentRunner(actions=[], final={"tables": [
        {"region": "A1:B3", "header_row": 1},
        {"region": "A5:B7", "header_row": 5}]})
    review = StructuralReviewer(runner).review(src, handle, grid, scan)

    assert review.recut is True, "partial-coverage proposal that improves coverage must be accepted"

    open_leftovers = [f for f in review.sheet_findings
                      if f.category == "uncovered-data" and f.resolution == "open"]
    assert open_leftovers, (
        "genuine leftover uncovered-data (A9:B10) must appear in sheet_findings "
        f"as open; sheet_findings={[(f.category, f.ref, f.resolution) for f in review.sheet_findings]}"
    )
    refs = {f.ref for f in open_leftovers}
    assert any("A9" in r for r in refs), (
        f"open leftover must reference the A9:B10 block; got refs={refs}"
    )

    # Lock the partial-coverage fix: no ref may appear with BOTH fixed and open.
    fixed_refs = {f.ref for f in review.sheet_findings if f.resolution == "fixed"}
    open_refs = {f.ref for f in review.sheet_findings if f.resolution == "open"}
    overlap = fixed_refs & open_refs
    assert not overlap, (
        f"same ref cannot have both fixed and open resolution; overlap={overlap}"
    )


def test_non_last_handle_residual_captured():
    """Genuine sheet-scope residual from a non-last handle must reach sheet_findings.

    This guards Finding 1 (loop-scope bug): if residual.extend/append is placed
    OUTSIDE the for-loop, only the LAST handle's scan populates residual, silently
    dropping findings from all earlier handles.

    In this fixture A9:B10 is a genuine leftover visible to BOTH the first handle
    (A1:B3) and the second handle (A5:B7) in their per-handle scans.  The fix moves
    the accumulation inside the loop; deduplication then emits it exactly once.
    Checking that the finding appears validates the loop-scope fix's output.
    """
    src, grid, handle, scan = _setup_three()
    runner = FakeAgentRunner(actions=[], final={"tables": [
        {"region": "A1:B3", "header_row": 1},
        {"region": "A5:B7", "header_row": 5}]})
    review = StructuralReviewer(runner).review(src, handle, grid, scan)

    open_leftovers = [f for f in review.sheet_findings
                      if f.category == "uncovered-data" and f.resolution == "open"]
    assert open_leftovers, (
        "residual from non-last handle must not be lost; "
        f"sheet_findings={[(f.category, f.ref, f.resolution) for f in review.sheet_findings]}"
    )
    # exactly one open leftover after deduplication (both handles see A9:B10)
    assert len(open_leftovers) == 1, (
        f"deduplication must yield exactly 1 open leftover; got {len(open_leftovers)}"
    )
