"""TDD tests for extract_json helper — RED phase first, then GREEN after implementation."""
import pytest
from mcg_swarm.llm.client import extract_json


def test_raw_json():
    assert extract_json('{"ok": true}') == {"ok": True}


def test_json_fenced():
    text = '```json\n{"status": "ok", "value": 42}\n```'
    assert extract_json(text) == {"status": "ok", "value": 42}


def test_plain_fenced():
    text = '```\n{"x": 1}\n```'
    assert extract_json(text) == {"x": 1}


def test_prose_wrapped():
    text = 'Here is your result:\n{"answer": "yes"}\nHope that helps!'
    assert extract_json(text) == {"answer": "yes"}


def test_nested_object():
    text = 'Sure! {"a": {"b": [1, 2, 3]}, "c": true} is what you asked for.'
    result = extract_json(text)
    assert result == {"a": {"b": [1, 2, 3]}, "c": True}


def test_prose_with_fenced_json():
    text = 'I will return JSON:\n```json\n{"nested": {"key": "val"}}\n```\nDone.'
    assert extract_json(text) == {"nested": {"key": "val"}}


def test_unparseable_raises_value_error():
    with pytest.raises(ValueError, match="no JSON object"):
        extract_json("this has no JSON at all, just plain text")


def test_unparseable_truncates_in_error():
    long_text = "x" * 500
    with pytest.raises(ValueError) as exc_info:
        extract_json(long_text)
    # error message should be truncated, not include all 500 chars
    assert len(str(exc_info.value)) < 400


def test_raw_json_with_whitespace():
    assert extract_json('  {"k": "v"}  ') == {"k": "v"}
