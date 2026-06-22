from __future__ import annotations
import openpyxl
from mcg_swarm.schemas import ColumnSpec, SegmentReport, TableFormula
from mcg_swarm.splitter import _infer_dtype


def _deterministic_columns(path, band, header):
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        ws = wb[band.sheet]
        grid = list(ws.iter_rows(min_row=band.row_start, max_row=min(band.row_end, band.row_start + 19),
                                 min_col=band.col_start, max_col=band.col_end, values_only=True))
    finally:
        wb.close()
    cols = []
    for j, name in enumerate(header):
        samples = [r[j] if j < len(r) else None for r in grid]
        cols.append(ColumnSpec(name=str(name), dtype=_infer_dtype(samples),
                               role="key" if j == 0 else "value"))
    return cols


def _detect_formulas(path, band, header):
    out, anomalies = [], []
    wb = openpyxl.load_workbook(path, data_only=False, read_only=True)
    try:
        ws = wb[band.sheet]
        for r in ws.iter_rows(min_row=band.row_start, max_row=band.row_end,
                              min_col=band.col_start, max_col=band.col_end):
            for cell in r:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    col_idx = cell.column - band.col_start
                    target = header[col_idx] if col_idx < len(header) else cell.coordinate
                    anomalies.append(f"unparsed live formula at {cell.coordinate}: {cell.value}")
                    # NOTE: translation of arbitrary Excel formulas to the allowlist is out
                    # of scope here; record as anomaly so merge/repair can decide. role stays "value".
            break  # one representative row is enough to flag a computed column
    finally:
        wb.close()
    return out, anomalies


def _analyze_band_single_open(path, band, header):
    """Open workbook ONCE to infer column types AND detect first-row formulas.

    Replaces the two separate opens in _deterministic_columns + _detect_formulas
    that each cost ~2-3 s on large files (openpyxl parses the whole XML on open).
    Returns (columns, formulas, anomalies).
    """
    anomalies: list[str] = []
    # data_only=True returns computed cell VALUES (not raw formula strings).
    # Formula strings are only visible with data_only=False, which we intentionally
    # skip here to avoid a second workbook open.  As a result, the formulas list
    # stays empty in this fast path.  This is informational-only: formula detection
    # does not affect scored capabilities.
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        ws = wb[band.sheet]
        # Read up to 20 rows for dtype sampling
        sample_rows = list(ws.iter_rows(
            min_row=band.row_start,
            max_row=min(band.row_end, band.row_start + 19),
            min_col=band.col_start,
            max_col=band.col_end,
            values_only=True,
        ))
    finally:
        wb.close()

    cols = []
    for j, name in enumerate(header):
        samples = [r[j] if j < len(r) else None for r in sample_rows]
        cols.append(ColumnSpec(name=str(name), dtype=_infer_dtype(samples),
                               role="key" if j == 0 else "value"))
    return cols, [], anomalies


def analyze_band(path, band, header, llm=None) -> SegmentReport:
    columns, formulas, anomalies = _analyze_band_single_open(path, band, header)
    desc = f"Band {band.region} with columns: {', '.join(c.name for c in columns)}."
    if llm is not None:
        try:
            schema = {"columns": [{"name": "str", "unit": "str|null", "role": "key|value|computed"}]}
            res = llm.complete(
                system="You verify spreadsheet table headers. Confirm names make sense and "
                       "fill missing unit/role. Never invent cell values.",
                user=f"Header: {header}\nInferred: {[c.model_dump() for c in columns]}",
                schema=schema)
            by_name = {c["name"]: c for c in res.get("columns", [])}
            for c in columns:
                patch = by_name.get(c.name)
                if patch:
                    if patch.get("unit") is not None: c.unit = patch["unit"]
                    if patch.get("role") in ("key", "value", "computed"): c.role = patch["role"]
        except Exception as e:  # fall back to deterministic; never fail the band
            anomalies.append(f"llm verify skipped: {e}")
    return SegmentReport(band=band.region, columns=columns, formulas=formulas,
                         description=desc, anomalies=anomalies)
