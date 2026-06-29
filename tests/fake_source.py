"""Dict-backed WorkbookSource for formula tests (cached values + formula strings)."""


class FakeSource:
    """One sheet. `values`/`formulas` are {(row, col): cell}. read_formula_region
    overlays formula strings onto values (matches openpyxl data_only=False)."""

    def __init__(self, sheet, values, formulas):
        self._sheet, self._values, self._formulas = sheet, values, formulas
        self.path = None

    def sheet_names(self):
        return [self._sheet]

    @staticmethod
    def _grid(store, min_row, min_col, max_row, max_col):
        if not store:
            return []
        r0 = min_row or min(r for r, _ in store)
        r1 = max_row or max(r for r, _ in store)
        c0 = min_col or min(c for _, c in store)
        c1 = max_col or max(c for _, c in store)
        return [tuple(store.get((r, c)) for c in range(c0, c1 + 1))
                for r in range(r0, r1 + 1)]

    def read_region(self, sheet, min_row=None, min_col=None, max_row=None, max_col=None):
        return self._grid(self._values, min_row, min_col, max_row, max_col)

    def read_cell(self, sheet, row, col):
        return self._values.get((row, col))

    def read_formula_region(self, sheet, min_row=None, min_col=None, max_row=None, max_col=None):
        merged = dict(self._values)
        merged.update(self._formulas)
        return self._grid(merged, min_row, min_col, max_row, max_col)


def vertical_fake():
    """Units | Price | Revenue(=A*B). Header row 1, data rows 2-4, Units unique keys."""
    values = {(1, 1): "Units", (1, 2): "Price", (1, 3): "Revenue"}
    formulas = {}
    for r in range(2, 5):
        values[(r, 1)] = r            # Units (unique -> usable as key)
        values[(r, 2)] = 10           # Price
        values[(r, 3)] = r * 10       # Revenue cached value (recalculated)
        formulas[(r, 3)] = f"=A{r}*B{r}"
    return FakeSource("Sheet1", values, formulas)
