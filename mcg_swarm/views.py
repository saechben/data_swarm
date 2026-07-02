"""SourceView decorators (spec §4.3) — WorkbookSource wrappers presenting a
transformed coordinate system, so downstream (bands, index, gate) only ever
sees canonical vertical tables. Identity is represented by ``None`` (no wrapper).
"""
from __future__ import annotations

from mcg_swarm.source import WorkbookSource


def _transpose(rows) -> list[tuple]:
    """Transpose a list of row tuples, padding ragged rows with None."""
    rows = list(rows)
    if not rows:
        return []
    width = max(len(r) for r in rows)
    padded = [tuple(r) + (None,) * (width - len(r)) for r in rows]
    return [tuple(col) for col in zip(*padded)]


class TransposedView:
    """Present ``inner``'s sheets with rows and columns swapped.

    A cell at (row=r, col=c) in this view reads inner cell (row=c, col=r).
    An analyzer that detects a fields-as-rows table attaches this view and
    expresses its TableHandle in VIEW coordinates; downstream reads through
    the view and stays vertical-only by construction (spec §2 principle 2).
    """

    def __init__(self, inner: WorkbookSource) -> None:
        self._inner = inner

    def sheet_names(self) -> list[str]:
        return self._inner.sheet_names()

    def read_cell(self, sheet, row, col):
        return self._inner.read_cell(sheet, col, row)

    def read_region(self, sheet, min_row=None, min_col=None, max_row=None, max_col=None):
        return _transpose(self._inner.read_region(
            sheet, min_row=min_col, min_col=min_row,
            max_row=max_col, max_col=max_row))

    def read_formula_region(self, sheet, min_row=None, min_col=None, max_row=None, max_col=None):
        return _transpose(self._inner.read_formula_region(
            sheet, min_row=min_col, min_col=min_row,
            max_row=max_col, max_col=max_row))
