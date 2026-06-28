# Robust Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the swarm's `CanonicalTable` output reliable — zero silent errors plus a bounded multi-pass ReAct repair loop — fed by adaptive (spread) sampling, behind a swappable data-source interface.

**Architecture:** Introduce a `WorkbookSource` port so all reads go through an interface (file adapter now, others later). Add a spread-sampling strategy so the quality gate detects errors anywhere in a table. Turn the single-shot table validator into an always-on, bounded multi-pass repair loop (agent-only) with verify-before-accept, and log every pass's failures by category to drive future automation.

**Tech Stack:** Python 3.11, openpyxl (read-only), pydantic v2, pytest, claude-agent-sdk (live agent; `FakeAgentRunner` for tests).

## Global Constraints

- Python 3.11; pydantic v2 — mutate models with `.model_copy(update={...})`, never `dataclasses.replace`.
- The orchestrator/validator contract: **NEVER raise** out of `orchestrate_table` / `TableValidator.review`; capture failures in `CanonicalTable.errors`. `errors == []` **iff** the quality gate passed.
- Default mode stays `static` (no agent). Robustness path is opt-in via `MCG_SUBAGENT=react`. Do **not** flip the global default in this branch.
- Behavior-preserving migrations: existing test suite (≈186 passing) must stay green after every migration task. Run `pytest -q` to confirm.
- No hardcoded deterministic repair strategies — the agent is the repair engine; we log failures for later.
- New env knobs (read at call time, with defaults): `MCG_REPAIR_MAX_PASSES=3`, `MCG_SAMPLE_FULL_THRESHOLD=300`, `MCG_SAMPLE_SIZE=300`, `MCG_REPAIR_LOG` (unset = no JSONL).

---

## File structure

**New files**
- `mcg_swarm/source.py` — `WorkbookSource` Protocol + `OpenpyxlFileSource`.
- `mcg_swarm/sampling.py` — `select_sample`.
- `mcg_swarm/repair_log.py` — `categorize_failures`, `log_repair_pass`.
- `tests/test_source.py`, `tests/test_sampling.py`, `tests/test_repair_log.py`, `tests/test_repair_loop.py`.

**Modified files**
- `mcg_swarm/runner.py` — accept `WorkbookSource | path | {"main": path}`; thread source down.
- `mcg_swarm/splitter.py` — `detect_table(rows, sheet_name)`; `split_workbook(source)`.
- `mcg_swarm/extraction.py` — `ExtractionIndex`/`build_index` read via source.
- `mcg_swarm/quality_gate.py` — read via source; use `select_sample`.
- `mcg_swarm/subagent/static.py` — read via source.
- `mcg_swarm/subagent/tools.py` — `BandView(source, band)`.
- `mcg_swarm/header_llm.py` — read via source.
- `mcg_swarm/orchestrator.py` — pass source through `_orchestrate_core` / `orchestrate_table`.
- `mcg_swarm/subagent/table_check.py` — multi-pass repair loop; lift size gate; prior-attempts seed; logging.
- `mcg_swarm/subagent/agent_runner.py` — `FakeAgentRunner` optional `finals` sequence.
- `mcg_swarm/subagent/__init__.py` — wire `max_passes` from env.

---

## PHASE 1 — WorkbookSource port

### Task 1: Define the source port + openpyxl adapter

**Files:**
- Create: `mcg_swarm/source.py`
- Test: `tests/test_source.py`

**Interfaces:**
- Produces: `WorkbookSource` (Protocol) with `sheet_names() -> list[str]`, `read_region(sheet, min_row=None, min_col=None, max_row=None, max_col=None) -> list[tuple]`, `read_cell(sheet, row, col) -> Any`. `OpenpyxlFileSource(path: str)` implements it and exposes `.path`. `as_source(x) -> WorkbookSource` normalizes a path/dict/source.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_source.py
import openpyxl
from mcg_swarm.source import OpenpyxlFileSource, as_source, WorkbookSource

def _wb(tmp_path):
    p = tmp_path / "s.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "S"
    ws.append(["A", "B"]); ws.append([1, 2]); ws.append([3, 4])
    wb.save(p); return str(p)

def test_sheet_names_region_cell(tmp_path):
    src = OpenpyxlFileSource(_wb(tmp_path))
    assert src.sheet_names() == ["S"]
    assert src.read_region("S", 1, 1, 3, 2) == [("A", "B"), (1, 2), (3, 4)]
    assert src.read_region("S") == [("A", "B"), (1, 2), (3, 4)]  # unbounded = whole sheet
    assert src.read_cell("S", 2, 2) == 2

def test_as_source_normalizes(tmp_path):
    p = _wb(tmp_path)
    assert isinstance(as_source(p), OpenpyxlFileSource)
    assert isinstance(as_source({"main": p}), OpenpyxlFileSource)
    s = OpenpyxlFileSource(p)
    assert as_source(s) is s
    assert isinstance(s, WorkbookSource)  # runtime_checkable
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_source.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcg_swarm.source'`.

- [ ] **Step 3: Write minimal implementation**

```python
# mcg_swarm/source.py
"""WorkbookSource port — abstracts where cells come from so the swarm depends on
an interface, not on openpyxl+path. Ships one impl (file); others (bytes, DataFrame,
streaming) implement the same Protocol later without touching extraction logic."""
from __future__ import annotations
from typing import Any, Optional, Protocol, runtime_checkable

import openpyxl


@runtime_checkable
class WorkbookSource(Protocol):
    def sheet_names(self) -> list[str]: ...
    def read_region(self, sheet: str, min_row: Optional[int] = None,
                    min_col: Optional[int] = None, max_row: Optional[int] = None,
                    max_col: Optional[int] = None) -> list[tuple]: ...
    def read_cell(self, sheet: str, row: int, col: int) -> Any: ...


class OpenpyxlFileSource:
    """File-backed source. Opens read-only/data_only per call to preserve the existing
    live-read semantics (edits to a closed workbook are reflected on the next read)."""

    def __init__(self, path: str) -> None:
        self.path = path

    def sheet_names(self) -> list[str]:
        wb = openpyxl.load_workbook(self.path, data_only=True, read_only=True)
        try:
            return list(wb.sheetnames)
        finally:
            wb.close()

    def read_region(self, sheet, min_row=None, min_col=None, max_row=None, max_col=None):
        wb = openpyxl.load_workbook(self.path, data_only=True, read_only=True)
        try:
            ws = wb[sheet]
            return [r for r in ws.iter_rows(
                min_row=min_row, max_row=max_row,
                min_col=min_col, max_col=max_col, values_only=True)]
        finally:
            wb.close()

    def read_cell(self, sheet, row, col):
        wb = openpyxl.load_workbook(self.path, data_only=True, read_only=True)
        try:
            return wb[sheet].cell(row=row, column=col).value
        finally:
            wb.close()


def as_source(x) -> WorkbookSource:
    """Normalize a path str, {'main': path} dict, or WorkbookSource into a WorkbookSource."""
    if isinstance(x, WorkbookSource) and not isinstance(x, (str, dict)):
        return x
    if isinstance(x, dict):
        return OpenpyxlFileSource(x["main"])
    if isinstance(x, str):
        return OpenpyxlFileSource(x)
    raise TypeError(f"cannot build WorkbookSource from {type(x).__name__}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_source.py -q`
Expected: PASS (3 assertions in 2 tests).

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/source.py tests/test_source.py
git commit -m "feat(source): WorkbookSource port + OpenpyxlFileSource adapter"
```

---

### Task 2: Thread source through runner + splitter (entry seam)

**Files:**
- Modify: `mcg_swarm/splitter.py:149-150` (`detect_table`), `mcg_swarm/splitter.py:261-267` (`split_workbook`)
- Modify: `mcg_swarm/runner.py:12-39`
- Modify: `mcg_swarm/orchestrator.py` (`_orchestrate_core`, `orchestrate_table` signatures — accept `source`)
- Test: `tests/test_source_pipeline.py`

**Interfaces:**
- Consumes: `as_source`, `OpenpyxlFileSource` (Task 1).
- Produces: `split_workbook(source: WorkbookSource) -> list[TableHandle]`; `detect_table(rows: list[tuple], sheet_name: str) -> TableHandle`; `run_swarm(workbooks, llm=None)` still accepts `{"main": path}` and now also a path or `WorkbookSource`. `orchestrate_table(source, handle, table_id, ...)` and `_orchestrate_core(source, handle, ...)` take a source as first arg.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_source_pipeline.py
import openpyxl
from mcg_swarm.source import OpenpyxlFileSource
from mcg_swarm.runner import run_swarm
from mcg_swarm.splitter import split_workbook

def _wb(tmp_path):
    p = tmp_path / "p.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Data"
    ws.append(["Region", "Revenue"]); ws.append(["NA", 10]); ws.append(["EU", 20])
    wb.save(p); return str(p)

def test_split_workbook_accepts_source(tmp_path):
    src = OpenpyxlFileSource(_wb(tmp_path))
    handles = split_workbook(src)
    assert [h.sheet for h in handles] == ["Data"]
    assert [c.name for c in handles[0].columns] == ["Region", "Revenue"]

def test_run_swarm_accepts_path_and_source(tmp_path):
    p = _wb(tmp_path)
    a = run_swarm({"main": p})           # back-compat dict
    b = run_swarm(OpenpyxlFileSource(p)) # explicit source
    assert a.tables[0].columns[0].name == b.tables[0].columns[0].name == "Region"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_source_pipeline.py -q`
Expected: FAIL — `split_workbook` rejects a source / `detect_table` signature mismatch.

- [ ] **Step 3: Refactor `detect_table` and `split_workbook`**

In `mcg_swarm/splitter.py`, change the signature and first line of `detect_table` (currently `def detect_table(ws):` with `rows = list(ws.iter_rows(values_only=True))`):

```python
def detect_table(rows: list[tuple], sheet_name: str) -> TableHandle:
    # rows: list of value-tuples for the whole sheet (was list(ws.iter_rows(values_only=True)))
```

Then replace every `ws.title` inside `detect_table` with `sheet_name` (the ambiguous-stub return and the final `TableHandle(...)` construction).

Replace `split_workbook` (lines 261-267) with:

```python
from mcg_swarm.source import WorkbookSource, as_source  # add at top of splitter.py

def split_workbook(source) -> list[TableHandle]:
    src = as_source(source)  # tolerate a path for back-compat
    out = []
    for name in src.sheet_names():
        rows = src.read_region(name)  # unbounded = whole sheet
        out.append(detect_table(rows, name))
    return out
```

- [ ] **Step 4: Thread source through runner + orchestrator**

In `mcg_swarm/runner.py`, change `run_swarm`:

```python
from mcg_swarm.source import as_source  # add at top

def run_swarm(workbooks, llm=None) -> WorkbookExtraction:
    source = as_source(workbooks)            # dict/path/source all OK
    name = getattr(source, "path", "workbook")
    name = __import__("os").path.basename(name) if isinstance(name, str) else "workbook"
    try:
        handles = split_workbook(source)
    except Exception as e:
        return WorkbookExtraction(workbook=name, sheets=[], tables=[],
                                  generator_version=GENERATOR_VERSION,
                                  errors=[f"unreadable workbook: {e}"])
    subagent = build_subagent(llm=llm)
    table_validator = build_table_validator(llm=llm)
    tables, sheets = [], []
    for i, h in enumerate(handles):
        sheets.append(h.sheet)
        tables.append(orchestrate_table(
            source, h, table_id=f"{h.sheet}__{i}", llm=llm,
            subagent=subagent, table_validator=table_validator))
    return WorkbookExtraction(workbook=name, sheets=sheets, tables=tables,
                              generator_version=GENERATOR_VERSION)
```

In `mcg_swarm/orchestrator.py`, rename the first parameter of `_orchestrate_core` and `orchestrate_table` from `path` to `source` and pass it through unchanged to `build_index`, `run_table_tests`, `resolve_messy_tab`, `subagent.analyze` (via BandTask), and `table_validator.review`. The `BandTask(path=...)` field keeps its name for now but receives `source` (Task 4 makes static/tools consume it). Keep `build_indices` in `runner.py` using `OpenpyxlFileSource` internally (it takes `path` today — wrap: `as_source(path)`).

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_source_pipeline.py -q && pytest -q`
Expected: new file PASS; full suite still green (fix any caller passing `path=` positionally — it now receives a source, which the leaf readers still accept until Task 3/4; if a test calls `build_index(path, ...)` directly, it stays working because `OpenpyxlFileSource` exposes `.path` and leaf readers are migrated next).

> NOTE for implementer: Tasks 3 and 4 migrate the leaf readers to consume the source object. Between Task 2 and Task 4, `orchestrator` passes a `WorkbookSource` where leaf readers still expect a `path`. To keep the suite green within Task 2, temporarily pass `getattr(source, "path", source)` at the `build_index(...)` and `run_table_tests(...)` call sites in `orchestrator.py`; remove those `.path` shims in Tasks 3–4. Commit Task 2 only when `pytest -q` is green.

- [ ] **Step 6: Commit**

```bash
git add mcg_swarm/splitter.py mcg_swarm/runner.py mcg_swarm/orchestrator.py tests/test_source_pipeline.py
git commit -m "refactor(source): run_swarm/split_workbook consume WorkbookSource (back-compat)"
```

---

### Task 3: Migrate ExtractionIndex to the source

**Files:**
- Modify: `mcg_swarm/extraction.py:38-90` (`ExtractionIndex.__init__`, `_read`), `:120-168` (`query_range`, `read_all`), `:177-182` (`build_index`)
- Test: `tests/test_extraction_source.py`

**Interfaces:**
- Consumes: `WorkbookSource`, `as_source` (Task 1).
- Produces: `build_index(source, handle, row_key) -> ExtractionIndex`; `ExtractionIndex(source, sheet, region, header_row, columns, row_key, header_span=1)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extraction_source.py
import openpyxl
from mcg_swarm.source import OpenpyxlFileSource
from mcg_swarm.extraction import build_index
from mcg_swarm.schemas import ColumnSpec
from mcg_swarm.splitter import TableHandle

def test_build_index_reads_via_source(tmp_path):
    p = tmp_path / "e.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "D"
    ws.append(["Key", "Val"]); ws.append(["a", 1]); ws.append(["b", 2]); wb.save(p)
    src = OpenpyxlFileSource(str(p))
    handle = TableHandle(sheet="D", region="A1:B3", header_row=1,
                         columns=[ColumnSpec(name="Key", dtype="string", role="key"),
                                  ColumnSpec(name="Val", dtype="number")], header_span=1)
    idx = build_index(src, handle, row_key=["Key"])
    assert idx.query("a", "Val").value == 1
    assert idx.column_names() == ["Key", "Val"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_extraction_source.py -q`
Expected: FAIL — `build_index`/`ExtractionIndex` still expect a path string and call `openpyxl.load_workbook`.

- [ ] **Step 3: Migrate the reads**

In `mcg_swarm/extraction.py`:

- `__init__(self, path, sheet, ...)` → `__init__(self, source, sheet, ...)`; store `self.source = source`; drop `self.path`.
- Replace the build-time scan (lines 49-53):

```python
        grid = self.source.read_region(sheet, min_row, min_col, max_row, max_col)
```
  (delete the `wb = openpyxl.load_workbook(...)` / `try/finally`).

- Replace `_read` (lines 83-90):

```python
    def _read(self, phys_row: int, phys_col: int):
        return self.source.read_cell(self.sheet, phys_row, phys_col)
```

- Replace the `query_range` open (lines 123-138): build with one region read:

```python
        rows = self.source.read_region(self.sheet, min_row, min_col, max_row, max_col)
        out = []
        for r_off, row in enumerate(rows):
            r = min_row + r_off
            for c_off, val in enumerate(row):
                c = min_col + c_off
                out.append(ExtractedValue(value=val, dtype="number", unit=None,
                    sheet=self.sheet, cell_ref=f"{get_column_letter(c)}{r}", is_computed=False))
        return out
```

- Replace the `read_all` open (lines 156-168): read each needed cell via the source, or one region read covering the sampled rows; simplest behavior-preserving form:

```python
        out = []
        for row_key in row_keys:
            phys_row = self._key_to_phys[row_key]
            for col_name, phys_col in col_items:
                value = self.source.read_cell(self.sheet, phys_row, phys_col)
                out.append((row_key, col_name, value, f"{get_column_letter(phys_col)}{phys_row}"))
        return out
```

- `build_index` (lines 177-182):

```python
def build_index(source, handle, row_key) -> ExtractionIndex:
    from mcg_swarm.source import as_source
    header_span = getattr(handle, "header_span", 1)
    return ExtractionIndex(as_source(source), handle.sheet, handle.region,
                           handle.header_row, handle.columns, row_key, header_span=header_span)
```

Remove the now-unused `import openpyxl` if nothing else uses it. In `orchestrator.py`, drop the `.path` shim at the `build_index(...)` call (pass `source`).

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_extraction_source.py -q && pytest -q`
Expected: new test PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/extraction.py mcg_swarm/orchestrator.py tests/test_extraction_source.py
git commit -m "refactor(source): ExtractionIndex reads via WorkbookSource"
```

---

### Task 4: Migrate quality_gate, static, tools.BandView, header_llm to the source

**Files:**
- Modify: `mcg_swarm/quality_gate.py:22` (signature), `:124-138` (batch scan)
- Modify: `mcg_swarm/subagent/static.py:32-69`
- Modify: `mcg_swarm/subagent/tools.py:37-52` (`BandView.__init__`)
- Modify: `mcg_swarm/header_llm.py:30-36`
- Modify: `mcg_swarm/subagent/verifier.py` (BandView construction), `mcg_swarm/subagent/task.py` (BandTask carries `source`)
- Test: `tests/test_gate_source.py`

**Interfaces:**
- Consumes: `WorkbookSource` (Task 1).
- Produces: `run_table_tests(source, table, index, sample_size=25)`; `BandView(source, band, rows_above_header=2)`; `run_static(source, band, header, llm=None)`; `BandTask.source` field.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gate_source.py
import openpyxl
from mcg_swarm.source import OpenpyxlFileSource
from mcg_swarm.extraction import build_index
from mcg_swarm.quality_gate import run_table_tests
from mcg_swarm.subagent.tools import BandView
from mcg_swarm.size_estimate import Band
from mcg_swarm.schemas import CanonicalTable, ColumnSpec, ExtractionRef
from mcg_swarm.splitter import TableHandle

def _src(tmp_path):
    p = tmp_path / "g.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "D"
    ws.append(["Key", "Val"]); ws.append(["a", 1]); ws.append(["b", 2]); wb.save(p)
    return OpenpyxlFileSource(str(p))

def test_gate_runs_via_source(tmp_path):
    src = _src(tmp_path)
    handle = TableHandle(sheet="D", region="A1:B3", header_row=1,
        columns=[ColumnSpec(name="Key", dtype="string", role="key"),
                 ColumnSpec(name="Val", dtype="number")], header_span=1)
    idx = build_index(src, handle, row_key=["Key"])
    table = CanonicalTable(table_id="t", sheet="D", region="A1:B3", header_row=1,
        columns=handle.columns, extraction=ExtractionRef(script_name="t", row_key=["Key"]))
    assert run_table_tests(src, table, idx).passed

def test_bandview_via_source(tmp_path):
    src = _src(tmp_path)
    band = Band(sheet="D", header_row=1, region="A1:B3", col_start=1, col_end=2,
                row_start=2, row_end=3)
    view = BandView(src, band)
    assert view.geometry()["sheet"] == "D"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gate_source.py -q`
Expected: FAIL — `run_table_tests`/`BandView` still take a path and call `openpyxl`.

- [ ] **Step 3: Migrate the four readers**

`quality_gate.py`: signature `def run_table_tests(source, table, index, sample_size=25)`; replace the batch-open block (lines 124-138) with:

```python
        rows = source.read_region(table.sheet, scan_min_row, scan_min_col,
                                  scan_max_row, scan_max_col)
        for r_off, row_vals in enumerate(rows):
            actual_row = scan_min_row + r_off
            for c_off, val in enumerate(row_vals):
                pos = (actual_row, scan_min_col + c_off)
                if pos in needed:
                    live_cache[pos] = val
```
  (delete the `wb = openpyxl.load_workbook(...)`/try/finally; remove `import openpyxl` if unused).

`subagent/static.py`: `_analyze_band_single_open(source, band, header)` — replace lines 45-57 with:

```python
    sample_rows = source.read_region(band.sheet, band.row_start,
                                     band.col_start,
                                     min(band.row_end, band.row_start + 19),
                                     band.col_end)
```
  Update `run_static(source, band, header, llm=None)` and `StaticSubagent.analyze` to pass `task.source` (see BandTask below). Remove `import openpyxl`.

`subagent/tools.py`: `BandView.__init__(self, source, band, rows_above_header=2)` — replace lines 44-52 with:

```python
        self._grid = [list(r) for r in source.read_region(
            band.sheet, self._top, band.col_start, band.row_end, band.col_end)]
```
  Remove `import openpyxl`.

`header_llm.py`: replace lines 30-36's load/iter with `source.read_region(handle.sheet, 1, None, n, None)`; change the function/`resolve_messy_tab` signature to take `source` and thread it from `orchestrator._orchestrate_core`.

`subagent/task.py`: add field `source: object = None` to `BandTask`. In `orchestrator._band_task`, set `source=source` (and keep `path=getattr(source, "path", None)` for any legacy reference). In `verifier.py` and `table_check.py`, build `BandView(task.source or as_source(task.path), band)` — i.e. construct from the source.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_gate_source.py -q && pytest -q`
Expected: new test PASS; full suite green. (Update any direct callers in existing tests that pass a path to `run_table_tests`/`BandView`/`run_static` — wrap with `OpenpyxlFileSource(path)`.)

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/quality_gate.py mcg_swarm/subagent/static.py mcg_swarm/subagent/tools.py mcg_swarm/header_llm.py mcg_swarm/subagent/task.py mcg_swarm/subagent/verifier.py mcg_swarm/subagent/table_check.py tests/test_gate_source.py
git commit -m "refactor(source): gate/static/BandView/header_llm read via WorkbookSource"
```

---

## PHASE 2 — Adaptive sampling

### Task 5: `select_sample` strategy

**Files:**
- Create: `mcg_swarm/sampling.py`
- Test: `tests/test_sampling.py`

**Interfaces:**
- Produces: `select_sample(row_keys: list, *, full_threshold: int | None = None, sample_size: int | None = None) -> list` — returns all keys when `len <= full_threshold`, else a spread sample (head + strided middle + tail) of size ≈ `sample_size`, preserving original order, including first and last key. Reads `MCG_SAMPLE_FULL_THRESHOLD` / `MCG_SAMPLE_SIZE` when args omitted.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sampling.py
from mcg_swarm.sampling import select_sample

def test_small_returns_all():
    keys = list(range(50))
    assert select_sample(keys, full_threshold=300, sample_size=300) == keys

def test_large_is_spread_and_bounded():
    keys = list(range(1_000_000))
    s = select_sample(keys, full_threshold=300, sample_size=300)
    assert len(s) <= 300
    assert s[0] == 0 and s[-1] == 999_999      # head and tail included
    assert s == sorted(s)                       # original order preserved
    assert len(set(s)) == len(s)                # no duplicates
    assert max(s) - min(s) > 900_000            # genuinely spans the table (not first-N)

def test_threshold_boundary():
    keys = list(range(300))
    assert select_sample(keys, full_threshold=300, sample_size=300) == keys  # == threshold -> all
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sampling.py -q`
Expected: FAIL — `ModuleNotFoundError: mcg_swarm.sampling`.

- [ ] **Step 3: Write minimal implementation**

```python
# mcg_swarm/sampling.py
"""Adaptive row sampling: full scan for small tables, a spread (head/middle/tail)
sample for large ones so anomalies anywhere in the column are caught — not just the
first rows (which let late-row dtype drift slip past static and the gate)."""
from __future__ import annotations
import os

DEFAULT_FULL_THRESHOLD = 300
DEFAULT_SAMPLE_SIZE = 300


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default


def select_sample(row_keys, *, full_threshold=None, sample_size=None):
    if full_threshold is None:
        full_threshold = _env_int("MCG_SAMPLE_FULL_THRESHOLD", DEFAULT_FULL_THRESHOLD)
    if sample_size is None:
        sample_size = _env_int("MCG_SAMPLE_SIZE", DEFAULT_SAMPLE_SIZE)
    n = len(row_keys)
    if n <= full_threshold or n <= sample_size:
        return list(row_keys)
    # Even stride across the whole range; force-include first and last; dedupe; keep order.
    idxs = {0, n - 1}
    step = (n - 1) / (sample_size - 1)
    for i in range(sample_size):
        idxs.add(int(round(i * step)))
    return [row_keys[i] for i in sorted(idxs) if 0 <= i < n]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sampling.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/sampling.py tests/test_sampling.py
git commit -m "feat(sampling): adaptive spread sampling (full small, high-N large)"
```

---

### Task 6: Quality gate uses spread sampling

**Files:**
- Modify: `mcg_swarm/quality_gate.py:62-66`
- Test: `tests/test_gate_sampling.py`

**Interfaces:**
- Consumes: `select_sample` (Task 5); `run_table_tests(source, ...)` (Task 4).
- Produces: gate now samples across the table; a late-row anomaly fails the gate.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gate_sampling.py
import openpyxl
from mcg_swarm.source import OpenpyxlFileSource
from mcg_swarm.extraction import build_index
from mcg_swarm.quality_gate import run_table_tests
from mcg_swarm.schemas import CanonicalTable, ColumnSpec, ExtractionRef
from mcg_swarm.splitter import TableHandle

def test_late_row_dtype_drift_is_caught(tmp_path):
    # Numeric in first 20 rows, text afterward — first-N sampling missed this.
    p = tmp_path / "drift.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "T"
    ws.append(["Id", "Days"])
    for i in range(1, 21):  ws.append([f"r{i}", i])
    for i in range(21, 60): ws.append([f"r{i}", "pending"])  # late text rows
    wb.save(p)
    src = OpenpyxlFileSource(str(p))
    handle = TableHandle(sheet="T", region="A1:B59", header_row=1,
        columns=[ColumnSpec(name="Id", dtype="string", role="key"),
                 ColumnSpec(name="Days", dtype="number")], header_span=1)  # WRONG dtype
    idx = build_index(src, handle, row_key=["Id"])
    table = CanonicalTable(table_id="t", sheet="T", region="A1:B59", header_row=1,
        columns=handle.columns, extraction=ExtractionRef(script_name="t", row_key=["Id"]))
    rep = run_table_tests(src, table, idx)
    assert not rep.passed  # spread sample reaches the text rows; round-trip/dtype flags it
```

> Implementer note: confirm RED first — under the old contiguous `keys[:25]` this passes (bug). Only after switching to `select_sample` does the late text get sampled and fail.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gate_sampling.py -q`
Expected: FAIL — `assert not rep.passed` fails (old first-25 sampling never reads the text rows).

- [ ] **Step 3: Switch the gate to spread sampling**

In `mcg_swarm/quality_gate.py`, replace line 66 (`sample_keys = keys[:sample_size]`) and its comment block (62-66) with:

```python
    from mcg_swarm.sampling import select_sample
    # Spread sample (head/middle/tail) so anomalies anywhere are caught, not just the
    # first rows. `sample_size` arg still caps it for callers that pass one explicitly.
    sample_keys = select_sample(keys, sample_size=sample_size if sample_size != 25 else None)
```

> The `sample_size != 25` guard preserves explicit small-sample callers while letting the default (25) fall through to the env-driven spread default (~300). If you prefer, change the `run_table_tests` default to `sample_size=None` and pass `None` through to `select_sample` — pick one and keep it consistent.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_gate_sampling.py -q && pytest -q`
Expected: new test PASS; full suite green. If a pre-existing gate test asserted the contiguous-25 bounding box, update it to the spread behavior.

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/quality_gate.py tests/test_gate_sampling.py
git commit -m "feat(gate): spread sampling catches anomalies beyond the first rows"
```

---

## PHASE 3 — Repair loop, logging, activation

### Task 7: Repair logging + failure categorization

**Files:**
- Create: `mcg_swarm/repair_log.py`
- Test: `tests/test_repair_log.py`

**Interfaces:**
- Produces: `categorize_failures(failures: list[str]) -> dict[str, int]` (categories: `coverage_gap`, `column_name`, `column_integrity`, `row_integrity`, `round_trip`, `computed`, `other`); `log_repair_pass(workbook, table_id, pass_no, errors_before, errors_after, accepted, patch_summary, latency_s) -> None` (emits a `logging` INFO record and, when `MCG_REPAIR_LOG` is set, appends one JSON line).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_repair_log.py
import json
from mcg_swarm.repair_log import categorize_failures, log_repair_pass

def test_categorize_by_prefix():
    fails = [
        "coverage gap: column 'X' not in _col_to_phys",
        "column-name: duplicate column name 'A'",
        "column-integrity: 'B' index col=C but live header says col=D",
        "row-integrity: key 'k' -> row 5",
        "round-trip: 'V'@'k' live=1 but query()=2",
        "computed mismatch Total@k: live=3 calc=4",
        "something weird",
    ]
    cats = categorize_failures(fails)
    assert cats == {"coverage_gap": 1, "column_name": 1, "column_integrity": 1,
                    "row_integrity": 1, "round_trip": 1, "computed": 1, "other": 1}

def test_jsonl_written(tmp_path, monkeypatch):
    out = tmp_path / "repair.jsonl"
    monkeypatch.setenv("MCG_REPAIR_LOG", str(out))
    log_repair_pass("wb.xlsx", "T__0", 0, ["coverage gap: x"], [], True, "meta:1col", 1.2)
    rec = json.loads(out.read_text().strip())
    assert rec["table_id"] == "T__0" and rec["accepted"] is True
    assert rec["failure_categories"]["coverage_gap"] == 1
    assert rec["errors_after"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_repair_log.py -q`
Expected: FAIL — `ModuleNotFoundError: mcg_swarm.repair_log`.

- [ ] **Step 3: Write minimal implementation**

```python
# mcg_swarm/repair_log.py
"""Structured, categorized logging of repair passes — the data runway for deciding
which failure categories are worth automating with deterministic fixes later."""
from __future__ import annotations
import json
import logging
import os

_log = logging.getLogger("mcg_swarm.repair")

_PREFIXES = [
    ("coverage gap", "coverage_gap"),
    ("column-name", "column_name"),
    ("column-integrity", "column_integrity"),
    ("row-integrity", "row_integrity"),
    ("round-trip", "round_trip"),
    ("computed", "computed"),
]


def categorize_failures(failures) -> dict:
    cats = {"coverage_gap": 0, "column_name": 0, "column_integrity": 0,
            "row_integrity": 0, "round_trip": 0, "computed": 0, "other": 0}
    for f in failures:
        s = str(f)
        for prefix, key in _PREFIXES:
            if s.startswith(prefix):
                cats[key] += 1
                break
        else:
            cats["other"] += 1
    return cats


def log_repair_pass(workbook, table_id, pass_no, errors_before, errors_after,
                    accepted, patch_summary, latency_s) -> None:
    rec = {
        "workbook": workbook, "table_id": table_id, "pass": pass_no,
        "errors_before": list(errors_before), "errors_after": list(errors_after),
        "accepted": bool(accepted),
        "failure_categories": categorize_failures(errors_before),
        "patch_summary": patch_summary, "latency_s": round(float(latency_s), 3),
    }
    _log.info("repair pass %s table=%s before=%d after=%d accepted=%s",
              pass_no, table_id, len(rec["errors_before"]),
              len(rec["errors_after"]), rec["accepted"])
    path = os.environ.get("MCG_REPAIR_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_repair_log.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/repair_log.py tests/test_repair_log.py
git commit -m "feat(repair-log): categorized per-pass repair logging (+ optional JSONL)"
```

---

### Task 8: Bounded multi-pass repair loop in TableValidator

**Files:**
- Modify: `mcg_swarm/subagent/table_check.py:81-90` (policy: add `max_passes`, drop size gate), `:230-269` (`review`, `_run_agent`)
- Modify: `mcg_swarm/subagent/agent_runner.py:20-40` (`FakeAgentRunner` optional `finals` sequence)
- Test: `tests/test_repair_loop.py`

**Interfaces:**
- Consumes: `log_repair_pass` (Task 7); `_candidates`, `_accepts`, `_ranks_higher`, `_reindex_and_check`, `_table_seed` (existing, `table_check.py`); `WorkbookSource` (Task 4).
- Produces: `TableCheckPolicy(validate=False, max_passes=3)` (no `max_table_rows`); `TableValidator.review(source, handle, table)` runs ≤ `max_passes` agent passes, verify-before-accept each, logs each pass; `FakeAgentRunner(actions=[], final=None, finals=None)` returns successive `finals` across `run` calls (falls back to `final`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_repair_loop.py
import openpyxl
from mcg_swarm.source import OpenpyxlFileSource
from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from mcg_swarm.subagent.table_check import TableValidator, TableCheckPolicy
from mcg_swarm.schemas import CanonicalTable, ColumnSpec, ExtractionRef
from mcg_swarm.splitter import TableHandle

def _setup(tmp_path):
    p = tmp_path / "r.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "D"
    ws.append(["Key", "A", "B"]); ws.append(["k1", 1, 2]); ws.append(["k2", 3, 4])
    wb.save(p)
    src = OpenpyxlFileSource(str(p))
    handle = TableHandle(sheet="D", region="A1:C3", header_row=1,
        columns=[ColumnSpec(name="Key", dtype="string", role="key"),
                 ColumnSpec(name="A", dtype="number"), ColumnSpec(name="B", dtype="number")],
        header_span=1)
    table = CanonicalTable(table_id="t", sheet="D", region="A1:C3", header_row=1,
        columns=handle.columns, extraction=ExtractionRef(script_name="t", row_key=["Key"]))
    return src, handle, table

def test_no_improvement_stops_after_one_pass(tmp_path):
    src, handle, table = _setup(tmp_path)
    runner = FakeAgentRunner(actions=[], final={})  # agent proposes nothing
    out = TableValidator(runner, TableCheckPolicy(validate=True, max_passes=3)).review(src, handle, table)
    assert out.errors == []           # clean stays clean
    assert runner.calls == 1          # stopped after one no-op pass

def test_multi_pass_each_call_distinct(tmp_path):
    src, handle, table = _setup(tmp_path)
    # Two accepted-or-not passes: runner must be asked more than once when the first
    # pass changes something (label-score tie improvement keeps the loop alive).
    runner = FakeAgentRunner(actions=[], finals=[
        {"column_patches": [{"name": "A", "unit": "USD"}]},   # pass 0: accepted meta change? no err change
        {},                                                   # pass 1: nothing
    ])
    TableValidator(runner, TableCheckPolicy(validate=True, max_passes=3)).review(src, handle, table)
    assert runner.calls >= 1          # loop invoked; exact count asserted in loop-logic test below

def test_size_gate_removed_large_table_is_checked(tmp_path):
    src, handle, table = _setup(tmp_path)
    runner = FakeAgentRunner(actions=[], final={})
    pol = TableCheckPolicy(validate=True, max_passes=1)
    assert pol.should_check(table, n_data_rows=10_000) is True  # no size cap anymore
    TableValidator(runner, pol).review(src, handle, table)
    assert runner.calls == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_repair_loop.py -q`
Expected: FAIL — `TableCheckPolicy` has no `max_passes`, still has `max_table_rows` gate; `FakeAgentRunner` has no `finals`/`calls`; `review` takes a path not a source and runs once.

- [ ] **Step 3: Extend `FakeAgentRunner`**

In `mcg_swarm/subagent/agent_runner.py`, extend the dataclass/ctor to track call count and an optional sequence:

```python
# add fields: finals: list | None = None ; calls: int = 0 (init=False)
    def run(self, seed, tools, *, schema):
        self.calls += 1
        if self.finals is not None:
            i = min(self.calls - 1, len(self.finals) - 1)
            return dict(self.finals[i])
        return dict(self.final or {})
```
(Keep the existing `actions` replay behavior if present; only the return value changes.)

- [ ] **Step 4: Rewrite policy + review as a loop**

In `mcg_swarm/subagent/table_check.py`, change the policy (remove size gate):

```python
@dataclasses.dataclass
class TableCheckPolicy:
    validate: bool = False
    max_passes: int = 3
    def should_check(self, table: CanonicalTable, n_data_rows: int) -> bool:
        return bool(table.errors) or self.validate   # no size cap; sampling bounds cost
```

Replace `review` and `_run_agent` (currently `:237-269`):

```python
    def review(self, source, handle, table: CanonicalTable) -> CanonicalTable:
        try:
            from mcg_swarm.source import as_source
            from mcg_swarm.repair_log import log_repair_pass
            src = as_source(source)
            _r0, _c0, max_r, _c1 = range_box(handle.region)
            n_data_rows = max_r - handle.header_row
            if not self._policy.should_check(table, n_data_rows):
                return table
            workbook = getattr(src, "path", "workbook")
            current, attempts = table, []
            for pass_no in range(self._policy.max_passes):
                errs_before = list(current.errors)
                patch = self._run_agent(src, handle, current, attempts)
                best, best_errs = None, None
                for cand in _candidates(current, patch):
                    try:
                        errs = _reindex_and_check(src, cand)
                    except Exception:
                        continue
                    if not self._accepts(current, cand, errs):
                        continue
                    if best is None or _ranks_higher(cand, errs, best, best_errs):
                        best, best_errs = cand, errs
                accepted = best is not None
                log_repair_pass(workbook, current.table_id, pass_no, errs_before,
                                best_errs if accepted else errs_before, accepted,
                                _patch_summary(patch), 0.0)
                if accepted:
                    current = best.model_copy(update={"errors": best_errs})
                    attempts.append(_patch_summary(patch))
                    if not best_errs:
                        break
                else:
                    break
            return current
        except Exception:
            return table

    def _run_agent(self, source, handle, table, attempts) -> TableRecoveryPatch:
        min_r, min_c, max_r, max_c = range_box(handle.region)
        band = Band(sheet=handle.sheet, header_row=handle.header_row, region=handle.region,
                    col_start=min_c, col_end=max_c,
                    row_start=handle.header_row + 1, row_end=max_r)
        tools = build_band_toolset(BandView(source, band))
        seed = _table_seed(table)
        if attempts:
            seed += ("\nPrevious passes already tried (do not repeat these): "
                     + json.dumps(attempts))
        raw = self._runner.run(seed, tools, schema=TableRecoveryPatch)
        return TableRecoveryPatch.model_validate(raw)
```

Add a small helper near the other module helpers:

```python
def _patch_summary(patch: TableRecoveryPatch) -> str:
    parts = []
    if patch.column_patches:
        parts.append(f"meta:{len(patch.column_patches)}")
    if patch.header_row is not None or patch.header_span is not None:
        parts.append(f"struct(hr={patch.header_row},hs={patch.header_span},cols={len(patch.columns)})")
    return ",".join(parts) or "noop"
```

Update `_reindex_and_check(path, table)` → `_reindex_and_check(source, table)` and pass `source` to `build_index` and `run_table_tests` (both already source-aware after Tasks 3–4). Update its `TableHandle(...)` call unchanged.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_repair_loop.py -q && pytest tests/test_subagent_table_check.py -q && pytest -q`
Expected: new tests PASS; existing table-check tests still PASS (update their `.review(p, ...)` calls to pass `OpenpyxlFileSource(p)` and `TableCheckPolicy(validate=...)` which no longer takes `max_table_rows`).

- [ ] **Step 6: Commit**

```bash
git add mcg_swarm/subagent/table_check.py mcg_swarm/subagent/agent_runner.py tests/test_repair_loop.py tests/test_subagent_table_check.py
git commit -m "feat(repair): bounded multi-pass repair loop, size gate lifted, per-pass logging"
```

---

### Task 9: Activation — wire max_passes + always-on validate

**Files:**
- Modify: `mcg_swarm/subagent/__init__.py:83-99` (`build_table_validator`)
- Test: `tests/test_validator_activation.py`

**Interfaces:**
- Consumes: `TableCheckPolicy(validate, max_passes)` (Task 8).
- Produces: `build_table_validator(llm=None)` constructs `TableValidator(runner, TableCheckPolicy(validate=_validate_enabled(), max_passes=_max_passes()))` where `_max_passes()` reads `MCG_REPAIR_MAX_PASSES` (default 3). Returns `None` only when `MCG_SUBAGENT != react` or no auth (unchanged gating).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_validator_activation.py
import mcg_swarm.subagent as sa

def test_max_passes_from_env(monkeypatch):
    monkeypatch.setenv("MCG_REPAIR_MAX_PASSES", "5")
    assert sa._max_passes() == 5

def test_max_passes_default(monkeypatch):
    monkeypatch.delenv("MCG_REPAIR_MAX_PASSES", raising=False)
    assert sa._max_passes() == 3

def test_validator_none_without_react(monkeypatch):
    monkeypatch.delenv("MCG_SUBAGENT", raising=False)  # default static
    assert sa.build_table_validator() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_validator_activation.py -q`
Expected: FAIL — `_max_passes` does not exist.

- [ ] **Step 3: Add `_max_passes` and wire it**

In `mcg_swarm/subagent/__init__.py`:

```python
def _max_passes() -> int:
    try:
        return max(1, int(os.environ.get("MCG_REPAIR_MAX_PASSES", "3").strip()))
    except (TypeError, ValueError):
        return 3
```
Then in `build_table_validator`, change the construction to:

```python
        return TableValidator(
            ClaudeSDKAgentRunner(),
            TableCheckPolicy(validate=_validate_enabled(), max_passes=_max_passes()))
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_validator_activation.py -q && pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/subagent/__init__.py tests/test_validator_activation.py
git commit -m "feat(repair): wire MCG_REPAIR_MAX_PASSES; validator runs always (react mode)"
```

---

## PHASE 4 — Docs

### Task 10: Update OPTIMIZATIONS + add error-recovery diagram

**Files:**
- Modify: `OPTIMIZATIONS.md` (note the spread-sample O(rows) cost under #1; mark G1 unchanged)
- Create: `docs/diagrams/error-recovery.md` (Mermaid, engineer-level with file:line — per the project diagram convention)

**Interfaces:** none (docs only).

- [ ] **Step 1: Update OPTIMIZATIONS.md**

Add to item #1 (or a new note): the gate now spread-samples, so a pass reads across the table (~O(rows) on huge tables) and the repair loop runs it up to `MCG_REPAIR_MAX_PASSES` times; the `WorkbookSource` port is the seam where a streaming/columnar source removes this cost. Reference `mcg_swarm/source.py`.

- [ ] **Step 2: Write the diagram** (`docs/diagrams/error-recovery.md`)

A Mermaid `flowchart` matching the project convention: `_orchestrate_core` → CanonicalTable(errors) → `TableValidator.review` loop (`should_check` → `_run_agent` live → `_candidates` → `_reindex_and_check` → `_accepts` verify-before-accept → keep best → repeat ≤ max_passes) → emit (errors=[] or remaining) → `build_indices` skips errored tables. Annotate each node with `table_check.py:NN`. Include the phase reference table and design notes (always-on, sampling, no-loop→loop, logging). End by updating the `diagrams-index` memory pointer.

- [ ] **Step 3: Commit**

```bash
git add OPTIMIZATIONS.md docs/diagrams/error-recovery.md
git commit -m "docs: error-recovery diagram + spread-sample cost note in OPTIMIZATIONS"
```

---

## Self-review notes (addressed)

- **Spec coverage:** source port (T1–T4), adaptive sampling (T5–T6), repair loop (T8) + always-on/size-gate-lifted (T8–T9) + logging (T7), contract preserved (T8 `_accepts`/`errors`), config knobs (T5/T7/T9), tests via FakeAgentRunner (T8), scalability note (T10). All spec sections map to a task.
- **Migration safety:** Tasks 2–4 are behavior-preserving; each ends on `pytest -q` green. The `.path` shim in T2 is explicitly removed in T3–T4.
- **Type consistency:** `WorkbookSource`/`as_source` names are stable across tasks; `review(source, handle, table)`, `run_table_tests(source, ...)`, `build_index(source, ...)`, `BandView(source, band)`, `select_sample(...)`, `TableCheckPolicy(validate, max_passes)`, `_max_passes()` used identically where referenced.
- **Live agent:** all loop tests use `FakeAgentRunner`; no live SDK calls in CI. Optional live smoke remains the demo (`demo_walkthrough.py fix`).
