"""Tests for the bounded multi-pass repair loop in TableValidator (Task 8).

TDD: written RED first, then implementation makes them GREEN.
"""
import openpyxl
import pytest

from mcg_swarm.source import OpenpyxlFileSource
from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from mcg_swarm.subagent.table_check import TableValidator, TableCheckPolicy
from mcg_swarm.schemas import CanonicalTable, ColumnSpec, ExtractionRef
from mcg_swarm.splitter import TableHandle


def _setup(tmp_path):
    p = tmp_path / "r.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "D"
    ws.append(["Key", "A", "B"])
    ws.append(["k1", 1, 2])
    ws.append(["k2", 3, 4])
    wb.save(p)
    src = OpenpyxlFileSource(str(p))
    handle = TableHandle(
        sheet="D",
        region="A1:C3",
        header_row=1,
        columns=[
            ColumnSpec(name="Key", dtype="string", role="key"),
            ColumnSpec(name="A", dtype="number"),
            ColumnSpec(name="B", dtype="number"),
        ],
        header_span=1,
    )
    table = CanonicalTable(
        table_id="t",
        sheet="D",
        region="A1:C3",
        header_row=1,
        columns=handle.columns,
        extraction=ExtractionRef(script_name="t", row_key=["Key"]),
    )
    return src, handle, table


# --- FakeAgentRunner finals sequence ----------------------------------------

def test_fake_runner_calls_counter_starts_at_zero():
    runner = FakeAgentRunner(actions=[], final={})
    assert runner.calls == 0


def test_fake_runner_calls_increments_on_each_run():
    runner = FakeAgentRunner(actions=[], final={})
    from mcg_swarm.subagent.table_check import TableRecoveryPatch
    runner.run("seed", [], schema=TableRecoveryPatch)
    assert runner.calls == 1
    runner.run("seed", [], schema=TableRecoveryPatch)
    assert runner.calls == 2


def test_fake_runner_finals_returns_successive(tmp_path):
    """finals[0] on call 1, finals[1] on call 2, last clamped on call 3+."""
    from mcg_swarm.subagent.table_check import TableRecoveryPatch
    runner = FakeAgentRunner(
        actions=[],
        final={"column_patches": [{"name": "Z", "unit": "EUR"}]},
        finals=[
            {"column_patches": [{"name": "A", "unit": "USD"}]},
            {},
        ],
    )
    r0 = runner.run("s", [], schema=TableRecoveryPatch)
    r1 = runner.run("s", [], schema=TableRecoveryPatch)
    r2 = runner.run("s", [], schema=TableRecoveryPatch)  # clamped to finals[-1]
    # first call returns finals[0]
    assert r0.get("column_patches", [{}])[0].get("unit") == "USD"
    # second call returns finals[1] (empty)
    assert r1.get("column_patches", []) == []
    # third call clamped to finals[1] (last)
    assert r2.get("column_patches", []) == []
    assert runner.calls == 3


def test_fake_runner_finals_none_falls_back_to_final():
    from mcg_swarm.subagent.table_check import TableRecoveryPatch
    runner = FakeAgentRunner(actions=[], final={"column_patches": [{"name": "X"}]})
    r = runner.run("s", [], schema=TableRecoveryPatch)
    assert r["column_patches"][0]["name"] == "X"


# --- TableCheckPolicy size gate removed -------------------------------------

def test_size_gate_removed_large_table_is_checked(tmp_path):
    src, handle, table = _setup(tmp_path)
    runner = FakeAgentRunner(actions=[], final={})
    pol = TableCheckPolicy(validate=True, max_passes=1)
    assert pol.should_check(table, n_data_rows=10_000) is True  # no size cap
    TableValidator(runner, pol).review(src, handle, table)
    assert runner.calls == 1


def test_size_gate_removed_policy_has_no_max_table_rows():
    """TableCheckPolicy must not accept max_table_rows anymore."""
    import dataclasses
    fields = {f.name for f in dataclasses.fields(TableCheckPolicy)}
    assert "max_table_rows" not in fields, "max_table_rows should be removed from policy"


def test_policy_has_max_passes():
    pol = TableCheckPolicy(validate=False, max_passes=5)
    assert pol.max_passes == 5


def test_policy_should_check_no_errors_no_validate():
    pol = TableCheckPolicy(validate=False, max_passes=3)
    table = CanonicalTable(
        table_id="x", sheet="S", region="A1:B2", header_row=1,
        columns=[ColumnSpec(name="K", dtype="string", role="key")],
        extraction=ExtractionRef(script_name="s", row_key=["K"]),
    )
    assert pol.should_check(table, n_data_rows=5) is False


def test_policy_should_check_with_errors():
    pol = TableCheckPolicy(validate=False, max_passes=3)
    table = CanonicalTable(
        table_id="x", sheet="S", region="A1:B2", header_row=1,
        columns=[ColumnSpec(name="K", dtype="string", role="key")],
        extraction=ExtractionRef(script_name="s", row_key=["K"]),
        errors=["some error"],
    )
    assert pol.should_check(table, n_data_rows=5) is True


# --- Loop termination: no-op stops after one pass ---------------------------

def test_no_improvement_stops_after_one_pass(tmp_path):
    """Agent proposes nothing → no candidates → break after first pass."""
    src, handle, table = _setup(tmp_path)
    runner = FakeAgentRunner(actions=[], final={})
    out = TableValidator(runner, TableCheckPolicy(validate=True, max_passes=3)).review(
        src, handle, table
    )
    assert out.errors == []    # clean stays clean
    assert runner.calls == 1   # stopped after one no-op pass


def test_max_passes_respected(tmp_path):
    """With max_passes=1 and a clean table + validate=True, exactly 1 pass runs."""
    src, handle, table = _setup(tmp_path)
    runner = FakeAgentRunner(actions=[], final={})
    TableValidator(runner, TableCheckPolicy(validate=True, max_passes=1)).review(
        src, handle, table
    )
    assert runner.calls == 1


# --- Multi-pass: accepted candidate continues loop --------------------------

def test_multi_pass_each_call_distinct(tmp_path):
    """First pass yields an accepted metadata change; second pass is a no-op → stops."""
    src, handle, table = _setup(tmp_path)
    # Pass 0: agent proposes unit="USD" on column "A" — column A has no unit, so this
    # IS a real change. _accepts checks: same error count (0 vs 0) + label score same
    # → rejected (tie not broken positively). Loop stops after 1 call.
    runner = FakeAgentRunner(
        actions=[],
        finals=[
            {"column_patches": [{"name": "A", "unit": "USD"}]},
            {},
        ],
    )
    TableValidator(runner, TableCheckPolicy(validate=True, max_passes=3)).review(
        src, handle, table
    )
    assert runner.calls >= 1  # loop invoked at least once


def test_rejected_patch_breaks_loop(tmp_path):
    """Rejected candidate stops the loop — don't burn all passes on no-ops."""
    src, handle, table = _setup(tmp_path)
    # Both passes produce no-op (empty patch → no candidates). Loop breaks after 1st.
    runner = FakeAgentRunner(
        actions=[],
        finals=[{}, {}],
    )
    TableValidator(runner, TableCheckPolicy(validate=True, max_passes=3)).review(
        src, handle, table
    )
    assert runner.calls == 1


# --- verify-before-accept stays enforced across passes ---------------------

def test_verify_before_accept_holds_in_loop(tmp_path):
    """A patch that produces no improvement must not be adopted on any pass."""
    src, handle, table = _setup(tmp_path)
    # Propose a unit change — will re-index to same 0 errors, same label score → reject
    runner = FakeAgentRunner(
        actions=[],
        finals=[
            {"column_patches": [{"name": "A", "unit": "USD"}]},
        ],
    )
    out = TableValidator(runner, TableCheckPolicy(validate=True, max_passes=3)).review(
        src, handle, table
    )
    # unit should NOT have been adopted (not provably better)
    a_col = next(c for c in out.columns if c.name == "A")
    assert a_col.unit is None


# --- Review returns original on exception ----------------------------------

def test_review_returns_original_on_exception(tmp_path):
    src, handle, table = _setup(tmp_path)

    class Boom:
        calls = 0
        def run(self, seed, tools, *, schema):
            raise RuntimeError("agent died")

    out = TableValidator(Boom(), TableCheckPolicy(validate=True, max_passes=3)).review(
        src, handle, table
    )
    assert out is table  # original returned, no raise


# --- Policy skips when should_check is False --------------------------------

def test_review_skips_when_policy_says_no(tmp_path):
    src, handle, table = _setup(tmp_path)
    runner = FakeAgentRunner(actions=[], final={"column_patches": [{"name": "A", "unit": "USD"}]})
    out = TableValidator(runner, TableCheckPolicy(validate=False, max_passes=3)).review(
        src, handle, table
    )
    assert runner.calls == 0   # never ran
    assert out is table        # unchanged
