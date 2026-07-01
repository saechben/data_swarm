"""Read-only WHOLE-SHEET tool layer for the structural (boundary) agent.

Mirrors tools.py/BandView but at sheet scope: the structural agent must see data OUTSIDE
the deterministically-chosen region (that is the whole point — a dropped table lives off
the region a band agent can see). Snapshots the sheet's used grid ONCE (open-cost
sensitive), serves every probe from memory. Rows are reported with absolute 1-based row
numbers; cells are 0-based within the snapshot's first column (column 1)."""
from __future__ import annotations

from eval.util import range_box
from mcg_swarm.source import as_source
from mcg_swarm.subagent.tools import Tool


class SheetView:
    """Once-snapshotted read-only view over an entire sheet grid."""

    def __init__(self, source, sheet: str) -> None:
        self.sheet = sheet
        src = as_source(source)
        # read_region with no bounds → the whole used sheet, grid[0] == row 1, col 1.
        self._grid = [list(r) for r in src.read_region(sheet)]

    def _row(self, abs_row: int):
        idx = abs_row - 1
        if 0 <= idx < len(self._grid):
            return list(self._grid[idx])
        return None

    def dimensions(self) -> dict:
        n_rows = len(self._grid)
        n_cols = max((len(r) for r in self._grid), default=0)
        return {"sheet": self.sheet, "n_rows": n_rows, "n_cols": n_cols}

    def peek_rows(self, start_row: int = 1, count: int = 20) -> list[dict]:
        out = []
        for ar in range(start_row, start_row + count):
            row = self._row(ar)
            if row is None:
                if ar <= len(self._grid):
                    continue
                break
            out.append({"row": ar, "cells": row})
        return out

    def peek_region(self, a1: str) -> list[dict]:
        min_row, min_col, max_row, max_col = range_box(a1)
        out = []
        for ar in range(min_row, max_row + 1):
            row = self._row(ar)
            if row is None:
                continue
            out.append({"row": ar,
                        "cells": row[(min_col - 1):max_col]})
        return out


def build_sheet_toolset(view: SheetView) -> list[Tool]:
    """Wrap a SheetView's probes as framework-agnostic Tools."""
    return [
        Tool("dimensions",
             "Whole-sheet size: sheet name, number of used rows and columns.",
             {"type": "object", "properties": {}},
             lambda a: view.dimensions()),
        Tool("peek_rows",
             "Read `count` rows starting at an absolute 1-based sheet row `start_row`.",
             {"type": "object", "properties": {
                 "start_row": {"type": "integer"}, "count": {"type": "integer"}}},
             lambda a: {"rows": view.peek_rows(int(a.get("start_row", 1)),
                                               int(a.get("count", 20)))}),
        Tool("peek_region",
             "Read cells in an absolute A1 range (e.g. 'A5:D12') anywhere on the sheet.",
             {"type": "object", "properties": {"a1": {"type": "string"}},
              "required": ["a1"]},
             lambda a: {"rows": view.peek_region(str(a["a1"]))}),
    ]
