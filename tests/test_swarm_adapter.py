"""TDD test for SwarmAdapter — Task 13.

Runs the real swarm on sales_regional.xlsx (easy workbook) and asserts the
adapter's extract() returns the correct value for the first extraction sample.
"""
from pathlib import Path

from eval.adapters.swarm_adapter import SwarmAdapter
from eval.harness.runner import load_labels
from eval.util import values_match


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
