# tests/test_gate_query_path.py
"""The quality gate's value validation must flow through the REAL query() function —
the same entry point downstream agents use — not a side-channel batch read.

RED before the fix: dtype-conformance reads `live_cache` directly, so query() is only
invoked by the 5-key round-trip spot check. GREEN after: dtype-conformance reads every
sampled non-string cell via index.query(), so query() is exercised end-to-end across the
whole sample.
"""
import openpyxl
from mcg_swarm.source import OpenpyxlFileSource
from mcg_swarm.extraction import build_index
from mcg_swarm.quality_gate import run_table_tests
from mcg_swarm.schemas import CanonicalTable, ColumnSpec, ExtractionRef
from mcg_swarm.splitter import TableHandle


def _clean_wb(tmp_path, n=30):
    """n data rows, Id (string key) + Amount (all-valid numbers). Gate passes."""
    p = tmp_path / "clean.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "T"
    ws.append(["Id", "Amount"])
    for i in range(1, n + 1):
        ws.append([f"r{i:02d}", i * 10])
    wb.save(p)
    return OpenpyxlFileSource(str(p)), n


def _handle(n):
    return TableHandle(
        sheet="T",
        region=f"A1:B{n + 1}",
        header_row=1,
        columns=[
            ColumnSpec(name="Id", dtype="string", role="key"),
            ColumnSpec(name="Amount", dtype="number"),
        ],
        header_span=1,
    )


def _table(handle, n):
    return CanonicalTable(
        table_id="t",
        sheet="T",
        region=f"A1:B{n + 1}",
        header_row=1,
        columns=handle.columns,
        extraction=ExtractionRef(script_name="t", row_key=["Id"]),
    )


def test_dtype_validation_goes_through_query(tmp_path):
    """Every sampled non-string cell must be read via index.query() during the gate.

    A small table (<= full_threshold) is fully sampled, so query() must be called for
    ALL row keys on the 'Amount' column — not just the first 5 (round-trip subsample).
    """
    src, n = _clean_wb(tmp_path)
    handle = _handle(n)
    idx = build_index(src, handle, row_key=["Id"])

    seen: list[tuple] = []
    orig_query = idx.query

    def spy(row, column):
        seen.append((row, column))
        return orig_query(row, column)

    idx.query = spy  # shadow the instance method

    rep = run_table_tests(src, _table(handle, n), idx)
    assert rep.passed, f"clean table should pass, got {rep.failures}"

    amount_keys = {r for (r, c) in seen if c == "Amount"}
    all_keys = set(idx.row_keys())
    assert amount_keys == all_keys, (
        "dtype validation did not read every sampled 'Amount' cell via query(); "
        f"query() saw {len(amount_keys)}/{len(all_keys)} keys "
        f"(missing {sorted(all_keys - amount_keys)[:5]}...). "
        "Validation must flow through the real query() function."
    )
