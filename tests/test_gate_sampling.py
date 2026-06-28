# tests/test_gate_sampling.py
"""
TDD tests for Task 6: spread sampling + dtype-conformance gate check.

RED requirement: test_late_row_dtype_drift_is_caught must FAIL before the fix
(contiguous keys[:25] never reads rows 21-59; no conformance phase exists).
GREEN after: spread sampling reads across the full range; conformance phase
catches the dtype drift and appends a dtype-mismatch failure.
"""
import openpyxl
from mcg_swarm.source import OpenpyxlFileSource
from mcg_swarm.extraction import build_index
from mcg_swarm.quality_gate import run_table_tests
from mcg_swarm.schemas import CanonicalTable, ColumnSpec, ExtractionRef
from mcg_swarm.splitter import TableHandle


def _drift_wb(tmp_path):
    """
    Workbook with 59 data rows:
    - rows 1-20: Days column has integers (valid numbers)
    - rows 21-59: Days column has text strings like "pending" (dtype drift)
    With contiguous keys[:25], the gate only reads rows 1-25 (mix of good+bad).
    But with the OLD keys[:25] approach, only 20 good + 5 text => 5/25 = 20% which
    is right at the tolerance boundary. To ensure RED is clear, we use rows 1-5 good
    and rows 6-59 bad — a big majority of drift rows are sampled even in contiguous mode
    BUT the conformance phase doesn't exist yet, so RED is purely about missing phase.
    """
    p = tmp_path / "drift.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "T"
    ws.append(["Id", "Days"])
    for i in range(1, 21):
        ws.append([f"r{i:02d}", i])         # rows 2-21: numeric Days
    for i in range(21, 60):
        ws.append([f"r{i:02d}", "pending"])  # rows 22-60: text Days (dtype drift)
    wb.save(p)
    return OpenpyxlFileSource(str(p))


def _table(handle):
    return CanonicalTable(
        table_id="t",
        sheet="T",
        region="A1:B59",
        header_row=1,
        columns=handle.columns,
        extraction=ExtractionRef(script_name="t", row_key=["Id"]),
    )


def test_late_row_dtype_drift_is_caught(tmp_path):
    """
    A 'number' column that has text values in late rows must produce a dtype-mismatch failure.

    RED: Before fix — old contiguous keys[:25] samples only rows 1-25 (first 20 are
    numeric, next 5 are text => 5/25 = 20% which is right at tolerance). More importantly,
    NO conformance phase exists at all, so the table wrongly passes regardless of sampling.

    GREEN: After fix — spread sampling reads across all 59 rows (including the bulk of
    text rows), AND the dtype-conformance phase flags the high fraction of bad cells.
    """
    src = _drift_wb(tmp_path)
    handle = TableHandle(
        sheet="T",
        region="A1:B59",
        header_row=1,
        columns=[
            ColumnSpec(name="Id", dtype="string", role="key"),
            ColumnSpec(name="Days", dtype="number"),  # declared number, but late rows are text
        ],
        header_span=1,
    )
    idx = build_index(src, handle, row_key=["Id"])
    rep = run_table_tests(src, _table(handle), idx)
    assert not rep.passed, f"Expected gate to FAIL for dtype drift, but it passed. failures={rep.failures}"
    assert any(f.startswith("dtype-mismatch") for f in rep.failures), (
        f"Expected a 'dtype-mismatch:' failure, got: {rep.failures}"
    )


def test_correct_dtype_passes(tmp_path):
    """
    Declaring the drifting column as 'string' (its real nature) must NOT fail conformance.
    String columns are exempt from conformance checks by design.
    """
    src = _drift_wb(tmp_path)
    handle = TableHandle(
        sheet="T",
        region="A1:B59",
        header_row=1,
        columns=[
            ColumnSpec(name="Id", dtype="string", role="key"),
            ColumnSpec(name="Days", dtype="string"),  # correct dtype for the mixed content
        ],
        header_span=1,
    )
    idx = build_index(src, handle, row_key=["Id"])
    assert run_table_tests(src, _table(handle), idx).passed


def test_sparse_sentinels_tolerated(tmp_path):
    """
    A number column with only 2/50 'n/a' sentinels (4% < 20% tolerance) must pass.
    The mismatch fraction is below DTYPE_MISMATCH_TOL=0.2, so gate stays green.
    """
    p = tmp_path / "ok.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "T"
    ws.append(["Id", "Days"])
    for i in range(1, 51):
        val = "n/a" if i in (10, 30) else i  # 2 sentinels out of 50
        ws.append([f"r{i:02d}", val])
    wb.save(p)

    src = OpenpyxlFileSource(str(p))
    handle = TableHandle(
        sheet="T",
        region="A1:B51",
        header_row=1,
        columns=[
            ColumnSpec(name="Id", dtype="string", role="key"),
            ColumnSpec(name="Days", dtype="number"),
        ],
        header_span=1,
    )
    table = CanonicalTable(
        table_id="t",
        sheet="T",
        region="A1:B51",
        header_row=1,
        columns=handle.columns,
        extraction=ExtractionRef(script_name="t", row_key=["Id"]),
    )
    idx = build_index(src, handle, row_key=["Id"])
    rep = run_table_tests(src, table, idx)
    assert rep.passed, (
        f"Expected gate to PASS for sparse sentinels (2/50 = 4% < 20% tolerance), "
        f"but got failures: {rep.failures}"
    )
