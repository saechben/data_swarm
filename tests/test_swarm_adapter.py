"""TDD test for SwarmAdapter — Task 13.

Runs the real swarm on sales_regional.xlsx (easy workbook) and asserts the
adapter's extract() returns the correct value for the first extraction sample.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import openpyxl
import pytest

from eval.adapters.swarm_adapter import MEASURE_ROW_CAP, SwarmAdapter
from eval.adapters.base import DetectedMeasure
from eval.harness.runner import load_labels
from eval.util import values_match
from mcg_swarm.schemas import ColumnSpec


def test_swarm_adapter_extracts_real_value():
    labels = {l.workbook: l for l in load_labels(Path("eval/data/labels"))}
    label = labels["sales_regional.xlsx"]
    a = SwarmAdapter()
    a.prepare("eval/data/workbooks/sales_regional.xlsx", label)
    # pick the first extraction sample from the label and verify the adapter returns its value
    s = next(s for s in label.samples if s.type == "extraction")
    got = a.extract(label.workbook, s.table_id, s.table, s.sheet, s.row_label, s.col_label)
    assert values_match(s.expected_value, got, s.tolerance, s.dtype), (
        f"expected {s.expected_value!r}, got {got!r} "
        f"(row={s.row_label!r}, col={s.col_label!r})"
    )


# ---- detected_measures unit tests (no real workbook needed) ----

def _make_index_stub(tmp_path, rows, col_specs):
    """Build a real ExtractionIndex with stubbed columns from a tiny xlsx."""
    from mcg_swarm.splitter import split_workbook
    from mcg_swarm.extraction import build_index

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for r in rows:
        ws.append(r)
    p = tmp_path / "stub.xlsx"
    wb.save(str(p))

    from mcg_swarm.splitter import split_workbook
    h = split_workbook(str(p))[0]
    idx = build_index(str(p), h, row_key=[rows[0][0]])  # first col is key
    # Override columns with our test specs
    idx.columns = {spec.name: spec for spec in col_specs}
    return str(p), idx


def test_detected_measures_numeric_only(tmp_path):
    """detected_measures only emits numeric value columns, not key or non-numeric."""
    from mcg_swarm.extraction import build_index
    from mcg_swarm.splitter import split_workbook

    _, idx = _make_index_stub(
        tmp_path,
        [["Region", "Revenue", "Quarter"],
         ["EMEA", 100, "Q1"],
         ["APAC", 200, "Q2"]],
        [
            ColumnSpec(name="Region", dtype="string", role="key"),
            ColumnSpec(name="Revenue", dtype="number", role="value"),
            ColumnSpec(name="Quarter", dtype="string", role="value"),
        ]
    )

    a = SwarmAdapter()
    a._indices["test.xlsx"] = {"t1": idx}
    measures = a.detected_measures("test.xlsx")

    col_labels = {m.col_label for m in measures}
    # Should emit Revenue (numeric value) but NOT Region (key) or Quarter (string value)
    assert "Revenue" in col_labels, "numeric value col must be emitted"
    assert "Region" not in col_labels, "key col must be skipped"
    assert "Quarter" not in col_labels, "string value col must be skipped"
    assert all(m.table_id == "t1" for m in measures)


def test_detected_measures_memoized(tmp_path):
    """detected_measures returns same object on repeated calls (memoized)."""
    _, idx = _make_index_stub(
        tmp_path,
        [["Region", "Revenue"], ["EMEA", 100]],
        [
            ColumnSpec(name="Region", dtype="string", role="key"),
            ColumnSpec(name="Revenue", dtype="number", role="value"),
        ]
    )
    a = SwarmAdapter()
    a._indices["test.xlsx"] = {"t1": idx}

    first = a.detected_measures("test.xlsx")
    second = a.detected_measures("test.xlsx")
    assert first is second, "second call should return cached list (same object)"


def test_detected_measures_row_cap(tmp_path, capsys):
    """Row cap limits emission and prints warning when triggered."""
    from mcg_swarm.extraction import build_index
    from mcg_swarm.splitter import split_workbook

    # Build a table with MEASURE_ROW_CAP + 5 data rows
    n = MEASURE_ROW_CAP + 5
    data_rows = [[f"R{i}", i * 10] for i in range(1, n + 1)]
    all_rows = [["Key", "Value"]] + data_rows

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for r in all_rows:
        ws.append(r)
    p = tmp_path / "big.xlsx"
    wb.save(str(p))

    h = split_workbook(str(p))[0]
    idx = build_index(str(p), h, row_key=["Key"])
    idx.columns = {
        "Key": ColumnSpec(name="Key", dtype="string", role="key"),
        "Value": ColumnSpec(name="Value", dtype="number", role="value"),
    }

    a = SwarmAdapter()
    a._indices["big.xlsx"] = {"tbl": idx}
    measures = a.detected_measures("big.xlsx")

    # Only MEASURE_ROW_CAP rows emitted (one value col each)
    assert len(measures) == MEASURE_ROW_CAP

    # Warning printed
    captured = capsys.readouterr()
    assert "[swarm_adapter]" in captured.out
    assert str(MEASURE_ROW_CAP) in captured.out


def test_detected_measures_skips_none(tmp_path):
    """detected_measures skips cells with None value."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Region", "Revenue"])
    ws.append(["EMEA", None])
    ws.append(["APAC", 200])
    p = tmp_path / "none_test.xlsx"
    wb.save(str(p))

    from mcg_swarm.splitter import split_workbook
    from mcg_swarm.extraction import build_index
    h = split_workbook(str(p))[0]
    idx = build_index(str(p), h, row_key=["Region"])
    idx.columns = {
        "Region": ColumnSpec(name="Region", dtype="string", role="key"),
        "Revenue": ColumnSpec(name="Revenue", dtype="number", role="value"),
    }

    a = SwarmAdapter()
    a._indices["none_test.xlsx"] = {"t1": idx}
    measures = a.detected_measures("none_test.xlsx")

    row_labels = {m.row_label for m in measures}
    assert "APAC" in row_labels
    assert "EMEA" not in row_labels  # None skipped
