"""TDD test: ExtractionIndex reads via WorkbookSource (Task 3)."""
import openpyxl
from mcg_swarm.source import OpenpyxlFileSource
from mcg_swarm.extraction import build_index
from mcg_swarm.schemas import ColumnSpec
from mcg_swarm.splitter import TableHandle


def test_build_index_reads_via_source(tmp_path):
    p = tmp_path / "e.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "D"
    ws.append(["Key", "Val"]); ws.append(["a", 1]); ws.append(["b", 2]); wb.save(p)
    src = OpenpyxlFileSource(str(p))
    handle = TableHandle(sheet="D", region="A1:B3", header_row=1,
                         columns=[ColumnSpec(name="Key", dtype="string", role="key"),
                                  ColumnSpec(name="Val", dtype="number")], header_span=1)
    idx = build_index(src, handle, row_key=["Key"])
    assert idx.query("a", "Val").value == 1
    assert idx.column_names() == ["Key", "Val"]
