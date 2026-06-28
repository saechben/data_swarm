# tests/test_gate_source.py
"""Task 4 TDD tests: run_table_tests and BandView accept WorkbookSource."""
import openpyxl
from mcg_swarm.source import OpenpyxlFileSource
from mcg_swarm.extraction import build_index
from mcg_swarm.quality_gate import run_table_tests
from mcg_swarm.subagent.tools import BandView
from mcg_swarm.size_estimate import Band
from mcg_swarm.schemas import CanonicalTable, ColumnSpec, ExtractionRef
from mcg_swarm.splitter import TableHandle


def _src(tmp_path):
    p = tmp_path / "g.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "D"
    ws.append(["Key", "Val"]); ws.append(["a", 1]); ws.append(["b", 2]); wb.save(p)
    return OpenpyxlFileSource(str(p))


def test_gate_runs_via_source(tmp_path):
    src = _src(tmp_path)
    handle = TableHandle(sheet="D", region="A1:B3", header_row=1,
        columns=[ColumnSpec(name="Key", dtype="string", role="key"),
                 ColumnSpec(name="Val", dtype="number")], header_span=1)
    idx = build_index(src, handle, row_key=["Key"])
    table = CanonicalTable(table_id="t", sheet="D", region="A1:B3", header_row=1,
        columns=handle.columns, extraction=ExtractionRef(script_name="t", row_key=["Key"]))
    assert run_table_tests(src, table, idx).passed


def test_bandview_via_source(tmp_path):
    src = _src(tmp_path)
    band = Band(sheet="D", header_row=1, region="A1:B3", col_start=1, col_end=2,
                row_start=2, row_end=3)
    view = BandView(src, band)
    assert view.geometry()["sheet"] == "D"
