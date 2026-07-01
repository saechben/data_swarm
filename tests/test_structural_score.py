# tests/test_structural_score.py
from mcg_swarm.subagent.structural import ProposedTable, SheetRecutPatch, score_handles
from mcg_swarm.splitter import handle_from_region
from tests.fake_source import FakeSource


def _stacked():
    # two stacked tables: rows 1-3 and rows 5-6, one blank row between
    v = {(1, 1): "Region", (1, 2): "Revenue",
         (2, 1): "EMEA", (2, 2): 100,
         (3, 1): "APAC", (3, 2): 200,
         (5, 1): "Product", (5, 2): "Price",
         (6, 1): "Widget", (6, 2): 49}
    return FakeSource("Data", v, {})


def test_schema_defaults():
    p = SheetRecutPatch(tables=[ProposedTable(region="A1:B3", header_row=1)])
    assert p.tables[0].orientation == "vertical"
    assert p.tables[0].header_span == 1


def test_split_covers_more_than_single_region():
    src = _stacked()
    grid = src.read_region("Data")
    baseline = handle_from_region(grid, "Data", "A1:B3", header_row=1)
    split = [handle_from_region(grid, "Data", "A1:B3", header_row=1),
             handle_from_region(grid, "Data", "A5:B6", header_row=5)]
    base_cov, base_err, base_gap = score_handles(src, grid, [baseline], "Data")
    cand_cov, cand_err, cand_gap = score_handles(src, grid, split, "Data")
    # the second table's cells are only covered by the split
    assert cand_cov > base_cov
    # splitting must not manufacture new errors or interior gaps
    assert cand_err <= base_err
    assert cand_gap <= base_gap        # two tight regions, no interior blank rows/cols


def test_bad_split_does_not_beat_baseline():
    src = _stacked()
    grid = src.read_region("Data")
    baseline = handle_from_region(grid, "Data", "A1:B3", header_row=1)
    # a "re-cut" that is just the same single region → no coverage gain
    same = [handle_from_region(grid, "Data", "A1:B3", header_row=1)]
    base = score_handles(src, grid, [baseline], "Data")
    cand = score_handles(src, grid, same, "Data")
    assert not (cand[0] > base[0] and cand[1] <= base[1] and cand[2] <= base[2])


def test_overclaiming_region_is_penalised():
    # the degenerate proposal: one giant region swallowing the blank separator
    # row 4 AND the lower table. It covers more non-empty cells and drops the
    # uncovered-data residue error — but it fuses two tables, so it has an
    # interior blank-row gap the tight baseline does not.
    src = _stacked()
    grid = src.read_region("Data")
    tight = [handle_from_region(grid, "Data", "A1:B3", header_row=1)]
    giant = [handle_from_region(grid, "Data", "A1:B6", header_row=1)]
    t = score_handles(src, grid, tight, "Data")
    g = score_handles(src, grid, giant, "Data")
    assert g[0] > t[0]                 # greedy region covers more non-empty cells...
    assert g[2] > t[2]                 # ...but introduces an interior blank-row gap
    # so it must NOT satisfy the three-way strict-better acceptance rule
    assert not (g[0] > t[0] and g[1] <= t[1] and g[2] <= t[2])
