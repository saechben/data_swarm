"""analyze_workbook: rich per-sheet contract, never-raise, stub fallback."""
import pytest

from mcg_swarm.analyzers.pipeline import analyze_workbook, analyze_sheet
from mcg_swarm.analyzers.base import SheetAnalysis, LayoutCandidate
from mcg_swarm.analyzers.registry import register, build_analyzers
from mcg_swarm.config import SwarmConfig
from mcg_swarm.splitter import split_workbook, detect_table
from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from tests.test_views import _GridSource

_SHEETS = {
    "Sales": [("Region", "Sales"), ("North", 10), ("South", 20)],
    "Costs": [("Dept", "Cost"), ("Eng", 100), ("Ops", 50)],
}


def test_analyze_workbook_default_vertical():
    out = analyze_workbook(_GridSource(_SHEETS))
    assert [sa.sheet for sa in out] == ["Sales", "Costs"]
    for sa in out:
        assert isinstance(sa, SheetAnalysis)
        assert sa.method == "vertical" and sa.view is None
        assert sa.handles == (detect_table(_SHEETS[sa.sheet], sa.sheet),)
        assert sa.findings == ()


def test_split_workbook_shim_still_flat_handles():
    src = _GridSource(_SHEETS)
    assert split_workbook(src) == [detect_table(g, n) for n, g in _SHEETS.items()]


class _RaisingLens:
    name = "raising"
    def analyze(self, grid, sheet, source=None):
        raise RuntimeError("boom")


class _EmptyLens:
    name = "empty"
    def analyze(self, grid, sheet, source=None):
        return []


def test_lens_exception_becomes_finding_not_crash():
    register("raising", _RaisingLens)
    analyzers = build_analyzers(("vertical", "raising"))
    sa = analyze_sheet(analyzers, _SHEETS["Sales"], "Sales")
    assert sa.method == "vertical"                       # vertical still wins
    cats = [(f.category, f.severity) for f in sa.findings]
    assert ("analyzer-error", "warning") in cats
    msg = next(f.message for f in sa.findings if f.category == "analyzer-error")
    assert "raising" in msg and "boom" in msg


def test_all_lenses_empty_falls_back_to_stub():
    register("empty", _EmptyLens)
    sa = analyze_sheet(build_analyzers(("empty",)), _SHEETS["Sales"], "Sales")
    assert sa.method == "fallback"
    assert len(sa.handles) == 1
    stub = sa.handles[0]
    assert stub.ambiguous and stub.region == "A1:A1"
    assert stub.reason == "no analyzer produced a candidate"


def test_run_swarm_zero_handle_winner_skips_sheet():
    """A winning candidate with no handles must not crash the run (spec §5)."""
    from mcg_swarm.analyzers.base import LayoutCandidate
    from mcg_swarm.runner import run_swarm

    class _NoHandles:
        name = "nohandles"
        def analyze(self, grid, sheet, source=None):
            return [LayoutCandidate(method="nohandles", handles=(), coverage=1.0)]
    register("nohandles", _NoHandles)

    ex = run_swarm(_GridSource(_SHEETS), config=SwarmConfig(analyzers=("nohandles",)))
    assert ex.tables == []          # sheets skipped, not crashed
    assert ex.sheets == list(_SHEETS)


def test_malformed_candidate_degrades_to_fallback():
    class _Malformed:
        name = "malformed"
        def analyze(self, grid, sheet, source=None):
            return ["not a candidate"]
    register("malformed", _Malformed)

    sa = analyze_sheet(build_analyzers(("malformed",)), _SHEETS["Sales"], "Sales")
    assert sa.method == "fallback"
    assert any(f.category == "analyzer-error" and "assessment failed" in f.message
               for f in sa.findings)


def test_lens_receives_source():
    """#4: the pipeline hands each lens the WorkbookSource so it can build views."""
    seen = {}

    class _SourceSpy:
        name = "sourcespy"
        def analyze(self, grid, sheet, source=None):
            seen["source"] = source
            return []
    register("sourcespy", _SourceSpy)

    src = _GridSource(_SHEETS)
    analyze_workbook(src, config=SwarmConfig(analyzers=("sourcespy",)))
    assert seen["source"] is src


def test_lens_can_construct_view_over_source():
    """A lens can wrap the source in a TransposedView and attach it to a candidate."""
    from mcg_swarm.analyzers.base import LayoutCandidate
    from mcg_swarm.splitter import detect_table
    from mcg_swarm.views import TransposedView

    class _ViewLens:
        name = "viewlens"
        def analyze(self, grid, sheet, source=None):
            view = TransposedView(source)
            vgrid = view.read_region(sheet)
            handle = detect_table(vgrid, sheet)
            return [LayoutCandidate(method="viewlens", handles=(handle,),
                                    coverage=1.0, view=view)]
    register("viewlens", _ViewLens)

    horizontal = {"S": [("Region", "North", "South"), ("Sales", 10, 20)]}
    out = analyze_workbook(_GridSource(horizontal),
                           config=SwarmConfig(analyzers=("viewlens",)))
    sa = out[0]
    assert type(sa.view).__name__ == "TransposedView"
    assert sa.handles[0].region == "A1:B3"      # view coordinates (3 rows after transpose)


def test_pipeline_uses_rich_ranking_for_multi_candidate():
    """#5: with competing lenses, the pipeline picks by score_handles, not raw
    coverage. pairlens hardcodes a LOW coverage (0.1) — below vertical's
    computed ~0.5 — so raw-coverage ranking (plain assess()) would pick
    vertical. Only rich ranking (assess_sheet's score_handles, which counts
    actual covered cells: pairlens covers all 12 non-empty cells vs
    vertical's 6) picks pairlens. This makes the test discriminate the two
    wirings instead of passing under both."""
    from mcg_swarm.splitter import handle_from_region
    from mcg_swarm.analyzers.base import LayoutCandidate

    two = [("Region", "Sales"), ("North", 10), ("South", 20),
           (None, None),
           ("Dept", "Cost"), ("Eng", 100), ("Ops", 50)]

    class _PairLens:
        name = "pairlens"
        def analyze(self, grid, sheet, source=None):
            top = handle_from_region(grid, sheet, "A1:B3", 1)
            bottom = handle_from_region(grid, sheet, "A5:B7", 5)
            return [LayoutCandidate(method="pairlens", handles=(top, bottom),
                                    coverage=0.1)]
    register("pairlens", _PairLens)

    sa = analyze_sheet(build_analyzers(("vertical", "pairlens")), two, "S",
                       source=_GridSource({"S": two}))
    assert sa.method == "pairlens"        # 12-cell coverage beats vertical's 6
    assert len(sa.handles) == 2


# --- B2b: runner/arbiter threading -----------------------------------------

_STACKED = [("Region", "Sales"), ("North", 10), ("South", 20),
            (None, None),
            ("Dept", "Cost"), ("Eng", 100), ("Ops", 50)]


def _register_disagreeing_lenses(monkeypatch):
    """vertical (A1:B3) vs a whole-block lens (A1:B7); patched scores make the
    disagreement genuine (top does not dominate: more coverage but a gap)."""
    from mcg_swarm.splitter import handle_from_region

    class _BigLens:
        name = "biglens"
        def analyze(self, grid, sheet, source=None):
            h = handle_from_region(grid, sheet, "A1:B7", 1)
            return [LayoutCandidate(method="biglens", handles=(h,), coverage=1.0)]
    register("biglens", _BigLens)

    def fake(source, grid, handles, sheet):
        regions = frozenset(h.region for h in handles)
        return {frozenset({"A1:B7"}): (12, 0, 1),
                frozenset({"A1:B3"}): (11, 0, 0)}[regions]
    monkeypatch.setattr("mcg_swarm.subagent.structural.score_handles", fake)


def test_analyze_workbook_agreement_skips_arbiter():
    """Spec exit criterion: when lenses agree, dedup collapses them and the
    arbiter is never consulted."""
    class _AgreeLens:
        name = "agreelens"
        def analyze(self, grid, sheet, source=None):
            from mcg_swarm.splitter import detect_table
            return [LayoutCandidate(method="agreelens",
                                    handles=(detect_table(grid, sheet),),
                                    confidence=0.9)]
    register("agreelens", _AgreeLens)
    runner = FakeAgentRunner(actions=[], final={"choice": 0})
    vertical = {"S": [("Region", "Sales"), ("North", 10)]}
    out = analyze_workbook(_GridSource(vertical),
                           config=SwarmConfig(analyzers=("vertical", "agreelens")),
                           runner=runner)
    assert out[0].method == "vertical"       # higher-confidence label survives dedup
    assert out[0].contested is False
    assert runner.calls == 0


def test_analyze_workbook_disagreement_invokes_arbiter(monkeypatch):
    _register_disagreeing_lenses(monkeypatch)
    runner = FakeAgentRunner(actions=[], final={"choice": 1, "rationale": "r"})
    out = analyze_workbook(_GridSource({"S": _STACKED}),
                           config=SwarmConfig(analyzers=("vertical", "biglens")),
                           runner=runner)
    sa = out[0]
    assert runner.calls == 1
    assert sa.contested is True
    assert sa.method == "vertical"           # arbiter picked index 1 (runner-up)
    assert any(f.category == "arbiter-choice" for f in sa.findings)


def test_arbitrate_config_gate(monkeypatch):
    _register_disagreeing_lenses(monkeypatch)
    runner = FakeAgentRunner(actions=[], final={"choice": 1})
    out = analyze_workbook(_GridSource({"S": _STACKED}),
                           config=SwarmConfig(analyzers=("vertical", "biglens"),
                                              arbitrate=False),
                           runner=runner)
    assert runner.calls == 0
    assert out[0].method == "biglens"        # deterministic top stands


def test_sheet_analysis_carries_baseline(monkeypatch):
    _register_disagreeing_lenses(monkeypatch)
    out = analyze_workbook(_GridSource({"S": _STACKED}),
                           config=SwarmConfig(analyzers=("vertical", "biglens")))
    sa = out[0]
    assert sa.contested is True
    assert sa.method == "biglens"            # no runner -> deterministic top
    assert [h.region for h in sa.baseline_handles] == ["A1:B3"]
    assert sa.baseline_view is None


def test_no_runner_disagreement_unchanged(monkeypatch):
    """Graceful degradation: without a runner the deterministic top wins,
    exactly as before this task."""
    _register_disagreeing_lenses(monkeypatch)
    sa = analyze_sheet(build_analyzers(("vertical", "biglens")), _STACKED, "S",
                       source=_GridSource({"S": _STACKED}))
    assert sa.method == "biglens" and sa.contested is True
