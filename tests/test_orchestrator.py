import openpyxl
from mcg_swarm.orchestrator import orchestrate_table
from mcg_swarm.splitter import split_workbook


def _wb(tmp_path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    for r in rows:
        ws.append(r)
    p = tmp_path / "t.xlsx"
    wb.save(p)
    return str(p)


def test_clean_table_returns_passing_canonical(tmp_path):
    p = _wb(tmp_path, [["Region", "Revenue"], ["EMEA", 100], ["APAC", 200]])
    h = split_workbook(p)[0]
    t = orchestrate_table(p, h, table_id="t1", llm=None)
    assert t.errors == [] and t.region == "A1:B3"
    assert t.extraction.row_key == ["Region"]


def test_ambiguous_handle_yields_error_stub_not_exception(tmp_path):
    p = _wb(tmp_path, [])  # empty -> ambiguous
    h = split_workbook(p)[0]
    t = orchestrate_table(p, h, table_id="t1", llm=None)
    assert t.errors and "messy" in t.errors[0].lower()
