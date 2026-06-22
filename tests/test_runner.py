import openpyxl
from mcg_swarm.runner import run_swarm, build_indices


def _wb_two_tabs(tmp_path):
    wb = openpyxl.Workbook()
    ws1 = wb.active; ws1.title = "Sales"
    for r in [["Region", "Revenue"], ["EMEA", 100], ["APAC", 200]]: ws1.append(r)
    ws2 = wb.create_sheet("Empty")  # ambiguous tab
    p = tmp_path / "multi.xlsx"; wb.save(p); return str(p)


def test_run_swarm_one_table_per_tab_bad_tab_isolated(tmp_path):
    p = _wb_two_tabs(tmp_path)
    ext = run_swarm({"main": p})
    assert ext.workbook.endswith("multi.xlsx")
    assert len(ext.tables) == 2
    sales = [t for t in ext.tables if t.sheet == "Sales"][0]
    empty = [t for t in ext.tables if t.sheet == "Empty"][0]
    assert sales.errors == [] and empty.errors  # bad tab isolated, file still processed


def test_build_indices_round_trips(tmp_path):
    p = _wb_two_tabs(tmp_path)
    ext = run_swarm({"main": p})
    idxs = build_indices(p, ext)
    sales = [t for t in ext.tables if t.sheet == "Sales"][0]
    assert idxs[sales.table_id].query("APAC", "Revenue").value == 200
