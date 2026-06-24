"""Tests for build_subagent selection + graceful fallback (no SDK/key needed)."""
import pytest

from mcg_swarm.subagent import build_subagent, StaticSubagent
from mcg_swarm.subagent.escalating import EscalatingSubagent


def test_default_is_static(monkeypatch):
    monkeypatch.delenv("MCG_SUBAGENT", raising=False)
    assert isinstance(build_subagent(), StaticSubagent)


def test_react_without_key_falls_back(monkeypatch):
    monkeypatch.setenv("MCG_SUBAGENT", "react")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(build_subagent(), StaticSubagent)


def test_react_with_key_but_sdk_missing_falls_back(monkeypatch):
    try:
        import claude_agent_sdk  # noqa: F401
        pytest.skip("Claude Agent SDK installed; missing-SDK fallback not exercised here")
    except ImportError:
        pass
    monkeypatch.setenv("MCG_SUBAGENT", "react")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    sub = build_subagent()
    assert isinstance(sub, StaticSubagent)
    assert not isinstance(sub, EscalatingSubagent)
