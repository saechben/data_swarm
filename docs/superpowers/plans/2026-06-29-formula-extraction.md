# Formula Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate `CanonicalTable.formulas` from in-cell Excel formulas — with an executable operand binding and a human-readable context hint — so downstream agents can see how computed values are derived, and the quality gate's dormant computed-column self-check re-activates.

**Architecture:** A new per-table orchestrator step reads raw formula strings via a new `WorkbookSource.read_formula_region` method, translates same-row in-table arithmetic into the engine's named-operand grammar (`=B2*C2` → `Units*Price`), and emits `TableFormula`s. Anything not translatable (cross-sheet, named ranges, transposed refs, unsupported functions) is captured-but-untranslated with a reason, never dropped. Only fully-translated targets get `role="computed"`, so the gate verifies exactly what we can evaluate.

**Tech Stack:** Python 3.11, pydantic v2, openpyxl, pytest. Existing modules: `mcg_swarm/{schemas,source,extraction,formulas,orchestrator,merge}.py`.

## Global Constraints

- **Never raises.** All failures surface in `provisional_notes` / `errors` lists — never an exception out of `extract_formulas` or the orchestrator (`orchestrator.py:5`).
- **Deterministic-first.** Translation and the baseline context gloss are pure/deterministic; LLM is optional enrichment only.
- **No `cell.coordinate` / `cell.column` access** in read-only mode — use `iter_rows(values_only=True)` and compute coordinates from offsets (the old `EmptyCell` crash, commit `b77195b`).
- **Phase 1 scope:** intra-table, same-row, vertical arithmetic only. Cross-sheet, named ranges, `SUMIF`, and transposed refs are captured-untranslated.
- **All Phase-1 operands are `source="column"`.** Row-wise `SUM(B2:E2)` is expanded to `(colB+colC+colD+colE)`; no `range`/`cell`/`param` operands are produced in Phase 1.
- `_Base` uses `extra="forbid"` (`schemas.py:6`) — new schema fields must be declared.
- Full suite baseline: 222 passed / 2 skipped. Must stay green.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `mcg_swarm/schemas.py` | add `TableFormula.context` field |
| `mcg_swarm/source.py` | add `read_formula_region` to Protocol + `OpenpyxlFileSource` + `SnapshotSource` |
| `mcg_swarm/extraction.py` | add `physical_columns()` + `data_row_numbers()` public accessors to `ExtractionIndex` |
| `mcg_swarm/formula_translate.py` | **new** — pure `translate_formula(excel, formula_row, col_by_letter)` A1→grammar translator |
| `mcg_swarm/formula_extract.py` | **new** — `extract_formulas(source, index, columns)` orchestration: scan, dedup, role upgrade, context, notes |
| `mcg_swarm/orchestrator.py` | call `extract_formulas` between §4 and §5; thread formulas + notes into §5/§7 tables |
| `tests/test_formula_translate.py` | **new** — unit tests for the pure translator |
| `tests/test_formula_extract.py` | **new** — unit + e2e tests for `extract_formulas` + accessors + `read_formula_region` |
| `tests/test_formula_gate_reactivation.py` | **new** — proves gate Phase 4 fires (correct passes, wrong fails) |

---

## Task 1: Add `TableFormula.context` field

**Files:**
- Modify: `mcg_swarm/schemas.py:19-23`
- Test: `tests/test_schemas.py` (append)

**Interfaces:**
- Produces: `TableFormula(target, expression, operands=[], ast=None, context=None)` — `context: Optional[str]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_schemas.py`:

```python
def test_table_formula_context_field():
    from mcg_swarm.schemas import TableFormula
    f = TableFormula(target="Total", expression="A+B", context="Total is A plus B")
    assert f.context == "Total is A plus B"
    # backward-compatible: context is optional and defaults to None
    g = TableFormula(target="Total", expression="A+B")
    assert g.context is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_schemas.py::test_table_formula_context_field -v`
Expected: FAIL — `ValidationError` (extra field `context` forbidden).

- [ ] **Step 3: Add the field**

In `mcg_swarm/schemas.py`, change the `TableFormula` class:

```python
class TableFormula(_Base):
    target: str
    expression: str
    operands: list[OperandBinding] = Field(default_factory=list)
    ast: Optional[dict] = None
    context: Optional[str] = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_schemas.py::test_table_formula_context_field -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/schemas.py tests/test_schemas.py
git commit -m "feat(schemas): add TableFormula.context for downstream hints"
```

---

## Task 2: Add `WorkbookSource.read_formula_region`

**Files:**
- Modify: `mcg_swarm/source.py` (Protocol at :11-16, `OpenpyxlFileSource` at :19-48, `SnapshotSource` at :51-74)
- Test: `tests/test_formula_extract.py` (create)

**Interfaces:**
- Produces: `source.read_formula_region(sheet, min_row=None, min_col=None, max_row=None, max_col=None) -> list[tuple]` — returns raw cell contents (formula strings like `"=B2*C2"` for formula cells, literal values otherwise) as a list of row tuples. Reads with `data_only=False`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_formula_extract.py`:

```python
import openpyxl
from mcg_swarm.source import OpenpyxlFileSource


def _write_vertical_formula_wb(path):
    """3-col table: Units | Price | Revenue(=A*B per row). Header row 1, data rows 2-4."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Units", "Price", "Revenue"])
    for r in range(2, 5):
        ws.cell(row=r, column=1, value=r)           # Units
        ws.cell(row=r, column=2, value=10)          # Price
        ws.cell(row=r, column=3, value=f"=A{r}*B{r}")  # Revenue formula
    wb.save(path)


def test_read_formula_region_returns_formula_strings(tmp_path):
    p = tmp_path / "vf.xlsx"
    _write_vertical_formula_wb(str(p))
    src = OpenpyxlFileSource(str(p))
    rows = src.read_formula_region("Sheet1", 2, 1, 4, 3)
    # row 2 (first data row): Units=2, Price=10, Revenue="=A2*B2"
    assert rows[0][2] == "=A2*B2"
    assert rows[2][2] == "=A4*B4"


def test_read_formula_region_empty_cells_no_crash(tmp_path):
    """Regression for the old EmptyCell cell.coordinate crash (commit b77195b)."""
    p = tmp_path / "empty.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["A", "B", "C"])
    ws.append([1, None, None])   # row 2 has empty cells
    wb.save(str(p))
    src = OpenpyxlFileSource(str(p))
    rows = src.read_formula_region("Sheet1", 1, 1, 2, 3)  # must not raise
    assert rows[1][0] == 1 and rows[1][1] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_formula_extract.py -v`
Expected: FAIL — `AttributeError: 'OpenpyxlFileSource' object has no attribute 'read_formula_region'`.

- [ ] **Step 3: Add the method to the Protocol and both impls**

In `mcg_swarm/source.py`, add to the `WorkbookSource` Protocol (after `read_cell`, line 16):

```python
    def read_formula_region(self, sheet: str, min_row: Optional[int] = None,
                            min_col: Optional[int] = None, max_row: Optional[int] = None,
                            max_col: Optional[int] = None) -> list[tuple]: ...
```

Add to `OpenpyxlFileSource` (after `read_cell`, line 48):

```python
    def read_formula_region(self, sheet, min_row=None, min_col=None, max_row=None, max_col=None):
        # data_only=False exposes formula strings; values_only=True avoids touching
        # cell.coordinate on EmptyCell objects (read-only mode crash, commit b77195b).
        wb = openpyxl.load_workbook(self.path, data_only=False, read_only=True)
        try:
            ws = wb[sheet]
            return [r for r in ws.iter_rows(
                min_row=min_row, max_row=max_row,
                min_col=min_col, max_col=max_col, values_only=True)]
        finally:
            wb.close()
```

Add to `SnapshotSource` (after `read_cell`, line 74) — formula strings are never cached, always delegate:

```python
    def read_formula_region(self, sheet, min_row=None, min_col=None, max_row=None, max_col=None):
        return self._inner.read_formula_region(sheet, min_row, min_col, max_row, max_col)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_formula_extract.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/source.py tests/test_formula_extract.py
git commit -m "feat(source): add read_formula_region (data_only=False, values_only)"
```

---

## Task 3: Add `ExtractionIndex` geometry accessors

**Files:**
- Modify: `mcg_swarm/extraction.py` (`ExtractionIndex`, after `query_range` ~line 108)
- Test: `tests/test_formula_extract.py` (append)

**Interfaces:**
- Consumes: `OpenpyxlFileSource`, `_write_vertical_formula_wb` (Task 2), `build_index`.
- Produces:
  - `index.physical_columns() -> dict[str, int]` — column name → absolute 1-based physical column.
  - `index.data_row_numbers() -> list[int]` — sorted absolute 1-based physical row numbers of data rows.

- [ ] **Step 1: Create the shared `FakeSource` test helper**

Create `tests/fake_source.py` — a dict-backed `WorkbookSource` reused by Tasks 3, 5,
6, and 7. It serves **both** cached values (`read_region`/`read_cell`) and formula
strings (`read_formula_region`), simulating a LibreOffice-recalculated workbook. This
is required because openpyxl-written formulas have **no cached value**, so a real tmp
fixture would read `None` for formula cells and break the gate for reasons unrelated
to this feature. pytest's default import mode puts each test file's directory on
`sys.path`, so sibling test files import it as `from fake_source import ...`.

```python
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
```

- [ ] **Step 1b: Write the failing accessor test**

Append to `tests/test_formula_extract.py`:

```python
from fake_source import FakeSource, vertical_fake


def test_index_geometry_accessors():
    from mcg_swarm.splitter import split_workbook
    from mcg_swarm.extraction import build_index
    src = vertical_fake()
    handle = split_workbook(src)[0]
    index = build_index(src, handle, row_key=[])
    cols = index.physical_columns()
    assert cols["Units"] == 1 and cols["Price"] == 2 and cols["Revenue"] == 3
    assert index.data_row_numbers() == [2, 3, 4]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_formula_extract.py::test_index_geometry_accessors -v`
Expected: FAIL — `AttributeError: 'ExtractionIndex' object has no attribute 'physical_columns'`.

- [ ] **Step 3: Add the accessors**

In `mcg_swarm/extraction.py`, add to `ExtractionIndex` (after the `query_range` method):

```python
    def physical_columns(self) -> dict:
        """Column name -> absolute 1-based physical column (copy; safe to mutate)."""
        return dict(self._col_to_phys)

    def data_row_numbers(self) -> list:
        """Sorted absolute 1-based physical row numbers of data rows."""
        return sorted(set(self._key_to_phys.values()))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_formula_extract.py::test_index_geometry_accessors -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/extraction.py tests/test_formula_extract.py tests/fake_source.py
git commit -m "feat(extraction): expose physical_columns/data_row_numbers accessors"
```

---

## Task 4: Pure A1→grammar translator (`formula_translate.py`)

**Files:**
- Create: `mcg_swarm/formula_translate.py`
- Test: `tests/test_formula_translate.py` (create)

**Interfaces:**
- Consumes: `mcg_swarm.schemas.OperandBinding`, `mcg_swarm.formulas.parse_ast`, `mcg_swarm.formulas.FORMULA_FUNCS`.
- Produces:
  `translate_formula(excel: str, formula_row: int, col_by_letter: dict[str, str]) -> tuple[Optional[str], list[OperandBinding], Optional[str]]`
  - On success: `(expression, operands, None)` where `expression` is engine grammar and `operands` are all `source="column"`.
  - On failure: `(None, [], reason)` — `reason` is a short human-readable cause.
  - `col_by_letter` maps an in-table column LETTER (e.g. `"B"`) → column NAME (e.g. `"Price"`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_formula_translate.py`:

```python
from mcg_swarm.formula_translate import translate_formula

COL = {"A": "Units", "B": "Price", "C": "Revenue", "D": "Discount"}


def test_same_row_product():
    expr, ops, reason = translate_formula("=A2*B2", 2, COL)
    assert reason is None
    assert expr == "Units*Price"
    assert {(o.name, o.source, o.ref) for o in ops} == {
        ("Units", "column", "Units"), ("Price", "column", "Price")}


def test_same_row_subtraction_three_cols():
    expr, ops, reason = translate_formula("=A2-B2-D2", 2, COL)
    assert reason is None
    assert expr == "Units-Price-Discount"
    assert len(ops) == 3


def test_sum_range_expands_to_addition():
    expr, ops, reason = translate_formula("=SUM(A2:C2)", 2, COL)
    assert reason is None
    assert expr == "(Units+Price+Revenue)"
    assert {o.name for o in ops} == {"Units", "Price", "Revenue"}


def test_cross_sheet_bails():
    expr, ops, reason = translate_formula("=Inputs!B2*A2", 2, COL)
    assert expr is None and ops == []
    assert "cross-sheet" in reason.lower()


def test_named_range_bails():
    expr, ops, reason = translate_formula("=A2*TaxRate", 2, COL)
    assert expr is None and ops == []
    assert reason  # non-empty cause (unknown/named reference)


def test_transposed_different_row_bails():
    # =A2*A3 references two rows of the SAME column -> not same-row -> untranslatable
    expr, ops, reason = translate_formula("=A2*A3", 2, COL)
    assert expr is None and ops == []
    assert reason


def test_out_of_table_column_bails():
    # Z2 is not an in-table column
    expr, ops, reason = translate_formula("=A2*Z2", 2, COL)
    assert expr is None and ops == []
    assert reason
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_formula_translate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcg_swarm.formula_translate'`.

- [ ] **Step 3: Implement the translator**

Create `mcg_swarm/formula_translate.py`:

```python
"""Pure translator: in-cell Excel formula string -> engine-grammar expression +
column-operand bindings. Phase 1: same-row, in-table arithmetic only. Any reference
that is cross-sheet, a named range, a different row (transposed), or an out-of-table
column makes the whole formula untranslatable (returns a reason; never raises)."""
from __future__ import annotations

import ast as _ast
import re
from typing import Optional

from mcg_swarm.schemas import OperandBinding
from mcg_swarm.formulas import parse_ast, FORMULA_FUNCS

_A1 = re.compile(r"\$?([A-Z]{1,3})\$?([0-9]+)")
_SUM_RANGE = re.compile(r"SUM\(\s*\$?([A-Z]{1,3})\$?([0-9]+)\s*:\s*\$?([A-Z]{1,3})\$?([0-9]+)\s*\)")
_ALLOWED_FUNCS = set(FORMULA_FUNCS) | {"IF"}


def _col_to_idx(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


def translate_formula(excel: str, formula_row: int, col_by_letter: dict) -> tuple:
    """Return (expression, operands, reason). reason is None on success."""
    expr = excel.strip()
    if expr.startswith("="):
        expr = expr[1:]
    if "!" in expr:
        return None, [], "cross-sheet reference"

    idx_to_letter = {_col_to_idx(L): L for L in col_by_letter}
    operands: dict[str, OperandBinding] = {}

    def _add(name):
        operands[name] = OperandBinding(name=name, source="column", ref=name)
        return name

    # 1) Expand same-row horizontal SUM(start:end) into (a+b+c). Vertical or
    #    out-of-table ranges bail. Each expanded column is registered as an operand.
    def _expand_sum(m):
        c1, r1, c2, r2 = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
        if r1 != formula_row or r2 != formula_row:
            raise _Bail("SUM range is not on the formula's row (multi-row/transposed)")
        i1, i2 = _col_to_idx(c1), _col_to_idx(c2)
        if i1 > i2:
            i1, i2 = i2, i1
        names = []
        for i in range(i1, i2 + 1):
            if i not in idx_to_letter:
                raise _Bail("SUM range spans an out-of-table column")
            names.append(_add(col_by_letter[idx_to_letter[i]]))
        return "(" + "+".join(names) + ")"

    def _sub_ref(m):
        letters, row = m.group(1), int(m.group(2))
        if row != formula_row:
            raise _Bail("reference is not on the same row (transposed/multi-row)")
        if letters not in col_by_letter:
            raise _Bail(f"reference {letters}{row} is not an in-table column")
        return _add(col_by_letter[letters])

    try:
        # 1) expand SUM ranges first, then 2) replace remaining single A1 refs.
        expr = _SUM_RANGE.sub(_expand_sum, expr)
        expr = _A1.sub(_sub_ref, expr)
    except _Bail as b:
        return None, [], str(b)

    # 3) Validate the rewritten expression: must parse, every Name must be an
    #    operand, every Call must be an allowed function. Catches named ranges
    #    (bare identifiers) and disallowed functions.
    try:
        tree = _ast.parse(expr, mode="eval")
    except SyntaxError:
        return None, [], "unparseable expression"
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Name) and node.id not in operands:
            return None, [], f"unknown reference: {node.id}"
        if isinstance(node, _ast.Call):
            if not (isinstance(node.func, _ast.Name) and node.func.id in _ALLOWED_FUNCS):
                return None, [], "disallowed function"
    if not operands:
        return None, [], "no in-table column references"

    parse_ast(expr)  # sanity: serialisable (raises only on unsupported nodes)
    return expr, list(operands.values()), None


class _Bail(Exception):
    """Internal control-flow signal for an untranslatable reference."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_formula_translate.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/formula_translate.py tests/test_formula_translate.py
git commit -m "feat: pure Excel-formula -> engine-grammar translator (Phase 1)"
```

---

## Task 5: `extract_formulas` orchestration (`formula_extract.py`)

**Files:**
- Create: `mcg_swarm/formula_extract.py`
- Test: `tests/test_formula_extract.py` (append)

**Interfaces:**
- Consumes: `translate_formula` (Task 4), `index.physical_columns()` / `index.data_row_numbers()` (Task 3), `source.read_formula_region` (Task 2), `ColumnSpec`, `TableFormula`.
- Produces:
  `extract_formulas(source, index, columns: list[ColumnSpec], scan_limit: int = 20) -> tuple[list[TableFormula], list[str]]`
  - Returns `(formulas, notes)`. Mutates `role` to `"computed"` in place on any `ColumnSpec` in `columns` whose formula fully translated.
  - Builds `context` for every formula (deterministic gloss). Never raises.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_formula_extract.py` (`FakeSource`/`_vertical_fake` come from
Task 3):

```python
def _make_index(src, row_key=None):
    from mcg_swarm.splitter import split_workbook
    from mcg_swarm.extraction import build_index
    handle = split_workbook(src)[0]
    index = build_index(src, handle, row_key=row_key or [])
    return handle, index


def test_extract_vertical_formula_translates_and_upgrades_role():
    from mcg_swarm.formula_extract import extract_formulas
    src = vertical_fake()
    handle, index = _make_index(src)
    columns = list(handle.columns)
    formulas, notes = extract_formulas(src, index, columns)
    rev = next(f for f in formulas if f.target == "Revenue")
    assert rev.expression == "Units*Price"
    assert {o.name for o in rev.operands} == {"Units", "Price"}
    assert rev.context and "Revenue" in rev.context
    # role upgraded in place on the passed ColumnSpec
    assert next(c for c in columns if c.name == "Revenue").role == "computed"


def test_extract_cross_sheet_captured_untranslated():
    from mcg_swarm.formula_extract import extract_formulas
    values = {(1, 1): "A", (1, 2): "B", (1, 3): "C"}
    formulas_in = {}
    for r in range(2, 5):
        values[(r, 1)] = r
        values[(r, 2)] = 2
        values[(r, 3)] = 99               # arbitrary cached value
        formulas_in[(r, 3)] = f"='Inputs'!A1*A{r}"   # cross-sheet -> untranslatable
    src = FakeSource("Sheet1", values, formulas_in)
    handle, index = _make_index(src)
    columns = list(handle.columns)
    formulas, notes = extract_formulas(src, index, columns)
    c = next(f for f in formulas if f.target == "C")
    assert c.operands == []                       # not translated
    assert c.context                              # reason present
    assert next(col for col in columns if col.name == "C").role != "computed"
    assert any("C" in n for n in notes)           # provisional note recorded


def test_extract_no_formulas_returns_empty():
    from mcg_swarm.formula_extract import extract_formulas
    values = {(1, 1): "A", (1, 2): "B"}
    for r in range(2, 5):
        values[(r, 1)] = r
        values[(r, 2)] = r * 2
    src = FakeSource("Sheet1", values, {})
    handle, index = _make_index(src)
    formulas, notes = extract_formulas(src, index, list(handle.columns))
    assert formulas == [] and notes == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_formula_extract.py -k extract -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcg_swarm.formula_extract'`.

- [ ] **Step 3: Implement `extract_formulas`**

Create `mcg_swarm/formula_extract.py`:

```python
"""Per-table formula extraction step. Reads in-cell formula strings, translates
same-row in-table arithmetic into the engine grammar, and emits TableFormulas with
a downstream context hint. Untranslatable formulas are captured (empty operands +
reason) and recorded as provisional notes; only fully-translated targets get
role='computed'. Never raises."""
from __future__ import annotations

from openpyxl.utils import get_column_letter

from mcg_swarm.schemas import ColumnSpec, TableFormula
from mcg_swarm.formula_translate import translate_formula


def _gloss(target: str, operands, expression: str) -> str:
    names = [o.name for o in operands]
    if names:
        return (f"{target} is computed as {expression} "
                f"(same-row columns: {', '.join(names)}).")
    return f"{target} holds an Excel formula that could not be translated."


def extract_formulas(source, index, columns: list, scan_limit: int = 20) -> tuple:
    """Return (formulas, notes). Mutates role='computed' on translated targets."""
    try:
        col_phys = index.physical_columns()                 # name -> abs col
        if not col_phys:
            return [], []
        phys_to_name = {c: n for n, c in col_phys.items()}
        col_by_letter = {get_column_letter(c): n for n, c in col_phys.items()}
        data_rows = index.data_row_numbers()
        if not data_rows:
            return [], []
        min_col, max_col = min(col_phys.values()), max(col_phys.values())
        scan_rows = data_rows[:scan_limit]
        grid = source.read_formula_region(
            index.sheet, scan_rows[0], min_col, scan_rows[-1], max_col)

        by_col = {c.name: c for c in columns}
        seen: set = set()
        formulas: list = []
        notes: list = []

        for abs_row, row in zip(scan_rows, grid):
            for offset, val in enumerate(row):
                if not (isinstance(val, str) and val.startswith("=")):
                    continue
                phys_col = min_col + offset
                target = phys_to_name.get(phys_col)
                if target is None:
                    continue
                expression, operands, reason = translate_formula(
                    val, abs_row, col_by_letter)
                if expression is not None:
                    key = (target, expression)
                    if key in seen:
                        continue
                    seen.add(key)
                    formulas.append(TableFormula(
                        target=target, expression=expression, operands=operands,
                        context=_gloss(target, operands, expression)))
                    spec = by_col.get(target)
                    if isinstance(spec, ColumnSpec):
                        spec.role = "computed"
                else:
                    key = (target, val)
                    if key in seen:
                        continue
                    seen.add(key)
                    formulas.append(TableFormula(
                        target=target, expression=val, operands=[],
                        context=f"{target}: Excel formula not translated ({reason}); "
                                f"shown for reference."))
                    notes.append(f"untranslated formula in {target!r}: {reason}")
        return formulas, notes
    except Exception as exc:  # never raise from extraction
        return [], [f"formula extraction error: {exc}"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_formula_extract.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/formula_extract.py tests/test_formula_extract.py
git commit -m "feat: extract_formulas per-table step (translate, context, role upgrade)"
```

---

## Task 6: Wire `extract_formulas` into the orchestrator

**Files:**
- Modify: `mcg_swarm/orchestrator.py` (import near :12-19; insert step between :118 and :120; update §5 table :121-132 and §7 table :139-151)
- Test: `tests/test_formula_extract.py` (append e2e)

**Interfaces:**
- Consumes: `extract_formulas` (Task 5). `index` is already built at `orchestrator.py:118`; `merged.columns` are the same `ColumnSpec` objects held by `index`, so an in-place `role` upgrade is visible to the gate.
- Produces: `_orchestrate_core` returns a `CanonicalTable` whose `formulas` include extracted formulas and whose `provisional_notes` include extraction notes.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_formula_extract.py` (`_vertical_fake` from Task 3):

```python
def test_orchestrator_populates_formulas_endtoend():
    from mcg_swarm.splitter import split_workbook
    from mcg_swarm.orchestrator import _orchestrate_core
    src = vertical_fake()
    handle = split_workbook(src)[0]
    table = _orchestrate_core(src, handle, table_id="t0")
    rev = [f for f in table.formulas if f.target == "Revenue"]
    assert rev and rev[0].expression == "Units*Price"
    assert rev[0].context
    assert table.errors == []   # gate stays green (formula recomputes correctly)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_formula_extract.py::test_orchestrator_populates_formulas_endtoend -v`
Expected: FAIL — `table.formulas` is empty (assertion on `rev` fails).

- [ ] **Step 3: Wire it in**

In `mcg_swarm/orchestrator.py`, add the import after line 16:

```python
from mcg_swarm.formula_extract import extract_formulas
```

Replace the block from line 118 (`index = build_index(...)`) through the §5 table construction (ends line 132) with:

```python
        index = build_index(source, merged_handle, row_key=row_key)

        # §4.5  Extract in-cell formulas: translate same-row arithmetic, upgrade
        # role='computed' on translated targets (in place on merged.columns, which
        # `index` shares), capture the rest as provisional notes. Never raises.
        extracted_formulas, formula_notes = extract_formulas(
            source, index, merged.columns)
        all_formulas = list(merged.formulas) + extracted_formulas
        all_notes = list(merged.anomalies) + formula_notes

        # §5  Build intermediate CanonicalTable for testing
        table = CanonicalTable(
            table_id=table_id,
            sheet=handle.sheet,
            region=handle.region,
            header_row=handle.header_row,
            header_span=getattr(handle, "header_span", 1),
            columns=merged.columns,
            formulas=all_formulas,
            description=merged.description,
            provisional_notes=all_notes,
            extraction=ExtractionRef(script_name=f"idx_{table_id}", row_key=row_key),
        )
```

Then update the §7 return (lines 139-151) to use `all_formulas` and `all_notes`:

```python
        # §7  Return fully-populated CanonicalTable
        return CanonicalTable(
            table_id=table_id,
            sheet=handle.sheet,
            region=handle.region,
            header_row=handle.header_row,
            header_span=getattr(handle, "header_span", 1),
            columns=merged.columns,
            formulas=all_formulas,
            description=merged.description,
            provisional_notes=all_notes,
            extraction=ExtractionRef(script_name=f"idx_{table_id}", row_key=row_key),
            errors=errors,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_formula_extract.py::test_orchestrator_populates_formulas_endtoend -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/orchestrator.py tests/test_formula_extract.py
git commit -m "feat(orchestrator): per-table formula extraction step (§4.5)"
```

---

## Task 7: Prove gate Phase 4 self-check is live

**Files:**
- Test: `tests/test_formula_gate_reactivation.py` (create)

**Interfaces:**
- Consumes: `_orchestrate_core` (Task 6), `run_table_tests` (`quality_gate.py`), `CanonicalTable`, `build_index`.

This task adds no production code — it proves the dormant gate Phase 4 (`quality_gate.py:294-323`) now fires on extracted formulas: a correct formula passes, a deliberately wrong one fails. It uses the shared `FakeSource` (Task 3) so the gate's `live` read returns a real cached value (openpyxl cannot cache formula results).

- [ ] **Step 1: Write the test**

Create `tests/test_formula_gate_reactivation.py`:

```python
import dataclasses
from fake_source import vertical_fake
from mcg_swarm.splitter import split_workbook
from mcg_swarm.extraction import build_index
from mcg_swarm.quality_gate import run_table_tests
from mcg_swarm.orchestrator import _orchestrate_core


def test_correct_formula_passes_gate():
    src = vertical_fake()
    handle = split_workbook(src)[0]
    table = _orchestrate_core(src, handle, table_id="t0")
    # Revenue is role='computed' and gate Phase 4 recomputed it without failure.
    assert any(c.name == "Revenue" and c.role == "computed" for c in table.columns)
    assert table.errors == []


def test_wrong_formula_fails_gate():
    src = vertical_fake()
    handle = split_workbook(src)[0]
    table = _orchestrate_core(src, handle, table_id="t0")
    # Corrupt the Revenue formula to a wrong expression and re-run the gate directly.
    # pydantic v2 _Base is not frozen -> plain attribute assignment works.
    for f in table.formulas:
        if f.target == "Revenue":
            f.expression = "Units+Price"   # wrong: should be Units*Price
    row_key = [c.name for c in table.columns if c.role == "key"][:1]
    index = build_index(
        src, dataclasses.replace(handle, columns=table.columns), row_key=row_key)
    report = run_table_tests(src, table, index)
    assert not report.passed
    assert any("Revenue" in fail for fail in report.failures)
```

- [ ] **Step 2: Run the tests**

Run: `.venv/bin/pytest tests/test_formula_gate_reactivation.py -v`
Expected: PASS (both).

- [ ] **Step 3: Commit**

```bash
git add tests/test_formula_gate_reactivation.py
git commit -m "test: prove gate Phase 4 fires on extracted formulas"
```

---

## Task 8: Full-suite regression + real extreme workbook

**Files:**
- Test: `tests/test_formula_extract.py` (append a real-workbook smoke test)

**Interfaces:**
- Consumes: `_orchestrate_core`, the real transposed extreme workbook `eval/data/workbooks/formula_chain_pnl.xlsx`.

This task verifies the captured-untranslated path on a real transposed workbook (its `=B2*B3` refs hit the same-row guard and are recorded, not dropped) and that the whole suite stays green.

- [ ] **Step 1: Write the real-workbook smoke test**

Append to `tests/test_formula_extract.py`:

```python
import os


def test_real_transposed_workbook_captures_without_crash():
    """formula_chain_pnl is transposed (=B2*B3). Phase 1 captures these as
    untranslated (same-row guard) and must never crash or mark them computed."""
    wb_path = os.path.join("eval", "data", "workbooks", "formula_chain_pnl.xlsx")
    if not os.path.exists(wb_path):
        import pytest
        pytest.skip("extreme workbook not generated")
    from mcg_swarm.runner import run_swarm
    ext = run_swarm({"main": wb_path})
    # never raises; some table carries captured (untranslated) formulas or notes
    assert ext.tables  # extraction produced tables
    for t in ext.tables:
        for f in t.formulas:
            # any formula present is either translated (has operands) or captured
            assert f.context is not None
```

- [ ] **Step 2: Run the smoke test**

Run: `.venv/bin/pytest tests/test_formula_extract.py::test_real_transposed_workbook_captures_without_crash -v`
Expected: PASS (or SKIP if the workbook is not generated).

- [ ] **Step 3: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: All green — at least 222 passed (baseline) plus the new tests, 2 skipped. Zero failures.

- [ ] **Step 4: Commit**

```bash
git add tests/test_formula_extract.py
git commit -m "test: real transposed workbook captured-untranslated smoke + suite green"
```

---

## Self-Review Notes

- **Spec coverage:** §3 schema → Task 1; §5 `read_formula_region` → Task 2; §4 producer location + §6 algorithm → Tasks 4-6; §6 role upgrade → Task 5/6; §7 never-raises → wrapped in Task 5 + orchestrator §4.5; §8 testing → Tasks 4-8; contextualization (`context`) → Tasks 1, 5.
- **Deviation from spec (intentional, documented):** the `extract_formulas` signature drops the `orientation` parameter — the same-row guard in `translate_formula` rejects transposed/multi-row refs implicitly, which is simpler and equally correct (transposed → captured-untranslated, exactly the spec's intent). The `extract_formulas` signature is `(source, index, columns, scan_limit=20)` rather than the spec's `(source, sheet, region, columns, header_row, orientation)` — it reuses the index's already-computed geometry (Task 3 accessors) instead of re-deriving it, honoring the one-open / no-rework perf constraint. Phase-1 operands are all `source="column"` (SUM ranges are expanded to additive column operands) because the gate's `query_range` uses a fixed A1 ref that cannot track per-row.
- **Phase 2 interfaces unchanged:** cross-sheet/`cell`, named-range/`param`, and range operands extend `translate_formula` (new branches) and `OperandBinding` usage without touching `extract_formulas`'s scan/dedup loop or the orchestrator wiring.
- **Known Phase-1 limitation (safe-degrading):** `translate_formula` substitutes A1 refs with column *names*, then re-scans for remaining refs. A column whose header is itself shaped like an A1 ref (e.g. literally named `Q1`) would be re-matched by the A1 regex and the formula would fall back to captured-untranslated. This errs on the safe side (captured, never mistranslated) and is acceptable for Phase 1; a placeholder-substitution pass removes it in Phase 2 if real headers hit it.
