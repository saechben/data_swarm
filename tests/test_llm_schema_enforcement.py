"""Boundary schema enforcement for the LLMClient interface.

The swarm calls `llm.complete(system, user, schema=Model)` and must get back a dict
that already conforms to `Model` — regardless of whether the backend is a one-shot
LLM, the test fake, or a future agentic/ReAct client. These tests pin that contract
on `FakeLLMClient` (which shares the exact `complete()` boundary as every backend).
"""
from __future__ import annotations

from typing import Optional

import pytest
from pydantic import BaseModel

from mcg_swarm.llm.client import FakeLLMClient, LLMSchemaError, _enforce


class Coord(BaseModel):
    found: bool
    table_id: Optional[str] = None
    row_label: Optional[str] = None
    col_label: Optional[str] = None


# ── enforcement on/off ─────────────────────────────────────────────────────────

def test_no_schema_passes_raw_through():
    """schema=None → response returned verbatim (unstructured calls still work)."""
    fake = FakeLLMClient([{"anything": [1, 2, 3]}])
    assert fake.complete("s", "u") == {"anything": [1, 2, 3]}


def test_valid_response_is_validated_and_returned_as_dict():
    fake = FakeLLMClient([{"found": True, "table_id": "t", "row_label": "r", "col_label": "c"}])
    out = fake.complete("s", "u", schema=Coord)
    assert out == {"found": True, "table_id": "t", "row_label": "r", "col_label": "c"}


def test_missing_required_field_raises_schema_error():
    """A response missing the required `found` field fails loud at the boundary."""
    fake = FakeLLMClient([{"table_id": "t"}])
    with pytest.raises(LLMSchemaError):
        fake.complete("s", "u", schema=Coord)


def test_wrong_type_raises_schema_error():
    """`found` must be bool-coercible; a non-bool-ish string fails validation."""
    fake = FakeLLMClient([{"found": "definitely not a bool"}])
    with pytest.raises(LLMSchemaError):
        fake.complete("s", "u", schema=Coord)


def test_non_dict_response_raises_schema_error():
    fake = FakeLLMClient([["not", "an", "object"]])
    with pytest.raises(LLMSchemaError):
        fake.complete("s", "u", schema=Coord)


# ── dict shape guarantees ───────────────────────────────────────────────────────

def test_unset_optional_fields_are_omitted_not_null():
    """exclude_none keeps `.get(key, default)` semantics: an unset field is ABSENT,
    so callers' defaults still apply (a None-valued key would override them)."""
    fake = FakeLLMClient([{"found": False}])
    out = fake.complete("s", "u", schema=Coord)
    assert out == {"found": False}
    assert out.get("table_id", "DEFAULT") == "DEFAULT"


def test_extra_fields_are_ignored_not_rejected():
    """A chatty model adding fields must not break enforcement (robustness)."""
    fake = FakeLLMClient([{"found": True, "table_id": "t", "explanation": "because reasons"}])
    out = fake.complete("s", "u", schema=Coord)
    assert "explanation" not in out
    assert out["found"] is True and out["table_id"] == "t"


# ── _enforce unit ───────────────────────────────────────────────────────────────

def test_enforce_passthrough_for_non_model_schema():
    raw = {"x": 1}
    assert _enforce(raw, None) is raw            # None → identity passthrough
    assert _enforce(raw, {"legacy": "dict"}) is raw  # legacy dict hint → no enforcement
