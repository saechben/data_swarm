"""Stage 4 (spec §4.5): run_swarm live-re-validates contested non-baseline
winners against the vertical baseline before commitment."""
import mcg_swarm.runner as runner_mod
from mcg_swarm.analyzers.base import LayoutCandidate
from mcg_swarm.analyzers.registry import register
from mcg_swarm.config import SwarmConfig
from mcg_swarm.runner import run_swarm
from mcg_swarm.splitter import TableHandle, handle_from_region
from tests.test_views import _GridSource

_STACKED = [("Region", "Sales"), ("North", 10), ("South", 20),
            (None, None),
            ("Dept", "Cost"), ("Eng", 100), ("Ops", 50)]

# Three-way fixture: vertical (A1:B3) is the baseline; pairlens (A1:B3+A5:B7)
# is the top; qlens (A5:B7) is a runner-up that keeps the top non-dominant
# (pair has more coverage but a fake gap). Floor passes for pair
# (coverage 12 >= 6, errors 0 <= 0), so the committed winner is contested
# AND different from the baseline -> the live A/B branch runs.
_SCORES = {
    frozenset({"A1:B3"}): (6, 0, 0),
    frozenset({"A1:B3", "A5:B7"}): (12, 0, 1),
    frozenset({"A5:B7"}): (11, 0, 0),
}


class _PairLens:
    name = "s4_pair"
    def analyze(self, grid, sheet, source=None):
        return [LayoutCandidate(method="s4_pair", handles=(
            handle_from_region(grid, sheet, "A1:B3", 1),
            handle_from_region(grid, sheet, "A5:B7", 5)), coverage=1.0)]


class _QLens:
    name = "s4_q"
    def analyze(self, grid, sheet, source=None):
        return [LayoutCandidate(method="s4_q", handles=(
            handle_from_region(grid, sheet, "A5:B7", 5),), coverage=0.5)]


register("s4_pair", _PairLens)
register("s4_q", _QLens)

_CFG = SwarmConfig(analyzers=("vertical", "s4_pair", "s4_q"))


def _patch_scores(monkeypatch):
    def fake(source, grid, handles, sheet):
        return _SCORES[frozenset(h.region for h in handles)]
    monkeypatch.setattr("mcg_swarm.subagent.structural.score_handles", fake)


def test_contested_winner_committed_when_live_clean(monkeypatch):
    _patch_scores(monkeypatch)
    ex = run_swarm(_GridSource({"S": _STACKED}), config=_CFG)
    assert len(ex.tables) == 2                       # pair committed
    assert sorted(t.region for t in ex.tables) == ["A1:B3", "A5:B7"]
    assert all(not t.errors for t in ex.tables)
    assert any(f.category == "contested-layout" and f.severity == "info"
               for f in ex.findings)


def test_contested_winner_rejected_on_live_errors(monkeypatch):
    """A winner that fails LIVE (even though its snapshot score won) is
    rejected: the vertical baseline is committed instead."""
    _patch_scores(monkeypatch)
    real = runner_mod.orchestrate_table

    def flaky(source, handle, **kwargs):
        if handle.region == "A5:B7":                 # only the winner's 2nd table
            bad = TableHandle(handle.sheet, handle.region, handle.header_row,
                              [], ambiguous=True, reason="live failure injection")
            return real(source, bad, **kwargs)
        return real(source, handle, **kwargs)

    monkeypatch.setattr(runner_mod, "orchestrate_table", flaky)
    ex = run_swarm(_GridSource({"S": _STACKED}), config=_CFG)
    assert [t.region for t in ex.tables] == ["A1:B3"]   # baseline kept
    assert not ex.tables[0].errors
    assert any(f.category == "contested-layout" and f.severity == "warning"
               for f in ex.findings)


def test_floor_kept_baseline_skips_live_ab(monkeypatch):
    """When the floor already restored the baseline, winner == baseline and
    no live A/B runs (no contested-layout finding, single normal table)."""
    def fake(source, grid, handles, sheet):
        return {frozenset({"A1:B3"}): (6, 0, 0),
                frozenset({"A1:B3", "A5:B7"}): (12, 1, 0),   # errors > baseline
                frozenset({"A5:B7"}): (11, 0, 3)}[frozenset(h.region for h in handles)]
    monkeypatch.setattr("mcg_swarm.subagent.structural.score_handles", fake)
    ex = run_swarm(_GridSource({"S": _STACKED}), config=_CFG)
    assert [t.region for t in ex.tables] == ["A1:B3"]
    assert not any(f.category == "contested-layout" for f in ex.findings)
    assert any(f.category == "assessor-floor" for f in ex.findings)
