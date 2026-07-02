"""Stage-2 LayoutArbiter: drives the injected AgentRunner over the sheet toolset."""
import pytest

from mcg_swarm.analyzers.arbiter import LayoutArbiter
from mcg_swarm.analyzers.base import LayoutCandidate
from mcg_swarm.splitter import detect_table
from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from tests.test_views import _GridSource

_GRID = [("Region", "Sales"), ("North", 10), ("South", 20)]
_SRC = _GridSource({"S": _GRID})


def _topk():
    h = detect_table(_GRID, "S")
    a = LayoutCandidate(method="vertical", handles=(h,))
    b = LayoutCandidate(method="other", handles=(h,), confidence=0.8)
    return [(a, (6, 0, 0)), (b, (5, 1, 0))]


def test_arbiter_runs_toolset_and_returns_choice():
    runner = FakeAgentRunner(actions=[{"tool": "dimensions"}],
                             final={"choice": 1, "rationale": "matches data"})
    idx = LayoutArbiter(runner).choose(_topk(), source=_SRC, sheet="S")
    assert idx == 1
    assert runner.observations       # the probes ran against the REAL SheetView


def test_arbiter_seed_describes_candidates_and_scores():
    seen = {}

    class _SpyRunner:
        def run(self, seed, tools, *, schema, system=None):
            seen["seed"], seen["system"] = seed, system
            return {"choice": 0}

    LayoutArbiter(_SpyRunner()).choose(_topk(), source=_SRC, sheet="S")
    seed, system = seen["seed"], seen["system"]
    assert "[0]" in seed and "[1]" in seed          # candidates enumerated
    assert "vertical" in seed and "other" in seed   # methods named
    assert "coverage=6" in seed and "errors=1" in seed  # scores exposed
    assert "A1:B3" in seed                          # regions exposed
    assert "never invent" in system.lower()         # pick-one discipline


def test_arbiter_invalid_verdict_raises():
    runner = FakeAgentRunner(actions=[], final={"choice": "not-an-int"})
    with pytest.raises(Exception):                  # pydantic validation error
        LayoutArbiter(runner).choose(_topk(), source=_SRC, sheet="S")
