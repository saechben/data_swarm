"""Tests for build_subagent selection + graceful fallback (no SDK/key needed)."""
import pytest

from mcg_swarm.subagent import build_subagent, StaticSubagent
from mcg_swarm.subagent.escalating import EscalatingSubagent


def test_default_is_static(monkeypatch):
    monkeypatch.delenv("MCG_SUBAGENT", raising=False)
    assert isinstance(build_subagent(), StaticSubagent)


def test_react_without_any_auth_falls_back(monkeypatch):
    # No API key AND no claude CLI on PATH → no usable agent auth → static.
    monkeypatch.setenv("MCG_SUBAGENT", "react")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("mcg_swarm.subagent.shutil.which", lambda _name: None)
    assert isinstance(build_subagent(), StaticSubagent)


def test_react_uses_cli_auth_without_key(monkeypatch):
    # The Claude Agent SDK authenticates via the logged-in `claude` CLI, so react must
    # engage even with no ANTHROPIC_API_KEY when the CLI is present (verified live).
    pytest.importorskip("claude_agent_sdk")
    monkeypatch.setenv("MCG_SUBAGENT", "react")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("mcg_swarm.subagent.shutil.which", lambda _name: "/usr/bin/claude")
    assert isinstance(build_subagent(), EscalatingSubagent)


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
