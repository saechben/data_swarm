# tests/test_extraction.py
import openpyxl, pytest
from mcg_swarm.splitter import split_workbook
from mcg_swarm.extraction import build_index

def _wb(tmp_path, rows):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Data"
    for r in rows: ws.append(r)
    p = tmp_path / "t.xlsx"; wb.save(p); return str(p)

def test_query_by_key_and_column(tmp_path):
    p = _wb(tmp_path, [["Region", "Revenue"], ["EMEA", 100], ["APAC", 200]])
    h = split_workbook(p)[0]
    idx = build_index(p, h, row_key=["Region"])
    v = idx.query("APAC", "Revenue")
    assert v.value == 200 and v.cell_ref == "B3" and v.sheet == "Data" and v.dtype == "number"

def test_unknown_key_and_column_raise(tmp_path):
    p = _wb(tmp_path, [["Region", "Revenue"], ["EMEA", 100]])
    idx = build_index(p, split_workbook(p)[0], row_key=["Region"])
    with pytest.raises(KeyError): idx.query("NOPE", "Revenue")
    with pytest.raises(KeyError): idx.query("EMEA", "NoCol")

def test_live_read_reflects_edits(tmp_path):
    p = _wb(tmp_path, [["Region", "Revenue"], ["EMEA", 100]])
    idx = build_index(p, split_workbook(p)[0], row_key=["Region"])
    assert idx.query("EMEA", "Revenue").value == 100
    wb = openpyxl.load_workbook(p); wb["Data"]["B2"] = 999; wb.save(p)
    assert idx.query("EMEA", "Revenue").value == 999  # no rebuild

def test_coverage_helpers(tmp_path):
    p = _wb(tmp_path, [["Region", "Revenue"], ["EMEA", 100], ["APAC", 200]])
    idx = build_index(p, split_workbook(p)[0], row_key=["Region"])
    assert set(idx.row_keys()) == {"EMEA", "APAC"}
    assert idx.column_names() == ["Region", "Revenue"]
