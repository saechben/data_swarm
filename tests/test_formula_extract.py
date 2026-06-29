import openpyxl
from mcg_swarm.source import OpenpyxlFileSource


def _write_vertical_formula_wb(path):
    """3-col table: Units | Price | Revenue(=A*B per row). Header row 1, data rows 2-4."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Units", "Price", "Revenue"])
    for r in range(2, 5):
        ws.cell(row=r, column=1, value=r)           # Units
        ws.cell(row=r, column=2, value=10)          # Price
        ws.cell(row=r, column=3, value=f"=A{r}*B{r}")  # Revenue formula
    wb.save(path)


def test_read_formula_region_returns_formula_strings(tmp_path):
    p = tmp_path / "vf.xlsx"
    _write_vertical_formula_wb(str(p))
    src = OpenpyxlFileSource(str(p))
    rows = src.read_formula_region("Sheet1", 2, 1, 4, 3)
    # row 2 (first data row): Units=2, Price=10, Revenue="=A2*B2"
    assert rows[0][2] == "=A2*B2"
    assert rows[2][2] == "=A4*B4"


def test_read_formula_region_empty_cells_no_crash(tmp_path):
    """Regression for the old EmptyCell cell.coordinate crash (commit b77195b)."""
    p = tmp_path / "empty.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["A", "B", "C"])
    ws.append([1, None, None])   # row 2 has empty cells
    wb.save(str(p))
    src = OpenpyxlFileSource(str(p))
    rows = src.read_formula_region("Sheet1", 1, 1, 2, 3)  # must not raise
    assert rows[1][0] == 1 and rows[1][1] is None


from fake_source import FakeSource, vertical_fake


def test_index_geometry_accessors():
    from mcg_swarm.splitter import split_workbook
    from mcg_swarm.extraction import build_index
    src = vertical_fake()
    handle = split_workbook(src)[0]
    index = build_index(src, handle, row_key=[])
    cols = index.physical_columns()
    assert cols["Units"] == 1 and cols["Price"] == 2 and cols["Revenue"] == 3
    assert index.data_row_numbers() == [2, 3, 4]
