"""Table-level validation / recovery check.

The band subagent runs deep in the pipeline (on one slice, before the table is merged
and quality-checked), so it cannot see whether the WHOLE table came out broken. This
check runs the ReAct agent over the fully-assembled `CanonicalTable`:

- **fallback (always on):** if the static pipeline returned a table WITH errors, run the
  agent to attempt recovery. Not configurable.
- **validation (configurable):** if the table is clean, also double-check column metadata
  when ``validate`` is set.

It reuses the band tools + the shared column patch (dtype/unit/role). It never raises —
any failure returns the original table unchanged. (Deeper recovery — re-detecting headers
or renaming columns and rebuilding the index — is a planned follow-up; today it corrects
column metadata.)
"""
from __future__ import annotations

import dataclasses
import json

from eval.util import range_box
from mcg_swarm.schemas import CanonicalTable
from mcg_swarm.size_estimate import Band
from mcg_swarm.subagent.escalating import REACT_MAX_TABLE_ROWS
from mcg_swarm.subagent.tools import BandView, build_band_toolset
from mcg_swarm.subagent.verifier import SegmentReportPatch, apply_column_patch


@dataclasses.dataclass
class TableCheckPolicy:
    """When to run the table-level agent check (size-guarded)."""
    validate: bool = False
    max_table_rows: int = REACT_MAX_TABLE_ROWS

    def should_check(self, table: CanonicalTable, n_data_rows: int) -> bool:
        if n_data_rows > self.max_table_rows:
            return False
        return bool(table.errors) or self.validate


def _table_seed(table: CanonicalTable) -> str:
    cols = [{"name": c.name, "dtype": c.dtype, "role": c.role, "unit": c.unit}
            for c in table.columns]
    lines = [
        "You are validating one fully-extracted spreadsheet table. A fast deterministic "
        "pass produced the column metadata below. Inspect the real cells with the "
        "read-only tools, then call `finalize` with corrections to column dtype/unit/role "
        "(only columns you change). Never invent cell values.",
        "",
        f"Region: {table.region}  header_row: {table.header_row}",
        "Columns: " + json.dumps(cols),
    ]
    if table.errors:
        lines.append("Static extraction reported ERRORS to investigate: "
                     + json.dumps(list(table.errors)))
    return "\n".join(lines)


class TableValidator:
    """Runs the ReAct agent over an assembled CanonicalTable to validate/recover it."""

    def __init__(self, runner, policy: TableCheckPolicy | None = None) -> None:
        self._runner = runner
        self._policy = policy or TableCheckPolicy()

    def review(self, path: str, handle, table: CanonicalTable) -> CanonicalTable:
        try:
            min_r, min_c, max_r, max_c = range_box(handle.region)
            n_data_rows = max_r - handle.header_row
            if not self._policy.should_check(table, n_data_rows):
                return table
            band = Band(
                sheet=handle.sheet, header_row=handle.header_row, region=handle.region,
                col_start=min_c, col_end=max_c,
                row_start=handle.header_row + 1, row_end=max_r,
            )
            view = BandView(path, band)
            tools = build_band_toolset(view)
            patch = self._runner.run(_table_seed(table), tools, schema=SegmentReportPatch)
            new_cols = apply_column_patch(table.columns, patch)
            return table.model_copy(update={"columns": new_cols})
        except Exception:
            return table  # never break the pipeline
