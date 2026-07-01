from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from pydantic import BaseModel


class _P(BaseModel):
    ok: bool = True


def test_fake_runner_accepts_system_kwarg():
    r = FakeAgentRunner(actions=[], final={"ok": True})
    # must not raise when a system prompt is supplied
    out = r.run("seed", [], schema=_P, system="a different system prompt")
    assert out == {"ok": True}


def test_fake_runner_still_works_without_system():
    r = FakeAgentRunner(actions=[], final={"ok": True})
    assert r.run("seed", [], schema=_P) == {"ok": True}
