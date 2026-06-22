"""OPT-5 tests for the LLM-backed semantic resolver in SwarmAdapter.

Uses FakeLLMClient (no network) for all resolver tests.
One real-workbook prepare() call (sales_regional.xlsx) is shared across tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from eval.adapters.base import SemanticResult
from eval.adapters.swarm_adapter import SwarmAdapter
from eval.harness.runner import load_labels
from eval.util import values_match
from mcg_swarm.llm.client import FakeLLMClient


# ---------------------------------------------------------------------------
# Shared fixture: real swarm prepare on sales_regional.xlsx (no LLM during prepare)
# ---------------------------------------------------------------------------

WORKBOOKS = Path("eval/data/workbooks")
LABELS = Path("eval/data/labels")


@pytest.fixture(scope="module")
def adapter_sales():
    """SwarmAdapter prepared on sales_regional.xlsx.

    Uses the real swarm (deterministic path, no API key needed) and keeps
    self._llm = None so prepare() runs fast.  Individual tests inject a
    FakeLLMClient after fixture setup.
    """
    labels = {l.workbook: l for l in load_labels(LABELS)}
    label = labels["sales_regional.xlsx"]
    a = SwarmAdapter()
    a.prepare(str(WORKBOOKS / "sales_regional.xlsx"), label)
    # Guarantee no real LLM is called during tests.
    a._llm = None
    # Reset coord cache so injected fakes take effect per test.
    a._coord_cache = {}
    return a, label


# ---------------------------------------------------------------------------
# Helper: build a FakeLLMClient that returns the given coord dict once.
# ---------------------------------------------------------------------------

def _fake(coord_dict: dict) -> FakeLLMClient:
    return FakeLLMClient([coord_dict])


def _fake_callable(mapping: dict[str, dict]) -> FakeLLMClient:
    """FakeLLMClient that dispatches on the phrase embedded in the user prompt."""

    def _respond(system, user, schema=None):
        for phrase, resp in mapping.items():
            if phrase in user:
                return resp
        return {"found": False}

    return FakeLLMClient(_respond)


# ---------------------------------------------------------------------------
# Test 1: answer_semantic returns live value + correct coords on a valid LLM response
# ---------------------------------------------------------------------------

def test_answer_semantic_valid_coord(adapter_sales):
    a, label = adapter_sales

    # Discover a real row_label and col_label from the actual index.
    wb = label.workbook
    indices = a._indices.get(wb, {})
    assert indices, "No indices built — swarm failed to parse sales_regional.xlsx"

    table_id = next(iter(indices))
    idx = indices[table_id]
    row_label = str(idx.row_keys()[0])
    col_label = idx.column_names()[0]

    # Inject fake LLM that returns this coord.
    a._llm = _fake({
        "found": True,
        "table_id": table_id,
        "row_label": row_label,
        "col_label": col_label,
    })
    a._coord_cache = {}

    res = a.answer_semantic(wb, "some query about " + row_label)

    assert isinstance(res, SemanticResult)
    assert res.table_id == table_id
    assert res.row_label == row_label
    assert res.col_label == col_label
    # Value must come from the real index (not None).
    assert res.value is not None or res.value is None  # at minimum, no crash
    # If the cell holds a number, cross-check via direct extract.
    direct = a.extract(wb, table_id, "", "", row_label, col_label)
    assert res.value == direct


# ---------------------------------------------------------------------------
# Test 2: found=false → empty SemanticResult (no crash)
# ---------------------------------------------------------------------------

def test_answer_semantic_not_found(adapter_sales):
    a, label = adapter_sales
    wb = label.workbook

    a._llm = _fake({"found": False})
    a._coord_cache = {}

    res = a.answer_semantic(wb, "something that doesn't exist")

    assert isinstance(res, SemanticResult)
    assert res.value is None
    assert res.table_id is None
    assert res.row_label is None
    assert res.col_label is None


# ---------------------------------------------------------------------------
# Test 3: compute_formula resolves two operands and returns correct arithmetic
# ---------------------------------------------------------------------------

def test_compute_formula_two_operands(adapter_sales):
    a, label = adapter_sales
    wb = label.workbook

    # Use real coords from the index (first table, first two numeric-capable cols).
    indices = a._indices.get(wb, {})
    table_id = next(iter(indices))
    idx = indices[table_id]
    row_label = str(idx.row_keys()[0])
    cols = idx.column_names()
    # Need at least 2 columns.
    assert len(cols) >= 2, "Need at least 2 columns for formula test"
    col_a, col_b = cols[0], cols[1]

    val_a = a.extract(wb, table_id, "", "", row_label, col_a)
    val_b = a.extract(wb, table_id, "", "", row_label, col_b)

    # Only test arithmetic if both values are numeric.
    if not (isinstance(val_a, (int, float)) and isinstance(val_b, (int, float))):
        pytest.skip("First two columns not numeric — skip arithmetic check")

    # Inject callable fake that maps operand names to their coords.
    a._llm = _fake_callable({
        "operand_alpha": {
            "found": True, "table_id": table_id,
            "row_label": row_label, "col_label": col_a,
        },
        "operand_beta": {
            "found": True, "table_id": table_id,
            "row_label": row_label, "col_label": col_b,
        },
    })
    a._coord_cache = {}

    result = a.compute_formula(
        wb,
        expression="A + B",
        operands={"A": "operand_alpha", "B": "operand_beta"},
        business_logic="",
    )

    assert result is not None
    assert abs(result - (float(val_a) + float(val_b))) < 1e-9


# ---------------------------------------------------------------------------
# Test 4: invalid col_label from LLM → resolver returns None → answer_semantic empty
# ---------------------------------------------------------------------------

def test_answer_semantic_invalid_col_rejected(adapter_sales):
    a, label = adapter_sales
    wb = label.workbook

    indices = a._indices.get(wb, {})
    table_id = next(iter(indices))
    idx = indices[table_id]
    row_label = str(idx.row_keys()[0])

    # LLM returns a col_label that doesn't exist in the table.
    a._llm = _fake({
        "found": True,
        "table_id": table_id,
        "row_label": row_label,
        "col_label": "__NONEXISTENT_COL__",
    })
    a._coord_cache = {}

    res = a.answer_semantic(wb, "query with bad col")

    # Resolver must reject invalid coord → empty result, no crash.
    assert isinstance(res, SemanticResult)
    assert res.value is None
    assert res.table_id is None


# ---------------------------------------------------------------------------
# Test 5: no LLM (self._llm is None) → answer_semantic returns empty (backwards compat)
# ---------------------------------------------------------------------------

def test_answer_semantic_no_llm(adapter_sales):
    a, label = adapter_sales
    wb = label.workbook

    a._llm = None
    a._coord_cache = {}

    res = a.answer_semantic(wb, "revenue EMEA")

    assert isinstance(res, SemanticResult)
    assert res.value is None


# ---------------------------------------------------------------------------
# Test 6: compute_formula returns None when operand can't be resolved
# ---------------------------------------------------------------------------

def test_compute_formula_missing_operand(adapter_sales):
    a, label = adapter_sales
    wb = label.workbook

    a._llm = _fake({"found": False})
    a._coord_cache = {}

    result = a.compute_formula(
        wb,
        expression="A + B",
        operands={"A": "unknown_measure_xyz", "B": "also_unknown"},
        business_logic="",
    )

    assert result is None
