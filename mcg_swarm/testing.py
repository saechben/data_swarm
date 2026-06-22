# mcg_swarm/testing.py
"""In-loop quality gate: coverage (resolution-only) + index integrity + round-trip + computed-column checks."""
from __future__ import annotations

from dataclasses import dataclass, field

import openpyxl
from openpyxl.utils import coordinate_to_tuple, get_column_letter

from eval.util import range_box, values_match
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


def _check_column_integrity(path: str, table, index) -> list[str]:
    """
    Column-integrity check (O(cols), one workbook open).

    Derives truth from the live file independently of index._col_to_phys:
    reads the header row at table.header_row and builds name->physical_col
    from the header cells within the table's column bounds, then asserts
    it matches index._col_to_phys for every column name.

    Catches numeric->numeric column remaps that round-trip misses because
    both cell_ref and value come from the same (possibly corrupted) map.
    """
    failures: list[str] = []
    min_row, min_col, max_row, max_col = range_box(table.region)
    header_row = table.header_row

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        ws = wb[table.sheet]
        # Build name -> physical_col from the live header row
        live_col_map: dict[str, int] = {}
        for c in range(min_col, max_col + 1):
            val = ws.cell(row=header_row, column=c).value
            if val not in (None, ""):
                live_col_map[str(val)] = c
    finally:
        wb.close()

    for col_name, idx_phys in index._col_to_phys.items():
        live_phys = live_col_map.get(col_name)
        if live_phys is None:
            failures.append(
                f"column-integrity: {col_name!r} in index but not found in live header row {header_row}"
            )
        elif idx_phys != live_phys:
            failures.append(
                f"column-integrity: {col_name!r} index col={get_column_letter(idx_phys)} "
                f"but live header says col={get_column_letter(live_phys)}"
            )

    return failures


def _check_row_integrity(path: str, table, index, sample_keys: list) -> list[str]:
    """
    Row-integrity check on sampled keys (bounded by sample_size).

    For each sampled key, takes the index-resolved physical row from
    index._key_to_phys, then reads the key-column cell at that row directly
    from the live file and asserts it equals the key.

    Catches row remaps where _key_to_phys points to the wrong physical row.
    Skipped when row_key is empty (positional indexing — no key to verify).
    """
    failures: list[str] = []
    row_key_names = table.extraction.row_key
    if not row_key_names:
        return failures  # positional — nothing to verify

    key_col_name = row_key_names[0]
    if key_col_name not in index._col_to_phys:
        return failures  # can't verify without a resolved key column

    key_phys_col = index._col_to_phys[key_col_name]

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        ws = wb[table.sheet]
        for k in sample_keys:
            if k not in index._key_to_phys:
                continue  # already caught by coverage check
            phys_row = index._key_to_phys[k]
            live_key = ws.cell(row=phys_row, column=key_phys_col).value
            # Normalise: index stores the key as read at build time; compare same type
            if live_key != k:
                failures.append(
                    f"row-integrity: key {k!r} -> row {phys_row} "
                    f"but live cell {get_column_letter(key_phys_col)}{phys_row}={live_key!r}"
                )
    finally:
        wb.close()

    return failures


def _check_column_names(path: str, table) -> list[str]:
    """
    Column-name gate (Fix 3): fail-loud check that table.columns names are:
      (a) unique — no duplicates (col-axis corruption produces duplicates),
      (b) each present in the live header row within the table's region.

    Reuses the same independent header read pattern as _check_column_integrity
    but checks table.columns (the CanonicalTable metadata) rather than the index.
    O(cols), one workbook open.
    """
    failures: list[str] = []
    col_names = [c.name for c in table.columns]

    # (a) Uniqueness
    seen: set[str] = set()
    for name in col_names:
        if name in seen:
            failures.append(
                f"column-name: duplicate column name {name!r} in table.columns"
            )
        seen.add(name)

    # (b) Each name must appear in the live header row
    min_row, min_col, max_row, max_col = range_box(table.region)
    header_row = table.header_row
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        ws = wb[table.sheet]
        live_names: set[str] = set()
        for c in range(min_col, max_col + 1):
            val = ws.cell(row=header_row, column=c).value
            if val not in (None, ""):
                live_names.add(str(val))
    finally:
        wb.close()

    for name in col_names:
        if name not in live_names:
            failures.append(
                f"column-name: {name!r} in table.columns not found in live header row {header_row}"
            )

    return failures


def run_table_tests(path: str, table, index, sample_size: int = 25) -> TableTestReport:
    """
    Run deterministic in-loop quality checks on an extracted table.

    Four phases:
    1. Coverage (resolution-only, O(keys+cols), zero file I/O):
       Every column in index.column_names() must exist in _col_to_phys and every
       row key in index.row_keys() must exist in _key_to_phys.  No per-cell reads.

    2. Index integrity (one workbook open total, O(cols) + O(sample) reads):
       (a) Column-integrity: reads the live header row and asserts name->physical_col
           matches _col_to_phys for each column.  Catches numeric->numeric column remaps.
       (b) Row-integrity: for each sampled key, reads the key-column cell at the
           index-resolved physical row and asserts it equals the key.

    3. Round-trip (bounded sample, ≤ sample_size × cols file reads):
       For a deterministic subset of row keys, compare index.query() value against
       the live cell value read directly from the workbook.

    4. Computed columns (same sample):
       For columns with role="computed" and a matching TableFormula, re-evaluate
       the formula and compare against the live cell.
    """
    failures: list[str] = []

    keys = index.row_keys()
    cols = index.column_names()

    # ------------------------------------------------------------------
    # Phase 1: Coverage — resolution-only, no file I/O
    # ------------------------------------------------------------------
    for col in cols:
        if col not in index._col_to_phys:
            failures.append(f"coverage gap: column {col!r} not in _col_to_phys")

    for k in keys:
        if k not in index._key_to_phys:
            failures.append(f"coverage gap: row key {k!r} not in _key_to_phys")

    # ------------------------------------------------------------------
    # Build deterministic sample (used by phases 2b, 3, 4)
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

    # ------------------------------------------------------------------
    # Phase 2: Index integrity — independent cross-checks against live file
    # ------------------------------------------------------------------
    failures.extend(_check_column_names(path, table))
    failures.extend(_check_column_integrity(path, table, index))
    failures.extend(_check_row_integrity(path, table, index, sample_keys))

    # ------------------------------------------------------------------
    # Phase 3: Round-trip — bounded sample
    # ------------------------------------------------------------------
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
    # Phase 4: Computed columns — same sample
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
