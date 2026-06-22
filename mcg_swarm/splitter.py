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
    # If this candidate has only 1 non-empty cell and the row immediately after is blank,
    # it looks like a title banner — not a real multi-column header.
    if len(nonempty) == 1 and rows_after and all(c in (None, "") for c in rows_after[0]):
        return False
    return True


def detect_table(ws) -> TableHandle:
    rows = list(ws.iter_rows(values_only=True))
    # find first non-empty row that is mostly strings and has a data row after it
    header_idx = None
    for i, row in enumerate(rows):
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
    region = f"A{header_idx + 1}:{get_column_letter(last_col + 1)}{end_idx + 1}"
    data = rows[header_idx + 1: end_idx + 1]
    cols = []
    for j in range(last_col + 1):
        name = header[j] if j < len(header) and header[j] not in (None, "") else get_column_letter(j + 1)
        samples = [r[j] if j < len(r) else None for r in data[:20]]
        cols.append(ColumnSpec(name=str(name), dtype=_infer_dtype(samples),
                               role="key" if j == 0 else "value"))
    ambiguous = header_idx > 0 and any(
        any(c not in (None, "") for c in rows[k]) for k in range(header_idx))
    return TableHandle(ws.title, region, header_idx + 1, cols,
                       ambiguous=ambiguous,
                       reason="content above header row" if ambiguous else "")


def split_workbook(path: str) -> list[TableHandle]:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        return [detect_table(wb[name]) for name in wb.sheetnames]
    finally:
        wb.close()
