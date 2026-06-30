"""Guarantee: cases static gets wrong are detected, never silently corrupted."""
import pytest
from mcg_swarm.runner import run_swarm
from tests.fixtures import nasty_workbooks as nw


def _all_error_categories(ext):
    cats = {f.category for f in ext.findings if f.severity == "error"}
    for t in ext.tables:
        cats |= {f.category for f in t.findings if f.severity == "error"}
    return cats


@pytest.mark.parametrize("builder,expected", [
    (nw.two_stacked, "uncovered-data"),
    (nw.side_by_side, "uncovered-data"),
    (nw.preamble_rows, "uncovered-data"),
    (nw.transposed, "empty-header-corner"),
])
def test_detected_not_silent(builder, expected, tmp_path):
    ext = run_swarm(builder(str(tmp_path / "wb.xlsx")))
    assert expected in _all_error_categories(ext), \
        f"{builder.__name__}: expected {expected} error finding, got none (silent corruption)"
