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
