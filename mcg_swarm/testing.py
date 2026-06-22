# mcg_swarm/testing.py
"""In-loop quality gate: coverage (resolution-only) + round-trip + computed-column checks."""
from __future__ import annotations

from dataclasses import dataclass, field

import openpyxl
from openpyxl.utils import coordinate_to_tuple

from eval.util import values_match
from mcg_swarm.formulas import build_env, evaluate


@dataclass
class TableTestReport:
    passed: bool
    failures: list = field(default_factory=list)


def _live_value(path: str, sheet: str, cell_ref: str):
    """Read a single cell value directly from the workbook (one open/close per call)."""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        r, c = coordinate_to_tuple(cell_ref)
        return wb[sheet].cell(row=r, column=c).value
    finally:
        wb.close()


def run_table_tests(path: str, table, index, sample_size: int = 25) -> TableTestReport:
    """
    Run deterministic in-loop quality checks on an extracted table.

    Three phases:
    1. Coverage (resolution-only, O(keys+cols), zero file I/O):
       Every column in index.column_names() must exist in _col_to_phys and every
       row key in index.row_keys() must exist in _key_to_phys.  No per-cell reads.

    2. Round-trip (bounded sample, ≤ sample_size × cols file reads):
       For a deterministic subset of row keys, compare index.query() value against
       the live cell value read directly from the workbook.

    3. Computed columns (same sample):
       For columns with role="computed" and a matching TableFormula, re-evaluate
       the formula and compare against the live cell.
    """
    failures: list[str] = []

    keys = index.row_keys()
    cols = index.column_names()

    # ------------------------------------------------------------------
    # Phase 1: Coverage — resolution-only, no file I/O
    # Check that every column name resolves in the physical-column map
    # and every row key resolves in the physical-row map.
    # This catches gaps where a header column or data row key is missing
    # from the precomputed maps without opening the workbook at all.
    # ------------------------------------------------------------------
    for col in cols:
        if col not in index._col_to_phys:
            failures.append(f"coverage gap: column {col!r} not in _col_to_phys")

    for k in keys:
        if k not in index._key_to_phys:
            failures.append(f"coverage gap: row key {k!r} not in _key_to_phys")

    # ------------------------------------------------------------------
    # Phase 2: Round-trip — bounded sample
    # Build a deterministic sample: on small tables use all keys; on large
    # tables take first, last, and evenly-spaced interior keys up to sample_size.
    # ------------------------------------------------------------------
    if len(keys) <= sample_size:
        sample_keys = keys[:]
    else:
        interior = keys[1:-1]
        step = max(1, len(interior) // (sample_size - 2))
        sample_keys = [keys[0], keys[-1]] + interior[::step]
        # deduplicate while preserving order
        seen: set = set()
        deduped = []
        for k in sample_keys:
            if k not in seen:
                seen.add(k)
                deduped.append(k)
        sample_keys = deduped

    for k in sample_keys:
        for col in cols:
            try:
                v = index.query(k, col)
            except Exception as e:
                failures.append(f"query raised ({k!r},{col!r}): {e}")
                continue
            live = _live_value(path, table.sheet, v.cell_ref)
            dtype = "number" if v.dtype == "number" else "string"
            if not values_match(live, v.value, 1e-9, dtype):
                failures.append(
                    f"roundtrip mismatch at {v.cell_ref}: live={live!r} query={v.value!r}"
                )

    # ------------------------------------------------------------------
    # Phase 3: Computed columns — same sample
    # ------------------------------------------------------------------
    formulas_by_target = {f.target: f for f in table.formulas}
    for c in table.columns:
        if c.role == "computed" and c.name in formulas_by_target:
            f = formulas_by_target[c.name]
            for k in sample_keys:
                try:
                    got = evaluate(
                        f,
                        build_env(
                            f,
                            k,
                            index.query,
                            query_cell=index.query_cell,
                            query_range=index.query_range,
                        ),
                    )
                    live = index.query(k, c.name).value
                    if not values_match(live, got, 1e-6, "number"):
                        failures.append(
                            f"computed mismatch {c.name}@{k}: live={live} calc={got}"
                        )
                except Exception as e:
                    failures.append(f"computed eval failed {c.name}@{k}: {e}")

    return TableTestReport(passed=not failures, failures=failures)
