"""Rich deterministic ranking: score_handles-based, dominance-aware."""
import pytest

from mcg_swarm.analyzers.assess import assess_sheet, rank_candidates, _dominates
from mcg_swarm.analyzers.base import LayoutCandidate
from mcg_swarm.splitter import detect_table, handle_from_region
from tests.test_views import _GridSource

# Two stacked tables separated by a blank row — the canonical A1-violation sheet.
_TWO_TABLES = [
    ("Region", "Sales"),
    ("North", 10),
    ("South", 20),
    (None, None),
    ("Dept", "Cost"),
    ("Eng", 100),
    ("Ops", 50),
]
_SRC = _GridSource({"S": _TWO_TABLES})


def _baseline():
    """What today's splitter sees: the top table only."""
    h = detect_table(_TWO_TABLES, "S")
    return LayoutCandidate(method="vertical", handles=(h,))


def _clean_pair():
    """The correct interpretation: both tables, tightly cut."""
    top = handle_from_region(_TWO_TABLES, "S", "A1:B3", 1)
    bottom = handle_from_region(_TWO_TABLES, "S", "A5:B7", 5)
    return LayoutCandidate(method="multitable", handles=(top, bottom))


def _fused():
    """The greedy over-claim: one region swallowing both + the gap row."""
    h = handle_from_region(_TWO_TABLES, "S", "A1:B7", 1)
    return LayoutCandidate(method="fused", handles=(h,))


def test_dominates_semantics():
    assert _dominates((12, 0, 0), (6, 0, 0))       # more coverage
    assert _dominates((12, 0, 0), (12, 1, 0))      # fewer errors
    assert _dominates((12, 0, 0), (12, 0, 1))      # fewer gaps
    assert _dominates((12, 0, 0), (12, 0, 0))      # ties dominate (short-circuit)
    assert not _dominates((12, 1, 0), (6, 0, 0))   # trade-off = genuine disagreement


def test_clean_pair_outranks_baseline_and_fused():
    ranked = rank_candidates([_baseline(), _clean_pair(), _fused()],
                             source=_SRC, grid=_TWO_TABLES, sheet="S")
    assert ranked[0][0].method == "multitable"
    # the pair claims every non-empty cell with zero interior gaps
    cov, errors, gaps = ranked[0][1]
    assert cov == 12 and gaps == 0
    # the fused over-claim is penalized by its interior blank row
    fused_score = next(s for c, s in ranked if c.method == "fused")
    assert fused_score[2] >= 1


def test_assess_sheet_picks_clean_pair():
    winner = assess_sheet([_baseline(), _clean_pair(), _fused()],
                          source=_SRC, grid=_TWO_TABLES, sheet="S")
    assert winner.method == "multitable"
    assert len(winner.handles) == 2


def test_assess_sheet_single_candidate_identity():
    c = _baseline()
    assert assess_sheet([c], source=_SRC, grid=_TWO_TABLES, sheet="S") is c


def test_assess_sheet_empty_raises():
    with pytest.raises(ValueError):
        assess_sheet([], source=_SRC, grid=_TWO_TABLES, sheet="S")


def test_rank_scores_viewed_candidate_through_its_view():
    """#5: a viewed candidate's handles are in view coordinates — score them there."""
    from mcg_swarm.views import TransposedView

    horizontal = {"S": [("Region", "North", "South"), ("Sales", 10, 20)]}
    src = _GridSource(horizontal)
    raw_grid = src.read_region("S")

    view = TransposedView(src)
    vhandle = detect_table(view.read_region("S"), "S")
    viewed = LayoutCandidate(method="transposed", handles=(vhandle,), view=view)

    ranked = rank_candidates([viewed], source=src, grid=raw_grid, sheet="S")
    cov, errors, _gaps = ranked[0][1]
    assert cov == 6            # all 6 non-empty cells, counted in VIEW coordinates
    assert errors == 0         # scored through the view, the table is clean


def test_rank_candidates_requires_source():
    """B2a final-review #4: source=None must fail loudly, not mis-score
    every handle into orchestration errors (the pipeline's never-raise guard
    turns the raise into a fallback stub + finding)."""
    grid = [("Region", "Sales"), ("North", 10)]
    c = LayoutCandidate(method="vertical", handles=(detect_table(grid, "S"),))
    with pytest.raises(ValueError):
        rank_candidates([c, c], source=None, grid=grid, sheet="S")
