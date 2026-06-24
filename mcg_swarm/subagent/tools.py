"""Framework-agnostic tool layer the ReAct verifier exposes over a band.

`Tool` is a plain dataclass (name / description / JSON-Schema input / handler) that any
agent framework can adapt at its own edge — the tools never import a framework.

`BandView` snapshots the band's cell grid ONCE on construction and serves every probe
from memory; no probe reopens the workbook. This codebase is open-cost sensitive
(`K_MAX` was capped at 4 because each openpyxl open is ~2-3s on large files), so a
tool-calling agent must never trigger repeated opens. Escalation is size-bounded, so the
snapshot stays small.

All probes return JSON-serializable structures (dicts/lists) so they pass cleanly back
as tool results. Rows are reported with their absolute (1-based) sheet row number.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import openpyxl

from eval.util import range_box
from mcg_swarm.size_estimate import Band


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict                      # JSON Schema for the handler's args
    handler: Callable[[dict], dict]         # args dict -> JSON-serializable result


class BandView:
    """Read-only, once-snapshotted view over a band's cells (region-clamped probes)."""

    def __init__(self, path: str, band: Band, rows_above_header: int = 2) -> None:
        self.band = band
        # Snapshot a few rows above the header (to surface title banners / multi-row
        # headers) through the last data row, across the band's columns — one open.
        self._top = max(1, band.header_row - rows_above_header)   # abs first snapshot row
        self._left = band.col_start                               # abs first snapshot col
        self._ncols = band.col_end - band.col_start + 1
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        try:
            ws = wb[band.sheet]
            self._grid = [list(r) for r in ws.iter_rows(
                min_row=self._top, max_row=band.row_end,
                min_col=band.col_start, max_col=band.col_end,
                values_only=True)]
        finally:
            wb.close()

    # -- internal helpers ---------------------------------------------------

    def _row(self, abs_row: int) -> Optional[list]:
        """Return the snapshot row at absolute sheet row *abs_row*, or None if outside."""
        idx = abs_row - self._top
        if 0 <= idx < len(self._grid):
            return list(self._grid[idx])
        return None

    # -- probes -------------------------------------------------------------

    def geometry(self) -> dict:
        b = self.band
        return {
            "sheet": b.sheet,
            "region": b.region,
            "header_row": b.header_row,
            "data_row_start": b.row_start,
            "data_row_end": b.row_end,
            "n_data_rows": b.row_end - b.row_start + 1,
            "n_cols": self._ncols,
            "col_start": b.col_start,
        }

    def header_candidates(self, rows_above: int = 2) -> list[dict]:
        """Rows from a few above the header through the header itself (banner/multi-row)."""
        start = max(self._top, self.band.header_row - rows_above)
        out = []
        for ar in range(start, self.band.header_row + 1):
            row = self._row(ar)
            if row is not None:
                out.append({"row": ar, "cells": row})
        return out

    def peek_rows(self, start: int = 0, count: int = 10) -> list[dict]:
        """`count` data rows starting at data offset `start` (0 = first data row)."""
        out = []
        for off in range(start, start + count):
            ar = self.band.row_start + off
            if ar > self.band.row_end:
                break
            row = self._row(ar)
            if row is None:
                break
            out.append({"row": ar, "cells": row})
        return out

    def tail_rows(self, count: int = 5) -> list[dict]:
        """The last `count` data rows — catches totals / footnote rows the head misses."""
        last = self.band.row_end
        first = max(self.band.row_start, last - count + 1)
        out = []
        for ar in range(first, last + 1):
            row = self._row(ar)
            if row is not None:
                out.append({"row": ar, "cells": row})
        return out

    def column_values(self, col: int, count: int = 50) -> dict:
        """Header + up to `count` data values for band-relative column index `col`."""
        if col < 0 or col >= self._ncols:
            return {"col_index": col, "header": None, "values": []}
        header_row = self._row(self.band.header_row)
        header = header_row[col] if header_row and col < len(header_row) else None
        values = []
        ar = self.band.row_start
        while ar <= self.band.row_end and len(values) < count:
            row = self._row(ar)
            values.append(row[col] if row and col < len(row) else None)
            ar += 1
        return {"col_index": col, "header": header, "values": values}

    def peek_region(self, a1: str) -> list[dict]:
        """Cells in an A1 sub-range, clamped to the band's rows and columns."""
        min_row, min_col, max_row, max_col = range_box(a1)
        lo_r, hi_r = max(min_row, self._top), min(max_row, self.band.row_end)
        lo_c, hi_c = max(min_col, self.band.col_start), min(max_col, self.band.col_end)
        out = []
        for ar in range(lo_r, hi_r + 1):
            row = self._row(ar)
            if row is None:
                continue
            out.append({"row": ar, "cells": row[(lo_c - self._left):(hi_c - self._left + 1)]})
        return out


def build_band_toolset(view: BandView) -> list[Tool]:
    """Wrap a BandView's probes as framework-agnostic Tools."""
    return [
        Tool("geometry",
             "Band geometry: sheet, region, header row, data row range, column count.",
             {"type": "object", "properties": {}},
             lambda a: view.geometry()),
        Tool("header_candidates",
             "Rows from a few above the header through the header row "
             "(to spot title banners or multi-row headers).",
             {"type": "object", "properties": {"rows_above": {"type": "integer"}}},
             lambda a: {"rows": view.header_candidates(int(a.get("rows_above", 2)))}),
        Tool("peek_rows",
             "Read data rows starting at a data offset (0 = first data row).",
             {"type": "object", "properties": {
                 "start": {"type": "integer"}, "count": {"type": "integer"}}},
             lambda a: {"rows": view.peek_rows(int(a.get("start", 0)), int(a.get("count", 10)))}),
        Tool("tail_rows",
             "Read the last N data rows (catches totals / footnote rows).",
             {"type": "object", "properties": {"count": {"type": "integer"}}},
             lambda a: {"rows": view.tail_rows(int(a.get("count", 5)))}),
        Tool("column_values",
             "Header plus up to N data values for a band-relative column index.",
             {"type": "object", "properties": {
                 "col": {"type": "integer"}, "count": {"type": "integer"}},
              "required": ["col"]},
             lambda a: view.column_values(int(a["col"]), int(a.get("count", 50)))),
        Tool("peek_region",
             "Read cells in an A1 sub-range (e.g. 'B3:D9'), clamped to the band.",
             {"type": "object", "properties": {"a1": {"type": "string"}},
              "required": ["a1"]},
             lambda a: {"rows": view.peek_region(str(a["a1"]))}),
    ]
