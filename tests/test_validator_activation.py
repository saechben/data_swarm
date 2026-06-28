"""Tests for validator activation and max_passes configuration."""
import mcg_swarm.subagent as sa


def test_max_passes_from_env(monkeypatch):
    monkeypatch.setenv("MCG_REPAIR_MAX_PASSES", "5")
    assert sa._max_passes() == 5


def test_max_passes_default(monkeypatch):
    monkeypatch.delenv("MCG_REPAIR_MAX_PASSES", raising=False)
    assert sa._max_passes() == 3


def test_validator_none_without_react(monkeypatch):
    monkeypatch.delenv("MCG_SUBAGENT", raising=False)  # default static
    assert sa.build_table_validator() is None
