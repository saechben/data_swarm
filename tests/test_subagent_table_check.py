"""Tests for the table-level validation / recovery check (offline, verify-before-accept)."""
import openpyxl

from mcg_swarm.orchestrator import orchestrate_table
from mcg_swarm.schemas import CanonicalTable, ColumnSpec, ExtractionRef
from mcg_swarm.splitter import split_workbook
from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from mcg_swarm.subagent.table_check import (
    TableCheckPolicy, TableValidator, _is_label, _label_score)


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


def _numbers_as_text_wb(tmp_path):
    """A clean-but-misheadered table: static over-detects header_span and folds the first
    data row into the header, so column names become data values ('49', '1200')."""
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Data"
    ws.append(["Product", "Price", "Qty"])
    ws.append(["Widget", "49", "1200"])   # numbers stored as TEXT
    ws.append(["Gadget", "99", "800"])
    p = tmp_path / "nat.xlsx"; wb.save(p); return str(p)


# --- policy ----------------------------------------------------------------

def test_policy_fallback_fires_on_errors_even_without_validate():
    pol = TableCheckPolicy(validate=False)
    assert pol.should_check(_table(errors=["quality gate: x"]), n_data_rows=3)


def test_policy_validate_checks_clean_table():
    assert TableCheckPolicy(validate=True).should_check(_table(), n_data_rows=3)
    assert not TableCheckPolicy(validate=False).should_check(_table(), n_data_rows=3)


def test_policy_size_guard_removed():
    # Size gate is gone — large tables are now checked (cost bounded by sampling).
    assert TableCheckPolicy(validate=True).should_check(_table(), n_data_rows=10_000)


# --- label score (year-aware) ----------------------------------------------

def test_is_label_distinguishes_data_from_header():
    assert _is_label("Product") and _is_label("Region")
    assert _is_label("2023") and _is_label("2024")        # years are valid labels
    assert not _is_label("49") and not _is_label("1200")  # bare non-year numbers
    assert not _is_label("1,200")


def test_label_score_prefers_real_header():
    real = [ColumnSpec(name=n, dtype="string") for n in ("Product", "Price", "Qty")]
    folded = [ColumnSpec(name=n, dtype="string") for n in ("Widget", "49", "1200")]
    assert _label_score(real) > _label_score(folded)


# --- verify-before-accept (metadata) ---------------------------------------

def test_gate_blind_dtype_change_on_clean_table_is_rejected(tmp_path):
    # Clean table; agent proposes Revenue string->number. The candidate re-indexes to the
    # same 0 errors and identical names -> not provably better -> rejected (stays string).
    p = _wb(tmp_path)
    handle = split_workbook(p)[0]
    runner = FakeAgentRunner(actions=[], final={"column_patches": [
        {"name": "Revenue", "dtype": "number"}]})
    out = TableValidator(runner, TableCheckPolicy(validate=True)).review(p, handle, _table())
    assert out.columns[1].dtype == "string"      # unverifiable change refused


def test_metadata_fix_that_clears_errors_is_accepted(tmp_path):
    # Original carries an error; the agent's fix re-indexes clean -> fewer errors -> accept.
    p = _wb(tmp_path)
    handle = split_workbook(p)[0]
    runner = FakeAgentRunner(actions=[], final={"column_patches": [
        {"name": "Revenue", "dtype": "number"}]})
    out = TableValidator(runner, TableCheckPolicy(validate=False)).review(
        p, handle, _table(errors=["stale: investigate"]))
    assert out.columns[1].dtype == "number"
    assert out.errors == []                       # errors recomputed from the candidate


# --- structural recovery (header re-detection) -----------------------------

def test_structural_recovery_fixes_overdetected_header(tmp_path):
    p = _numbers_as_text_wb(tmp_path)
    handle = split_workbook(p)[0]
    static = orchestrate_table(p, handle, "Data__0")          # no validator
    assert [c.name for c in static.columns] == ["Widget", "49", "1200"]  # the bug

    runner = FakeAgentRunner(actions=[{"tool": "header_candidates", "args": {}}], final={
        "header_span": 1,
        "columns": [{"name": "Product", "dtype": "string", "role": "key"},
                    {"name": "Price", "dtype": "number", "role": "value"},
                    {"name": "Qty", "dtype": "number", "role": "value"}]})
    out = TableValidator(runner, TableCheckPolicy(validate=True)).review(p, handle, static)
    assert [c.name for c in out.columns] == ["Product", "Price", "Qty"]   # recovered
    assert out.header_span == 1
    assert out.errors == []


def test_structural_patch_with_fabricated_name_is_rejected(tmp_path):
    # The gate's column-name check guards against invented names: 'FAKE' is not a live
    # header cell, so the candidate gains a gate error and is refused.
    p = _numbers_as_text_wb(tmp_path)
    handle = split_workbook(p)[0]
    static = orchestrate_table(p, handle, "Data__0")
    runner = FakeAgentRunner(actions=[], final={
        "header_span": 1,
        "columns": [{"name": "Product", "dtype": "string", "role": "key"},
                    {"name": "FAKE", "dtype": "number", "role": "value"},
                    {"name": "Qty", "dtype": "number", "role": "value"}]})
    out = TableValidator(runner, TableCheckPolicy(validate=True)).review(p, handle, static)
    assert [c.name for c in out.columns] == ["Widget", "49", "1200"]   # original kept


# --- safety ----------------------------------------------------------------

def test_validator_skips_when_policy_says_no(tmp_path):
    p = _wb(tmp_path)
    handle = split_workbook(p)[0]
    runner = FakeAgentRunner(actions=[], final={"column_patches": [
        {"name": "Revenue", "dtype": "number"}]})
    out = TableValidator(runner, TableCheckPolicy(validate=False)).review(p, handle, _table())
    assert out.columns[1].dtype == "string"      # clean + no errors -> never runs


def test_validator_returns_original_on_runner_error(tmp_path):
    p = _wb(tmp_path)
    handle = split_workbook(p)[0]

    class Boom:
        def run(self, seed, tools, *, schema):
            raise RuntimeError("agent died")

    out = TableValidator(Boom(), TableCheckPolicy(validate=True)).review(p, handle, _table())
    assert out.columns[1].dtype == "string"      # unchanged, no raise
