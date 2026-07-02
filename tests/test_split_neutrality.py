"""Phase A neutrality: the analyzer-based split_workbook reproduces the pre-refactor
per-sheet detect_table output exactly when analyzers=("vertical",)."""
import pytest

from mcg_swarm.splitter import split_workbook, detect_table
from mcg_swarm.config import SwarmConfig


class _FakeSource:
    """Minimal in-memory WorkbookSource (satisfies the runtime_checkable Protocol)."""

    def __init__(self, sheets):
        self._sheets = sheets  # {sheet_name: list[tuple]}

    def sheet_names(self):
        return list(self._sheets)

    def _window(self, grid, min_row, min_col, max_row, max_col):
        n_rows = len(grid)
        n_cols = max((len(r) for r in grid), default=0)
        r0 = 1 if min_row is None else min_row
        c0 = 1 if min_col is None else min_col
        r1 = n_rows if max_row is None else max_row
        c1 = n_cols if max_col is None else max_col
        out = []
        for r in range(r0, r1 + 1):
            row = grid[r - 1] if r - 1 < len(grid) else ()
            out.append(tuple(row[c - 1] if c - 1 < len(row) else None
                             for c in range(c0, c1 + 1)))
        return out

    def read_region(self, sheet, min_row=None, min_col=None, max_row=None, max_col=None):
        if min_row is min_col is max_row is max_col is None:
            return self._sheets[sheet]  # unbounded: identical to prior behavior
        return self._window(self._sheets[sheet], min_row, min_col, max_row, max_col)

    def read_cell(self, sheet, row, col):
        grid = self._sheets[sheet]
        r = grid[row - 1] if row - 1 < len(grid) else ()
        return r[col - 1] if col - 1 < len(r) else None

    def read_formula_region(self, sheet, min_row=None, min_col=None, max_row=None, max_col=None):
        return self.read_region(sheet, min_row, min_col, max_row, max_col)


_SHEETS = {
    "Sales": [("Region", "Sales"), ("North", 10), ("South", 20)],
    "Costs": [("Dept", "Cost"), ("Eng", 100), ("Ops", 50)],
}


def test_split_workbook_matches_detect_table_per_sheet():
    src = _FakeSource(_SHEETS)
    expected = [detect_table(grid, name) for name, grid in _SHEETS.items()]
    assert split_workbook(src, config=SwarmConfig()) == expected


def test_split_workbook_default_config_is_neutral():
    src = _FakeSource(_SHEETS)
    # No config arg → default SwarmConfig() → analyzers=("vertical",)
    assert split_workbook(src) == [detect_table(g, n) for n, g in _SHEETS.items()]


def test_run_swarm_unknown_analyzer_raises():
    from mcg_swarm.runner import run_swarm
    src = _FakeSource(_SHEETS)
    with pytest.raises(KeyError):
        run_swarm(src, config=SwarmConfig(analyzers=("does_not_exist",)))


def test_run_swarm_emits_pipeline_findings():
    """Lens-failure findings surface on the WorkbookExtraction."""
    from mcg_swarm.analyzers.registry import register
    from mcg_swarm.runner import run_swarm

    class _Boom:
        name = "boom2"
        def analyze(self, grid, sheet, source=None):
            raise RuntimeError("lens exploded")
    register("boom2", _Boom)

    ex = run_swarm(_FakeSource(_SHEETS),
                   config=SwarmConfig(analyzers=("vertical", "boom2")))
    cats = [f.category for f in ex.findings]
    assert cats.count("analyzer-error") == len(_SHEETS)   # one per sheet
    # extraction itself is unharmed — vertical still wins every sheet
    assert len(ex.tables) == len(_SHEETS)
    assert not ex.errors


def test_run_swarm_multi_handle_sheet_orchestrates_all():
    """A SheetAnalysis with N handles yields N tables with __i_j ids."""
    from mcg_swarm.analyzers.registry import register
    from mcg_swarm.splitter import handle_from_region
    from mcg_swarm.analyzers.base import LayoutCandidate
    from mcg_swarm.runner import run_swarm

    two = {"S": [("Region", "Sales"), ("North", 10), ("South", 20),
                 (None, None),
                 ("Dept", "Cost"), ("Eng", 100), ("Ops", 50)]}

    class _Pair:
        name = "pair"
        def analyze(self, grid, sheet, source=None):
            top = handle_from_region(grid, sheet, "A1:B3", 1)
            bottom = handle_from_region(grid, sheet, "A5:B7", 5)
            return [LayoutCandidate(method="pair", handles=(top, bottom),
                                    coverage=1.0)]
    register("pair", _Pair)

    ex = run_swarm(_FakeSource(two), config=SwarmConfig(analyzers=("pair",)))
    ids = sorted(t.table_id for t in ex.tables)
    assert ids == ["S__0_0", "S__0_1"]
    assert all(not t.errors for t in ex.tables)
