"""TransposedView: downstream sees a vertical table; the raw sheet is horizontal."""
from mcg_swarm.views import TransposedView
from mcg_swarm.source import WorkbookSource
from mcg_swarm.splitter import detect_table
from mcg_swarm.extraction import build_index


class _GridSource:
    """Minimal in-memory WorkbookSource over {sheet: list[tuple]} grids."""

    def __init__(self, sheets):
        self._sheets = sheets

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
        return self._window(self._sheets[sheet], min_row, min_col, max_row, max_col)

    def read_cell(self, sheet, row, col):
        grid = self._sheets[sheet]
        r = grid[row - 1] if row - 1 < len(grid) else ()
        return r[col - 1] if col - 1 < len(r) else None

    def read_formula_region(self, sheet, min_row=None, min_col=None, max_row=None, max_col=None):
        return self.read_region(sheet, min_row, min_col, max_row, max_col)


# Horizontal (transposed) layout: fields as rows, records as columns.
_HORIZONTAL = {"S": [("Region", "North", "South"),
                     ("Sales", 10, 20)]}


def test_view_satisfies_workbook_source_protocol():
    view = TransposedView(_GridSource(_HORIZONTAL))
    assert isinstance(view, WorkbookSource)
    assert view.sheet_names() == ["S"]


def test_full_sheet_read_region_is_transposed():
    view = TransposedView(_GridSource(_HORIZONTAL))
    assert view.read_region("S") == [("Region", "Sales"),
                                     ("North", 10),
                                     ("South", 20)]


def test_read_cell_swaps_axes():
    view = TransposedView(_GridSource(_HORIZONTAL))
    # view (row=3, col=2) == inner (row=2, col=3) == 20
    assert view.read_cell("S", 3, 2) == 20


def test_bounded_read_region_window_in_view_coords():
    view = TransposedView(_GridSource(_HORIZONTAL))
    # view rows 2..3, col 2 == the Sales values column
    assert view.read_region("S", min_row=2, min_col=2, max_row=3, max_col=2) == [(10,), (20,)]


def test_formula_region_transposed_too():
    view = TransposedView(_GridSource(_HORIZONTAL))
    assert view.read_formula_region("S")[1] == ("North", 10)


def test_downstream_index_resolves_correct_axis_through_view():
    """Spec §7: build_index through a TransposedView with NO band-layer changes."""
    view = TransposedView(_GridSource(_HORIZONTAL))
    handle = detect_table(view.read_region("S"), "S")  # sees a normal vertical table
    assert handle.region == "A1:B3" and handle.header_row == 1
    idx = build_index(view, handle, row_key=["Region"])
    assert idx.query("North", "Sales").value == 10
    assert idx.query("South", "Sales").value == 20
