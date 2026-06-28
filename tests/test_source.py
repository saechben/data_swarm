import openpyxl
from mcg_swarm.source import OpenpyxlFileSource, as_source, WorkbookSource

def _wb(tmp_path):
    p = tmp_path / "s.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "S"
    ws.append(["A", "B"]); ws.append([1, 2]); ws.append([3, 4])
    wb.save(p); return str(p)

def test_sheet_names_region_cell(tmp_path):
    src = OpenpyxlFileSource(_wb(tmp_path))
    assert src.sheet_names() == ["S"]
    assert src.read_region("S", 1, 1, 3, 2) == [("A", "B"), (1, 2), (3, 4)]
    assert src.read_region("S") == [("A", "B"), (1, 2), (3, 4)]  # unbounded = whole sheet
    assert src.read_cell("S", 2, 2) == 2

def test_as_source_normalizes(tmp_path):
    p = _wb(tmp_path)
    assert isinstance(as_source(p), OpenpyxlFileSource)
    assert isinstance(as_source({"main": p}), OpenpyxlFileSource)
    s = OpenpyxlFileSource(p)
    assert as_source(s) is s
    assert isinstance(s, WorkbookSource)  # runtime_checkable
