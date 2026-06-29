"""build_subagent / build_table_validator wiring: runner injected, no env, no SDK."""
from mcg_swarm.subagent import build_subagent, build_table_validator, StaticSubagent
from mcg_swarm.subagent.escalating import EscalatingSubagent
from mcg_swarm.subagent.table_check import TableValidator
from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from mcg_swarm.config import SwarmConfig


def _fake_runner():
    return FakeAgentRunner(actions=[], final={})


def test_no_runner_is_static():
    assert isinstance(build_subagent(runner=None), StaticSubagent)


def test_runner_gives_escalating():
    sub = build_subagent(runner=_fake_runner())
    assert isinstance(sub, EscalatingSubagent)


def test_validator_none_without_runner():
    assert build_table_validator(runner=None) is None


def test_validator_present_with_runner():
    assert isinstance(build_table_validator(runner=_fake_runner()), TableValidator)


def test_config_threads_validate_into_escalation():
    sub = build_subagent(runner=_fake_runner(), config=SwarmConfig(validate=False))
    assert sub._policy.validate is False
