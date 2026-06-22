# mcg_swarm/extraction.py
from __future__ import annotations
import openpyxl
from openpyxl.utils import get_column_letter
from eval.util import range_box
from mcg_swarm.schemas import ExtractedValue


class ExtractionIndex:
    def __init__(self, path, sheet, region, header_row, columns, row_key):
        self.path, self.sheet = path, sheet
        self.columns = {c.name: c for c in columns}
        self.row_key = row_key
        min_row, min_col, max_row, max_col = range_box(region)
        self._min_col = min_col

        # Build-time scan: open workbook once to precompute dicts (O(1) resolution).
        # We close it immediately — live reads reopen per call (correctness over speed).
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        try:
            ws = wb[sheet]
            grid = list(ws.iter_rows(min_row=min_row, max_row=max_row,
                                     min_col=min_col, max_col=max_col, values_only=True))
        finally:
            wb.close()

        header = grid[0]
        # column name -> absolute (1-based) column index
        self._col_to_phys: dict[str, int] = {
            str(name): min_col + j
            for j, name in enumerate(header)
            if name not in (None, "")
        }
        # row key value -> absolute (1-based) row index
        self._key_to_phys: dict = {}
        key_cols = [self._col_to_phys[k] for k in row_key] if row_key else []
        for i, row in enumerate(grid[1:], start=header_row + 1):
            if row_key:
                vals = tuple(row[kc - min_col] for kc in key_cols)
                key = vals[0] if len(vals) == 1 else vals
            else:
                key = i - header_row  # positional 1-based
            self._key_to_phys[key] = i

    def _read(self, phys_row: int, phys_col: int):
        """Open a fresh workbook handle per read to reflect edits without rebuilding index."""
        wb = openpyxl.load_workbook(self.path, data_only=True, read_only=True)
        try:
            ws = wb[self.sheet]
            return ws.cell(row=phys_row, column=phys_col).value
        finally:
            wb.close()

    def query(self, row, column) -> ExtractedValue:
        if column not in self._col_to_phys:
            raise KeyError(f"unknown column: {column!r}")
        if row not in self._key_to_phys:
            raise KeyError(f"unknown row key: {row!r}")
        pc, pr = self._col_to_phys[column], self._key_to_phys[row]
        spec = self.columns.get(column)
        return ExtractedValue(
            value=self._read(pr, pc),
            dtype=spec.dtype if spec else "string",
            unit=spec.unit if spec else None,
            sheet=self.sheet,
            cell_ref=f"{get_column_letter(pc)}{pr}",
            is_computed=bool(spec and spec.role == "computed"),
        )

    def query_cell(self, a1) -> ExtractedValue:
        from openpyxl.utils import coordinate_to_tuple
        r, c = coordinate_to_tuple(a1)
        return ExtractedValue(
            value=self._read(r, c),
            dtype="number",
            unit=None,
            sheet=self.sheet,
            cell_ref=a1,
            is_computed=False,
        )

    def query_range(self, a1) -> list[ExtractedValue]:
        min_row, min_col, max_row, max_col = range_box(a1)
        out = []
        wb = openpyxl.load_workbook(self.path, data_only=True, read_only=True)
        try:
            ws = wb[self.sheet]
            for r in range(min_row, max_row + 1):
                for c in range(min_col, max_col + 1):
                    out.append(ExtractedValue(
                        value=ws.cell(row=r, column=c).value,
                        dtype="number",
                        unit=None,
                        sheet=self.sheet,
                        cell_ref=f"{get_column_letter(c)}{r}",
                        is_computed=False,
                    ))
        finally:
            wb.close()
        return out

    def row_keys(self) -> list:
        return list(self._key_to_phys.keys())

    def column_names(self) -> list[str]:
        return list(self._col_to_phys.keys())


def build_index(path, handle, row_key) -> ExtractionIndex:
    return ExtractionIndex(
        path, handle.sheet, handle.region, handle.header_row, handle.columns, row_key
    )
