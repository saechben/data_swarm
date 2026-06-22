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

def test_query_range_reads_all_cells(tmp_path):
    # 3x2 table: header + 2 data rows, 2 value columns
    p = _wb(tmp_path, [["X", "Y"], [10, 20], [30, 40], [50, 60]])
    idx = build_index(p, split_workbook(p)[0], row_key=["X"])
    # query_range over A2:B4 (the 3 data rows, both columns)
    results = idx.query_range("A2:B4")
    assert len(results) == 6
    by_ref = {v.cell_ref: v.value for v in results}
    assert by_ref["A2"] == 10
    assert by_ref["B2"] == 20
    assert by_ref["A3"] == 30
    assert by_ref["B3"] == 40
    assert by_ref["A4"] == 50
    assert by_ref["B4"] == 60
    # all provenance fields set correctly
    assert all(v.sheet == "Data" for v in results)
    assert all(v.dtype == "number" for v in results)
    assert all(v.unit is None for v in results)
    assert all(v.is_computed is False for v in results)


def test_coverage_helpers(tmp_path):
    p = _wb(tmp_path, [["Region", "Revenue"], ["EMEA", 100], ["APAC", 200]])
    idx = build_index(p, split_workbook(p)[0], row_key=["Region"])
    assert set(idx.row_keys()) == {"EMEA", "APAC"}
    assert idx.column_names() == ["Region", "Revenue"]
