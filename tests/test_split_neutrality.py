"""Phase A neutrality: the analyzer-based split_workbook reproduces the pre-refactor
per-sheet detect_table output exactly when analyzers=("vertical",)."""
from mcg_swarm.splitter import split_workbook, detect_table
from mcg_swarm.config import SwarmConfig


class _FakeSource:
    """Minimal in-memory WorkbookSource (satisfies the runtime_checkable Protocol)."""

    def __init__(self, sheets):
        self._sheets = sheets  # {sheet_name: list[tuple]}

    def sheet_names(self):
        return list(self._sheets)

    def read_region(self, sheet, min_row=None, min_col=None, max_row=None, max_col=None):
        return self._sheets[sheet]

    def read_cell(self, sheet, row, col):
        grid = self._sheets[sheet]
        r = grid[row - 1] if row - 1 < len(grid) else ()
        return r[col - 1] if col - 1 < len(r) else None

    def read_formula_region(self, sheet, min_row=None, min_col=None, max_row=None, max_col=None):
        return self._sheets[sheet]


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
