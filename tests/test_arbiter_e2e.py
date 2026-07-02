"""E2E: the Stage-2 arbiter inside a full run_swarm pass (FakeAgentRunner).

config(validate=False, alter_boundaries=False) quiets the band verifier and
Layer-2 reviewer so the ONLY runner consumer on clean data is the arbiter."""
from mcg_swarm.analyzers.base import LayoutCandidate
from mcg_swarm.analyzers.registry import register
from mcg_swarm.config import SwarmConfig
from mcg_swarm.runner import build_indices, run_swarm
from mcg_swarm.splitter import detect_table, handle_from_region
from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from tests.test_views import _GridSource

_STACKED = [("Region", "Sales"), ("North", 10), ("South", 20),
            (None, None),
            ("Dept", "Cost"), ("Eng", 100), ("Ops", 50)]


class _E2EPairLens:
    name = "e2e_pair"
    def analyze(self, grid, sheet, source=None):
        return [LayoutCandidate(method="e2e_pair", handles=(
            handle_from_region(grid, sheet, "A1:B3", 1),
            handle_from_region(grid, sheet, "A5:B7", 5)), coverage=1.0)]


class _E2ECloneLens:
    name = "e2e_clone"
    def analyze(self, grid, sheet, source=None):
        return [LayoutCandidate(method="e2e_clone",
                                handles=(detect_table(grid, sheet),),
                                confidence=0.9)]


register("e2e_pair", _E2EPairLens)
register("e2e_clone", _E2ECloneLens)

_QUIET = dict(validate=False, alter_boundaries=False)


def _patch_scores(monkeypatch):
    def fake(source, grid, handles, sheet):
        return {frozenset({"A1:B3"}): (6, 0, 0),
                frozenset({"A1:B3", "A5:B7"}): (12, 0, 1),
                }[frozenset(h.region for h in handles)]
    monkeypatch.setattr("mcg_swarm.subagent.structural.score_handles", fake)


def test_run_swarm_arbiter_end_to_end(monkeypatch):
    """Disagreement -> arbiter consulted once -> its pick extracted with zero
    errors, indexed, and queryable: correctness stays provable."""
    _patch_scores(monkeypatch)
    runner = FakeAgentRunner(actions=[{"tool": "dimensions"}],
                             final={"choice": 0, "rationale": "two tables"})
    src = _GridSource({"S": _STACKED})
    ex = run_swarm(src, runner=runner,
                   config=SwarmConfig(analyzers=("vertical", "e2e_pair"), **_QUIET))
    assert runner.calls == 1
    assert sorted(t.region for t in ex.tables) == ["A1:B3", "A5:B7"]
    assert all(not t.errors for t in ex.tables)
    idx = build_indices(src, ex)
    top = next(t for t in ex.tables if t.region == "A1:B3")
    assert idx[top.table_id].query("North", "Sales").value == 10


def test_run_swarm_agreement_never_calls_runner():
    """Spec exit criterion: lenses agree -> dedup -> no arbiter call, and the
    result matches the default single-lens extraction."""
    runner = FakeAgentRunner(actions=[], final={"choice": 0})
    vertical = {"S": [("Region", "Sales"), ("North", 10), ("South", 20)]}
    ex = run_swarm(_GridSource(vertical), runner=runner,
                   config=SwarmConfig(analyzers=("vertical", "e2e_clone"), **_QUIET))
    base = run_swarm(_GridSource(vertical),
                     config=SwarmConfig(**_QUIET))
    assert runner.calls == 0
    assert [t.region for t in ex.tables] == [t.region for t in base.tables]
    assert [t.table_id for t in ex.tables] == [t.table_id for t in base.tables]


def test_default_config_untouched_by_b2b_machinery():
    """Byte-parity guard: default config, no runner -> no contested/arbiter/
    floor findings anywhere, orientation vertical."""
    vertical = {"S": [("Region", "Sales"), ("North", 10), ("South", 20)]}
    ex = run_swarm(_GridSource(vertical))
    t = ex.tables[0]
    assert t.orientation == "vertical" and not t.errors
    b2b_categories = {"contested-layout", "arbiter-choice", "arbiter-error",
                      "assessor-floor", "unknown-view"}
    assert not [f for f in ex.findings if f.category in b2b_categories]
