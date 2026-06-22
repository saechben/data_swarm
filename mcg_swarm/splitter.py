from __future__ import annotations
import datetime as _dt
from dataclasses import dataclass, field
import openpyxl
from openpyxl.utils import get_column_letter
from mcg_swarm.schemas import ColumnSpec


@dataclass
class TableHandle:
    sheet: str
    region: str
    header_row: int
    columns: list[ColumnSpec] = field(default_factory=list)
    ambiguous: bool = False
    reason: str = ""


def _infer_dtype(samples: list) -> str:
    vals = [v for v in samples if v not in (None, "")]
    if not vals:
        return "string"
    if all(isinstance(v, bool) for v in vals):
        return "boolean"
    if all(isinstance(v, _dt.datetime) for v in vals):
        return "date"

    def isnum(v):
        try:
            float(v)
            return not isinstance(v, bool)
        except (TypeError, ValueError):
            return False

    if all(isnum(v) for v in vals):
        return "number"
    return "string"


def _is_header_candidate(row: tuple, rows_after: list[tuple]) -> bool:
    """Return True if this row looks like a real table header (mostly strings, followed immediately by a data row)."""
    nonempty = [c for c in row if c not in (None, "")]
    if not nonempty:
        return False
    strings = [c for c in nonempty if isinstance(c, str)]
    if len(strings) < max(1, len(nonempty) // 2):
        return False
    # Require that the very next non-empty row exists (data must follow directly or after a gap,
    # but a single-cell row with a blank separator below is likely a title banner, not a header)
    has_data_after = any(any(c not in (None, "") for c in r) for r in rows_after)
    if not has_data_after:
        return False
    # If this candidate has only 1 non-empty cell, it is a title banner if:
    #   (a) the very next row is blank (existing case), OR
    #   (b) the very next row has strictly MORE non-empty cells (merged-title banner directly
    #       above the real header — openpyxl gives None for the non-anchor merged cells).
    if len(nonempty) == 1 and rows_after:
        next_row_nonempty = [c for c in rows_after[0] if c not in (None, "")]
        if all(c in (None, "") for c in rows_after[0]):
            # case (a): blank separator row below
            return False
        if len(next_row_nonempty) > 1:
            # case (b): wider row immediately below — this row is a title banner
            return False
    return True


def _is_title_banner(row: tuple, rows_after: list[tuple]) -> bool:
    """Return True if this row is a title banner that should be skipped (not ambiguous content)."""
    nonempty = [c for c in row if c not in (None, "")]
    if len(nonempty) != 1:
        return False
    if not rows_after:
        return False
    # Case (a): single cell followed by a blank row
    if all(c in (None, "") for c in rows_after[0]):
        return True
    # Case (b): single cell immediately above a wider row (merged title banner)
    next_nonempty = [c for c in rows_after[0] if c not in (None, "")]
    if len(next_nonempty) > 1:
        return True
    return False


def detect_table(ws) -> TableHandle:
    rows = list(ws.iter_rows(values_only=True))
    # find first non-empty row that is mostly strings and has a data row after it
    header_idx = None
    banner_rows: set[int] = set()
    for i, row in enumerate(rows):
        # Track title banner rows so they don't trigger "ambiguous" later
        if _is_title_banner(row, rows[i + 1:]):
            banner_rows.add(i)
            continue
        if _is_header_candidate(row, rows[i + 1:]):
            header_idx = i
            break
    if header_idx is None:
        return TableHandle(ws.title, "A1:A1", 1, [], ambiguous=True,
                           reason="no header row with data below")
    header = rows[header_idx]
    # extent: columns spanned by header; rows until a fully-empty row
    last_col = max((j for j, c in enumerate(header) if c not in (None, "")), default=0)
    end_idx = header_idx
    for r in range(header_idx + 1, len(rows)):
        if all(c in (None, "") for c in rows[r][: last_col + 1]):
            break
        end_idx = r

    # Trim empty leading columns: find the first column index that has any content
    # in the header or data rows (so left-offset tables don't get phantom "A" columns).
    data = rows[header_idx + 1: end_idx + 1]
    all_table_rows = [header] + list(data)

    first_col = 0
    for j in range(last_col + 1):
        if any(j < len(r) and r[j] not in (None, "") for r in all_table_rows):
            first_col = j
            break

    # Trim empty trailing columns: last column with content in any table row.
    last_col_trimmed = last_col
    for j in range(last_col, first_col - 1, -1):
        if any(j < len(r) and r[j] not in (None, "") for r in all_table_rows):
            last_col_trimmed = j
            break

    start_col_letter = get_column_letter(first_col + 1)
    end_col_letter = get_column_letter(last_col_trimmed + 1)
    region = f"{start_col_letter}{header_idx + 1}:{end_col_letter}{end_idx + 1}"

    cols = []
    for j in range(first_col, last_col_trimmed + 1):
        name = header[j] if j < len(header) and header[j] not in (None, "") else get_column_letter(j + 1)
        samples = [r[j] if j < len(r) else None for r in data[:20]]
        cols.append(ColumnSpec(name=str(name), dtype=_infer_dtype(samples),
                               role="key" if j == first_col else "value"))

    # Non-empty rows above the header that are NOT recognised title banners make it ambiguous.
    ambiguous = header_idx > 0 and any(
        k not in banner_rows and any(c not in (None, "") for c in rows[k])
        for k in range(header_idx))
    return TableHandle(ws.title, region, header_idx + 1, cols,
                       ambiguous=ambiguous,
                       reason="content above header row" if ambiguous else "")


def split_workbook(path: str) -> list[TableHandle]:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        return [detect_table(wb[name]) for name in wb.sheetnames]
    finally:
        wb.close()
