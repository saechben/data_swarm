"""E2E: the pure-agentic lens through run_swarm — propose structure, extract
deterministically, prove by query. FakeAgentRunner throughout (no live SDK).

config(validate=False, alter_boundaries=False) quiets the band verifier and
Layer-2 reviewer so the injected runner reaches ONLY the analyzer layer."""
from mcg_swarm.config import SwarmConfig
from mcg_swarm.runner import build_indices, run_swarm
from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from tests.test_views import _GridSource

_HORIZONTAL = {"S": [("Region", "North", "South"), ("Sales", 10, 20)]}
_STACKED = {"S": [("Region", "Sales"), ("North", 10), ("South", 20),
                  (None, None),
                  ("Dept", "Cost"), ("Eng", 100), ("Ops", 50)]}
_QUIET = dict(validate=False, alter_boundaries=False)


def test_agentic_lens_transposed_sheet_end_to_end():
    """Agent proposes a transposed reading -> run_swarm extracts through the
    view -> orientation persists -> adapter rebuild queries the right axis."""
    proposal = [{"region": "A1:B3", "header_row": 1, "orientation": "transposed"}]
    runner = FakeAgentRunner(
        actions=[{"tool": "try_layout", "args": {"tables": proposal}}],
        final={"tables": proposal, "rationale": "fields run down column A"})
    src = _GridSource(_HORIZONTAL)
    ex = run_swarm(src, runner=runner,
                   config=SwarmConfig(analyzers=("agentic",), **_QUIET))
    assert len(ex.tables) == 1
    t = ex.tables[0]
    assert t.orientation == "transposed" and not t.errors
    idx = build_indices(src, ex)[t.table_id]
    assert idx.query("North", "Sales").value == 10
    assert idx.query("South", "Sales").value == 20


def test_agentic_lens_multi_table_sheet_end_to_end():
    proposal = [{"region": "A1:B3", "header_row": 1},
                {"region": "A5:B7", "header_row": 5}]
    runner = FakeAgentRunner(actions=[], final={"tables": proposal})
    src = _GridSource(_STACKED)
    ex = run_swarm(src, runner=runner,
                   config=SwarmConfig(analyzers=("agentic",), **_QUIET))
    assert sorted(t.region for t in ex.tables) == ["A1:B3", "A5:B7"]
    assert all(not t.errors for t in ex.tables)
    idx = build_indices(src, ex)
    bottom = next(t for t in ex.tables if t.region == "A5:B7")
    assert idx[bottom.table_id].query("Eng", "Cost").value == 100


def test_agentic_agrees_with_vertical_dedups_to_baseline():
    """'Agreed by both approaches': identical interpretation -> Stage-0 dedup
    keeps the vertical label (agentic confidence 0.7 < 1.0), single candidate,
    no arbiter consult, extraction identical to the deterministic run."""
    proposal = [{"region": "A1:B3", "header_row": 1}]
    runner = FakeAgentRunner(actions=[], final={"tables": proposal})
    vertical = {"S": [("Region", "Sales"), ("North", 10), ("South", 20)]}
    ex = run_swarm(_GridSource(vertical), runner=runner,
                   config=SwarmConfig(analyzers=("vertical", "agentic"), **_QUIET))
    base = run_swarm(_GridSource(vertical), config=SwarmConfig(**_QUIET))
    assert [t.region for t in ex.tables] == [t.region for t in base.tables]
    assert [t.table_id for t in ex.tables] == [t.table_id for t in base.tables]
    assert not [f for f in ex.findings
                if f.category in ("contested-layout", "arbiter-choice")]
