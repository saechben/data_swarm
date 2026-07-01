# tests/test_structural_repair_e2e.py
"""End-to-end: Layer-2 turns a Phase-1 uncovered-data DETECTION into an actual repair,
offline, via a scripted FakeAgentRunner. The no-runner path stays detection-only."""
from mcg_swarm.config import SwarmConfig
from mcg_swarm.runner import run_swarm
from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from tests.fake_source import FakeSource


def _side_by_side():
    # left table cols A-B rows 1-3; right table cols D-E rows 1-3 (blank col C)
    v = {(1, 1): "Region", (1, 2): "Revenue",
         (2, 1): "EMEA", (2, 2): 100,
         (3, 1): "APAC", (3, 2): 200,
         (1, 4): "Product", (1, 5): "Price",
         (2, 4): "Widget", (2, 5): 49,
         (3, 4): "Gadget", (3, 5): 99}
    return FakeSource("Data", v, {})


def test_side_by_side_repaired_when_runner_present():
    runner = FakeAgentRunner(actions=[], final={"tables": [
        {"region": "A1:B3", "header_row": 1},
        {"region": "D1:E3", "header_row": 1}]})
    ext = run_swarm(_side_by_side(), runner=runner,
                    config=SwarmConfig(validate=False, alter_boundaries=True))
    assert {t.region for t in ext.tables} == {"A1:B3", "D1:E3"}
    assert [f for f in ext.findings
            if f.category == "uncovered-data" and f.resolution == "fixed"]


def test_side_by_side_detected_when_no_runner():
    ext = run_swarm(_side_by_side())
    assert len(ext.tables) == 1
    assert any(f.category == "uncovered-data" and f.severity == "error"
               for f in ext.findings)


def test_hallucinated_recut_rejected_no_corruption():
    # agent proposes a single wrong region that would DROP the left table's data
    runner = FakeAgentRunner(actions=[], final={"tables": [
        {"region": "D1:E3", "header_row": 1}]})
    ext = run_swarm(_side_by_side(), runner=runner,
                    config=SwarmConfig(validate=False, alter_boundaries=True))
    # baseline kept (deterministic left table), still flagged, marked rejected
    assert any(f.category == "uncovered-data" and f.resolution == "rejected"
               for f in ext.findings)
    assert any(t.region == "A1:B3" for t in ext.tables)
