"""Deterministic coverage/residue detection over a full sheet grid."""
from mcg_swarm.coverage import nonempty_cells, region_cells, coverage_score, scan_handle
from mcg_swarm.splitter import TableHandle


def _cats(findings):
    return sorted(f.category for f in findings)


def test_nonempty_and_region_cells():
    grid = [("a", None), (None, "b")]
    assert nonempty_cells(grid) == {(1, 1), (2, 2)}
    assert region_cells("A1:B2") == {(1, 1), (1, 2), (2, 1), (2, 2)}


def test_coverage_score():
    grid = [("a", "b"), ("c", None)]
    assert coverage_score(grid, ["A1:A2"]) == 2  # a, c covered; b not


def test_uncovered_data_stacked_table():
    # First table A1:C2 captured; a second header+data block sits below at rows 4-5.
    grid = [
        ("Region", "Rev", "Units"),
        ("NA", 1, 2),
        (None, None, None),
        ("Product", "Price", "SKU"),
        ("Widget", 9, "W1"),
    ]
    h = TableHandle("S", "A1:C2", 1)
    cats = _cats(scan_handle(grid, h, "S"))
    assert "uncovered-data" in cats


def test_uncovered_data_side_by_side():
    grid = [
        ("Region", "Rev", None, "Product", "Price"),
        ("NA", 1, None, "Widget", 9),
    ]
    h = TableHandle("S", "A1:B2", 1)
    cats = _cats(scan_handle(grid, h, "S"))
    assert "uncovered-data" in cats


def test_empty_header_corner():
    grid = [("", "Q1", "Q2"), ("Revenue", 1, 2)]
    h = TableHandle("S", "A1:C2", 1)
    cats = _cats(scan_handle(grid, h, "S"))
    assert "empty-header-corner" in cats


def test_false_header_span_value_like_leaf():
    grid = [("Item", "Price", "Margin"), ("X", "$1,200", "15%"), ("Y", "$3,400", "22%")]
    h = TableHandle("S", "A1:C3", 1, header_span=2)
    cats = _cats(scan_handle(grid, h, "S"))
    assert "false-header-span" in cats


def test_clean_table_no_findings():
    grid = [("Region", "Rev"), ("NA", 1), ("EU", 2)]
    h = TableHandle("S", "A1:B3", 1)
    assert scan_handle(grid, h, "S") == []
