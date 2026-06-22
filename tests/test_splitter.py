import openpyxl, pytest
from mcg_swarm.splitter import split_workbook, TableHandle

def _wb(tmp_path, rows, name="t.xlsx"):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Data"
    for r in rows: ws.append(r)
    p = tmp_path / name; wb.save(p); return str(p)

def test_clean_table_handle(tmp_path):
    p = _wb(tmp_path, [["Region", "Revenue", "Units"],
                       ["EMEA", 100, 5], ["APAC", 200, 9]])
    handles = split_workbook(p)
    assert len(handles) == 1
    h = handles[0]
    assert h.sheet == "Data" and h.header_row == 1 and not h.ambiguous
    assert h.region == "A1:C3"
    assert [c.name for c in h.columns] == ["Region", "Revenue", "Units"]
    assert h.columns[0].role == "key"
    assert h.columns[1].dtype == "number" and h.columns[0].dtype == "string"

def test_title_banner_offset_is_detected_not_guessed(tmp_path):
    p = _wb(tmp_path, [["Q3 Sales Report"], [],
                       ["Region", "Revenue"], ["EMEA", 100]])
    h = split_workbook(p)[0]
    # deterministic detector may resolve the offset; if it can't, it must flag, not guess
    assert h.header_row == 3 or h.ambiguous

def test_empty_sheet_is_ambiguous(tmp_path):
    p = _wb(tmp_path, [])
    h = split_workbook(p)[0]
    assert h.ambiguous


def test_merged_title_banner_above_header(tmp_path):
    """Row 1 has ONE non-empty cell (title banner), row 2 is the real multi-column header."""
    # This mimics a merged title cell spanning A1:C1 — openpyxl reads the rest as None
    p = _wb(tmp_path, [
        ["Title", None, None],        # row 1: single-cell title banner (like merged A1:C1)
        ["Region", "Q1", "Q2"],       # row 2: real header
        ["North", 100, 200],          # row 3: data
        ["South", 300, 400],          # row 4: data
    ])
    h = split_workbook(p)[0]
    assert h.header_row == 2, f"Expected header_row=2, got {h.header_row}"
    assert not h.ambiguous, f"Should not be ambiguous, got reason={h.reason!r}"
    assert [c.name for c in h.columns] == ["Region", "Q1", "Q2"], \
        f"Expected 3 columns, got {[c.name for c in h.columns]}"
    assert "B" not in h.region and h.region.startswith("A2"), \
        f"Region should start at A2, got {h.region!r}"


def test_left_offset_table_trims_empty_leading_column(tmp_path):
    """Column A is entirely empty; table lives in B:C starting row 2."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    # Row 1: entirely empty
    ws.append([None, None, None])
    # Row 2: header in B:C (A is empty)
    ws.cell(row=2, column=2, value="Name")
    ws.cell(row=2, column=3, value="Val")
    # Row 3: data
    ws.cell(row=3, column=2, value="Alice")
    ws.cell(row=3, column=3, value=42)
    # Row 4: data
    ws.cell(row=4, column=2, value="Bob")
    ws.cell(row=4, column=3, value=99)
    p = tmp_path / "offset.xlsx"
    wb.save(p)
    h = split_workbook(str(p))[0]
    assert not h.ambiguous, f"Should not be ambiguous, got reason={h.reason!r}"
    assert h.region.startswith("B"), f"Region should start at B, got {h.region!r}"
    col_names = [c.name for c in h.columns]
    assert col_names == ["Name", "Val"], f"Expected ['Name','Val'], got {col_names}"
    assert "A" not in col_names, f"Phantom 'A' column should not appear"
    assert h.columns[0].role == "key", "First real column should be key"
