from mcg_swarm.config import SwarmConfig
from mcg_swarm.runner import run_swarm
from mcg_swarm.subagent import build_structural_reviewer
from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from tests.fake_source import FakeSource


def _stacked():
    v = {(1, 1): "Region", (1, 2): "Revenue",
         (2, 1): "EMEA", (2, 2): 100,
         (3, 1): "APAC", (3, 2): 200,
         (5, 1): "Product", (5, 2): "Price",
         (6, 1): "Widget", (6, 2): 49}
    return FakeSource("Data", v, {})


def test_factory_off_when_flag_false():
    r = FakeAgentRunner(actions=[], final={"tables": []})
    assert build_structural_reviewer(runner=r,
                                     config=SwarmConfig(alter_boundaries=False)) is None
    assert build_structural_reviewer(runner=None) is None
    assert build_structural_reviewer(runner=r) is not None


def test_accepted_recut_yields_two_tables():
    runner = FakeAgentRunner(actions=[], final={"tables": [
        {"region": "A1:B3", "header_row": 1},
        {"region": "A5:B6", "header_row": 5}]})
    ext = run_swarm(_stacked(), runner=runner,
                    config=SwarmConfig(validate=False, alter_boundaries=True))
    assert len(ext.tables) == 2
    regions = {t.region for t in ext.tables}
    assert regions == {"A1:B3", "A5:B6"}
    # the dropped-table signal is now marked fixed at workbook scope
    fixed = [f for f in ext.findings
             if f.category == "uncovered-data" and f.resolution == "fixed"]
    assert fixed


def test_no_runner_still_detects_only():
    # runner=None: Phase-1 detection only, single table, uncovered-data still error
    ext = run_swarm(_stacked())
    assert len(ext.tables) == 1
    assert any(f.category == "uncovered-data" and f.severity == "error"
               for f in ext.findings)


def test_recut_rejected_when_live_pipeline_regresses(monkeypatch):
    # The static-vs-live divergence, forced: the split scores strictly better on the
    # static gate (reviewer accepts, recut=True), but the LIVE per-table pipeline emits
    # an error on a candidate sub-table. The re-validation guard must keep the
    # deterministic baseline and flip the finding to rejected.
    import mcg_swarm.runner as R
    from mcg_swarm.schemas import CanonicalTable, Finding

    real = R.orchestrate_table

    def wrap(source, handle, *, table_id, **kw):
        t = real(source, handle, table_id=table_id, **kw)
        if "__0_" in table_id:   # only the accepted re-cut's candidate sub-tables
            bad = Finding(category="messy-tab", severity="error", scope="table",
                          message="injected live-pipeline regression", source="gate")
            # re-validate (NOT model_copy — that skips validators) so errors re-derive
            t = CanonicalTable.model_validate(
                {**t.model_dump(), "findings": [*[f.model_dump() for f in t.findings],
                                                bad.model_dump()]})
        return t

    monkeypatch.setattr(R, "orchestrate_table", wrap)
    runner = FakeAgentRunner(actions=[], final={"tables": [
        {"region": "A1:B3", "header_row": 1},
        {"region": "A5:B6", "header_row": 5}]})
    ext = run_swarm(_stacked(), runner=runner,
                    config=SwarmConfig(validate=False, alter_boundaries=True))
    # baseline kept (single table), detection flipped fixed -> rejected
    assert len(ext.tables) == 1
    assert any(t.region == "A1:B3" for t in ext.tables)
    assert any(f.category == "uncovered-data" and f.resolution == "rejected"
               for f in ext.findings)
    assert not any(f.resolution == "fixed" for f in ext.findings)
