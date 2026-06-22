# tests/test_testing.py
import openpyxl
from mcg_swarm.testing import run_table_tests, TableTestReport
from mcg_swarm.splitter import split_workbook
from mcg_swarm.extraction import build_index
from mcg_swarm.schemas import CanonicalTable, ExtractionRef


def _wb(tmp_path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    for r in rows:
        ws.append(r)
    p = tmp_path / "t.xlsx"
    wb.save(p)
    return str(p)


def _canon(h):
    return CanonicalTable(
        table_id="t",
        sheet=h.sheet,
        region=h.region,
        header_row=h.header_row,
        columns=h.columns,
        description="d",
        extraction=ExtractionRef(script_name="idx", row_key=[h.columns[0].name]),
    )


def test_passes_on_clean_table(tmp_path):
    p = _wb(tmp_path, [["Region", "Revenue"], ["EMEA", 100], ["APAC", 200]])
    h = split_workbook(p)[0]
    idx = build_index(p, h, row_key=["Region"])
    rep = run_table_tests(p, _canon(h), idx)
    assert rep.passed and rep.failures == []


def test_detects_roundtrip_mismatch(tmp_path):
    """Column-integrity (and round-trip) must catch a string-col-into-number-col remap."""
    p = _wb(tmp_path, [["Region", "Revenue"], ["EMEA", 100]])
    h = split_workbook(p)[0]
    idx = build_index(p, h, row_key=["Region"])
    # corrupt: Revenue now points to Region's physical column
    idx._col_to_phys["Revenue"] = idx._col_to_phys["Region"]
    rep = run_table_tests(p, _canon(h), idx)
    # column-integrity catches it: Revenue col in index != Revenue col in live header
    assert not rep.passed and rep.failures


def test_detects_numeric_column_swap(tmp_path):
    """Regression guard: numeric->numeric column remap must be caught by column-integrity.

    A Revenue/Units swap produces self-consistent cell_ref+value in the index,
    so the round-trip alone cannot detect it.  The column-integrity check reads
    the live header independently and must flag the mismatch.
    """
    p = _wb(tmp_path, [["Region", "Revenue", "Units"], ["EMEA", 100, 5], ["APAC", 200, 8]])
    h = split_workbook(p)[0]
    idx = build_index(p, h, row_key=["Region"])
    # swap Revenue and Units — both numeric so round-trip cannot distinguish them
    idx._col_to_phys["Revenue"], idx._col_to_phys["Units"] = (
        idx._col_to_phys["Units"],
        idx._col_to_phys["Revenue"],
    )
    rep = run_table_tests(p, _canon(h), idx)
    assert not rep.passed, f"Expected failure but got no failures: {rep.failures}"
    assert any("column-integrity" in f for f in rep.failures), (
        f"Expected column-integrity failure, got: {rep.failures}"
    )


def test_report_dataclass_defaults():
    """TableTestReport: passed=True, failures defaults to empty list."""
    rep = TableTestReport(passed=True)
    assert rep.failures == []
    rep2 = TableTestReport(passed=False, failures=["x"])
    assert not rep2.passed


def test_passes_single_row(tmp_path):
    """Boundary: single data row, sample covers it."""
    p = _wb(tmp_path, [["ID", "Score"], [1, 99]])
    h = split_workbook(p)[0]
    idx = build_index(p, h, row_key=["ID"])
    rep = run_table_tests(p, _canon(h), idx, sample_size=1)
    assert rep.passed


def test_row_integrity_detects_key_remap(tmp_path):
    """Row-integrity check must catch a row remap: _key_to_phys["Alice"] -> wrong row.

    If _key_to_phys["Alice"] points to row 3 (Bob's row), the index resolves "Alice"
    to the wrong physical row.  The row-integrity check reads the key-column cell at
    that physical row and finds "Bob" != "Alice", flagging a failure.
    """
    p = _wb(tmp_path, [["Name", "Val"], ["Alice", 10], ["Bob", 20]])
    h = split_workbook(p)[0]
    idx = build_index(p, h, row_key=["Name"])
    # Remap Alice to Bob's physical row
    idx._key_to_phys["Alice"] = idx._key_to_phys["Bob"]
    rep = run_table_tests(p, _canon(h), idx)
    assert not rep.passed, "Expected failure when key maps to wrong physical row"
    assert any("row-integrity" in f for f in rep.failures), (
        f"Expected row-integrity failure, got: {rep.failures}"
    )
