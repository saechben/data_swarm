from mcg_swarm.splitter import handle_from_region

GRID = [
    ("Region", "Revenue", "Units"),   # row 1
    ("EMEA", 100, 5),                  # row 2
    ("APAC", 200, 9),                  # row 3
    (None, None, None),               # row 4 (gap)
    ("Product", "Price", None),       # row 5
    ("Widget", 49, None),             # row 6
]


def test_builds_handle_for_top_block():
    h = handle_from_region(GRID, "Data", "A1:C3", header_row=1)
    assert h.sheet == "Data"
    assert h.region == "A1:C3"
    assert h.header_row == 1
    assert h.header_span == 1
    assert [c.name for c in h.columns] == ["Region", "Revenue", "Units"]
    assert h.columns[0].role == "key"
    assert h.columns[1].role == "value"
    assert h.columns[1].dtype == "number"    # 100, 200
    assert h.columns[0].dtype == "string"    # EMEA, APAC


def test_builds_handle_for_offset_block():
    # a second table lower on the sheet, honoured at its real coordinates
    h = handle_from_region(GRID, "Data", "A5:B6", header_row=5)
    assert [c.name for c in h.columns] == ["Product", "Price"]
    assert h.columns[1].dtype == "number"    # 49


def test_two_row_header_span():
    grid = [
        ("Group", "H1", "H1"),        # row 1 (group header)
        ("Region", "Q1", "Q2"),       # row 2 (leaf header)
        ("EMEA", 1, 2),               # row 3
    ]
    h = handle_from_region(grid, "Data", "A1:C3", header_row=1, header_span=2)
    # bottom-first composite naming: leaf row wins where present
    assert [c.name for c in h.columns] == ["Region", "Q1", "Q2"]
    assert h.header_span == 2
