import openpyxl
from dataclasses import replace
from mcg_swarm.orchestrator import orchestrate_table
from mcg_swarm.splitter import split_workbook, TableHandle
from mcg_swarm.schemas import ColumnSpec
from mcg_swarm.llm.client import FakeLLMClient


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
