"""Tests for the table-level validation / recovery check (offline)."""
import openpyxl

from mcg_swarm.schemas import CanonicalTable, ColumnSpec, ExtractionRef
from mcg_swarm.splitter import split_workbook
from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from mcg_swarm.subagent.table_check import TableCheckPolicy, TableValidator


def _wb(tmp_path):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Data"
    ws.append(["Region", "Revenue"])     # header
    ws.append(["EMEA", 100])
    ws.append(["APAC", "n/a"])           # makes static infer Revenue as 'string'
    ws.append(["LatAm", 200])
    p = tmp_path / "t.xlsx"; wb.save(p); return str(p)


def _table(errors=None):
    return CanonicalTable(
        table_id="Data__0", sheet="Data", region="A1:B4", header_row=1,
        columns=[ColumnSpec(name="Region", dtype="string", role="key"),
                 ColumnSpec(name="Revenue", dtype="string", role="value")],
        description="static",
        extraction=ExtractionRef(script_name="idx", row_key=["Region"]),
        errors=errors or [])


# --- policy ----------------------------------------------------------------

def test_policy_fallback_fires_on_errors_even_without_validate():
    pol = TableCheckPolicy(validate=False)
    assert pol.should_check(_table(errors=["quality gate: x"]), n_data_rows=3)


def test_policy_validate_checks_clean_table():
    assert TableCheckPolicy(validate=True).should_check(_table(), n_data_rows=3)
    assert not TableCheckPolicy(validate=False).should_check(_table(), n_data_rows=3)


def test_policy_size_guard():
    assert not TableCheckPolicy(validate=True).should_check(_table(), n_data_rows=10_000)


# --- validator -------------------------------------------------------------

def test_validator_corrects_dtype_on_clean_table(tmp_path):
    p = _wb(tmp_path)
    handle = split_workbook(p)[0]
    runner = FakeAgentRunner(
        actions=[{"tool": "column_values", "args": {"col": 1}}],
        final={"columns": [{"name": "Revenue", "dtype": "number"}]})
    v = TableValidator(runner, TableCheckPolicy(validate=True))
    out = v.review(p, handle, _table())
    assert out.columns[1].dtype == "number"          # corrected from 'string'
    assert runner.observations[0]["header"] == "Revenue"  # the probe really ran


def test_validator_skips_when_policy_says_no(tmp_path):
    p = _wb(tmp_path)
    handle = split_workbook(p)[0]
    runner = FakeAgentRunner(actions=[], final={"columns": [{"name": "Revenue", "dtype": "number"}]})
    v = TableValidator(runner, TableCheckPolicy(validate=False))  # clean + no errors -> skip
    out = v.review(p, handle, _table())
    assert out.columns[1].dtype == "string"          # untouched


def test_validator_returns_original_on_runner_error(tmp_path):
    p = _wb(tmp_path)
    handle = split_workbook(p)[0]

    class Boom:
        def run(self, seed, tools, *, schema):
            raise RuntimeError("agent died")

    out = TableValidator(Boom(), TableCheckPolicy(validate=True)).review(p, handle, _table())
    assert out.columns[1].dtype == "string"          # unchanged, no raise
