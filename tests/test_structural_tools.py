from mcg_swarm.subagent.structural_tools import SheetView, build_sheet_toolset
from tests.fake_source import FakeSource


def _stacked():
    # table 1 rows 1-3, gap row 4, table 2 rows 5-6
    v = {(1, 1): "Region", (1, 2): "Revenue",
         (2, 1): "EMEA", (2, 2): 100,
         (3, 1): "APAC", (3, 2): 200,
         (5, 1): "Product", (5, 2): "Price",
         (6, 1): "Widget", (6, 2): 49}
    return FakeSource("Data", v, {})


def test_dimensions_spans_whole_sheet():
    view = SheetView(_stacked(), "Data")
    d = view.dimensions()
    assert d["sheet"] == "Data"
    assert d["n_rows"] >= 6
    assert d["n_cols"] >= 2


def test_peek_region_reads_lower_block():
    view = SheetView(_stacked(), "Data")
    rows = view.peek_region("A5:B6")
    assert rows[0]["row"] == 5
    assert rows[0]["cells"][0] == "Product"
    assert rows[1]["cells"][1] == 49


def test_toolset_names_and_dispatch():
    view = SheetView(_stacked(), "Data")
    tools = {t.name: t for t in build_sheet_toolset(view)}
    assert set(tools) == {"dimensions", "peek_rows", "peek_region"}
    out = tools["peek_rows"].handler({"start_row": 1, "count": 2})
    assert out["rows"][0]["cells"][0] == "Region"
