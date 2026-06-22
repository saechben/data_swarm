import openpyxl
from dataclasses import replace
from mcg_swarm.orchestrator import orchestrate_table
from mcg_swarm.splitter import split_workbook, TableHandle
from mcg_swarm.schemas import ColumnSpec
from mcg_swarm.llm.client import FakeLLMClient
from mcg_swarm.size_estimate import COLS_PER_AGENT


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


def test_ambiguous_handle_with_confident_llm_yields_passing_table(tmp_path):
    """
    Integration: ambiguous handle + confident FakeLLM → orchestrate_table resolves
    and produces errors==[] with the correct region.

    Approach: build a workbook with a clean table at rows 2-4 (header row 2).
    Artificially force ambiguous=True on the handle (simulating a messy-tab splitter
    result). Provide a FakeLLM that returns the exact correct region/header_row so
    the resolved handle round-trips cleanly through build_index and run_table_tests.
    """
    # Clean 2-row data table, header at row 1 (A1:B3 after save)
    p = _wb(tmp_path, [["Region", "Revenue"], ["EMEA", 100], ["APAC", 200]])
    h = split_workbook(p)[0]
    # Artificially mark ambiguous (simulates splitter uncertainty on a messy sheet)
    h_ambiguous = replace(h, ambiguous=True, reason="test-forced ambiguous")

    fake = FakeLLMClient(responses=[{
        "confident": True,
        "header_row": 1,
        "region": "A1:B3",
        "columns": [
            {"name": "Region", "dtype": "string"},
            {"name": "Revenue", "dtype": "number"},
        ],
    }])

    t = orchestrate_table(p, h_ambiguous, table_id="t1", llm=fake)
    assert t.errors == [], f"Expected no errors, got: {t.errors}"
    assert t.region == "A1:B3"
    assert t.extraction.row_key == ["Region"]


def _wide_wb(tmp_path, n_cols: int, n_rows: int = 3):
    """Build a workbook with n_cols columns wide enough to force col-axis fan-out."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Wide"
    headers = [f"Col{i:03d}" for i in range(n_cols)]
    ws.append(headers)
    for r in range(n_rows):
        ws.append([r * n_cols + c for c in range(n_cols)])
    p = tmp_path / "wide.xlsx"
    wb.save(p)
    return str(p)


def test_col_axis_band_gets_only_its_columns(tmp_path):
    """
    Fix 1: per-band header slice for col-axis fan-out.

    Build a table wide enough (> COLS_PER_AGENT) so plan_bands returns axis="col"
    with multiple bands.  Before the fix, every band receives the full header and
    _deterministic_columns iterates all N names against its narrow grid;
    merge_reports(axis="col") then concatenates → duplicated column names.
    After the fix each band receives only its own column-name slice →
    merged.columns has UNIQUE names equal to the full ordered set.
    """
    n_cols = COLS_PER_AGENT + 10  # forces col-axis split (col_pressure > 1)
    p = _wide_wb(tmp_path, n_cols=n_cols, n_rows=3)
    h = split_workbook(p)[0]

    t = orchestrate_table(p, h, table_id="wide1", llm=None)

    # Must not error (the test workbook is clean)
    assert t.errors == [], f"Unexpected errors: {t.errors}"

    col_names = [c.name for c in t.columns]

    # No duplicates
    assert len(col_names) == len(set(col_names)), (
        f"Duplicate column names after col-axis merge: {col_names}"
    )

    # Correct count matches what the workbook actually has
    assert len(col_names) == n_cols, (
        f"Expected {n_cols} columns, got {len(col_names)}: {col_names[:5]}..."
    )

    # Correct order (Col000, Col001, ..., Col{n_cols-1})
    expected = [f"Col{i:03d}" for i in range(n_cols)]
    assert col_names == expected, (
        f"Column order mismatch. First diff at index "
        f"{next(i for i,(a,b) in enumerate(zip(col_names,expected)) if a!=b)}"
    )
