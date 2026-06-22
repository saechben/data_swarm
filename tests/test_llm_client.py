import pytest
from mcg_swarm.llm.client import FakeLLMClient

def test_fake_returns_queued_dicts_and_records_calls():
    fake = FakeLLMClient(responses=[{"ok": True}, {"ok": False}])
    assert fake.complete(system="s", user="u1") == {"ok": True}
    assert fake.complete(system="s", user="u2") == {"ok": False}
    assert len(fake.calls) == 2 and fake.calls[0]["user"] == "u1"

def test_fake_callable_response():
    fake = FakeLLMClient(responses=lambda system, user, schema: {"echo": user})
    assert fake.complete(system="s", user="hi") == {"echo": "hi"}

def test_fake_exhausted_raises():
    fake = FakeLLMClient(responses=[{"a": 1}])
    fake.complete(system="s", user="u")
    with pytest.raises(IndexError):
        fake.complete(system="s", user="u")
