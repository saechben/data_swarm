import dataclasses
from fake_source import vertical_fake
from mcg_swarm.splitter import split_workbook
from mcg_swarm.extraction import build_index
from mcg_swarm.quality_gate import run_table_tests
from mcg_swarm.orchestrator import _orchestrate_core


def test_correct_formula_passes_gate():
    src = vertical_fake()
    handle = split_workbook(src)[0]
    table = _orchestrate_core(src, handle, table_id="t0")
    # Revenue is role='computed' and gate Phase 4 recomputed it without failure.
    assert any(c.name == "Revenue" and c.role == "computed" for c in table.columns)
    assert table.errors == []


def test_wrong_formula_fails_gate():
    src = vertical_fake()
    handle = split_workbook(src)[0]
    table = _orchestrate_core(src, handle, table_id="t0")
    # Corrupt the Revenue formula to a wrong expression and re-run the gate directly.
    # pydantic v2 _Base is not frozen -> plain attribute assignment works.
    for f in table.formulas:
        if f.target == "Revenue":
            f.expression = "Units+Price"   # wrong: should be Units*Price
    row_key = [c.name for c in table.columns if c.role == "key"][:1]
    index = build_index(
        src, dataclasses.replace(handle, columns=table.columns), row_key=row_key)
    report = run_table_tests(src, table, index)
    assert not report.passed
    assert any("Revenue" in fail for fail in report.failures)
