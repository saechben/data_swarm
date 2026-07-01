# Boundary Detection Phase 2 — Agent Boundary Alteration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When Phase-1 detection flags a dropped table (`uncovered-data`) and a runner is injected, let the agent propose a whole-sheet re-cut into multiple vertical tables, accepted only when it provably increases cell coverage without adding errors (verify-before-accept).

**Architecture:** A new sheet-level Layer 2 sits between `split_workbook` and per-table orchestration in `run_swarm`. It runs *only* when an `uncovered-data` finding fired and a runner is present. A structural agent, given whole-sheet read-only tools, proposes a full set of vertical table regions; each proposal is materialised into a deterministic `TableHandle`, the candidate set is scored `(coverage, errors, gaps)` against the deterministic baseline, and it replaces the baseline only if strictly better on all three (more covered cells, no new errors, no new interior fragmentation). The `gaps` term is the over-claim guard: it stops a greedy "one giant region" proposal from inflating coverage by swallowing a blank separator row or gutter column. An accepted re-cut is then re-validated against the baseline with the LIVE per-table pipeline inside `run_swarm`; if the split raises real pipeline errors it is discarded and the baseline kept. A rejected or hallucinated re-cut is a no-op — the deterministic single handle is kept and the finding is annotated `resolution="rejected"`. Detection (Phase 1) is untouched and remains the guarantee.

**Tech Stack:** Python 3, Pydantic v2, openpyxl, the injected `AgentRunner` DI seam (Claude Agent SDK in production, `FakeAgentRunner` in tests).

## Global Constraints

- Interpreter is `.venv/bin/python`; tests run with `.venv/bin/python -m pytest -q` (NOT bare `pytest`).
- Baseline before Task 1: **277 passed, 1 skipped** (SDK installed). Every task ends with zero failures; deltas confined to touched files.
- **Never-raise contract holds end to end.** Layer 2 catches every agent/SDK/build failure and falls back to the deterministic single handle with findings intact. Detection findings must never be lost or downgraded by Layer 2.
- **Verify-before-accept is the only commit path, and it is three-way.** A candidate handle set replaces the baseline *iff* `coverage_score` strictly increases AND total error count does not increase AND interior-fragmentation (`_region_gaps`) does not increase. The gap term is mandatory: without it a degenerate "one giant region over the whole used range" scores `(higher coverage, ≤ errors)` — because `coverage_score` is a monotone count of claimed non-empty cells and `uncovered-data` errors only fire *outside* a region, so over-claiming raises coverage and lowers residue errors together. `_region_gaps` counts fully-blank interior rows/cols, which a fused-two-tables region has and a coherent single table does not. On top of the static gate, an accepted re-cut is re-validated in `run_swarm` against the baseline with the LIVE per-table pipeline (§ Task 6). No other acceptance rule.
- **Scope: vertical re-cuts only.** Layer 2 repairs dropped/stacked/side-by-side vertical tables (`uncovered-data`). Transposed proposals (`orientation="transposed"`) are never built or applied — `empty-header-corner`/`transpose-suspected` stay detection-only, and `false-header-span` stays handled by the existing table-level `TableValidator`. This is deliberate, per the spec ("if it can't be scored as strictly better it stays detection-only").
- **`Finding` is the source of truth.** `Finding` is a plain `_Base` model with no validators, so `Finding.model_copy(update={...})` is safe. Do NOT use `model_copy(update=...)` on `CanonicalTable`/`WorkbookExtraction` to change `findings`/`errors` — those have derivation validators (Phase-1 gotcha).
- **Layer 2 must not recurse — but static scoring is only a proxy.** The static gate (`score_handles`) orchestrates candidate handles with `subagent=None, table_validator=None`: a cheap, deterministic proxy used only to *choose* whether a re-cut is structurally better. It deliberately does NOT run the band ReAct verifier or table validator, which is exactly why the accepted candidate must then be re-scored against the baseline with the real `subagent`/`table_validator` inside `run_swarm`. That live re-validation does not recurse: `orchestrate_table` never re-enters the sheet-level reviewer. This closes the gap between the static proxy and the pipeline production actually runs (the band verifier patches column role/dtype unconditionally, and a split can newly cross the ReAct escalation threshold that the monolithic baseline never hit).
- Existing table IDs stay `{sheet}__{i}` when a sheet yields one handle; only a genuinely split sheet uses `{sheet}__{i}_{j}`.
- `range_box(a1)` returns `(min_row, min_col, max_row, max_col)`; `openpyxl.utils.range_boundaries(a1)` returns `(min_col, min_row, max_col, max_row)`. Grids are `list[tuple]` with `grid[0]` = sheet row 1, `row[0]` = column 1.

---

### Task 1: `handle_from_region` — deterministic handle builder

Builds a `TableHandle` that honours an explicit absolute region + header row/span, deriving column names and dtypes with the splitter's existing helpers. This is how a re-cut proposal becomes a real handle. Pure, no agent.

**Files:**
- Modify: `mcg_swarm/splitter.py` (add `handle_from_region`; add `range_boundaries` to the existing openpyxl import on line 5)
- Test: `tests/test_handle_from_region.py`

**Interfaces:**
- Consumes: `TableHandle`, `ColumnSpec`, `_composite_col_names`, `_infer_dtype` (all in `mcg_swarm/splitter.py`); `openpyxl.utils.range_boundaries`.
- Produces: `handle_from_region(grid: list[tuple], sheet: str, region: str, header_row: int, header_span: int = 1) -> TableHandle` — `header_row` is an absolute (1-based) sheet row; column roles are `key` for the first column, `value` otherwise; dtypes inferred from up to 20 data rows.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_handle_from_region.py
from mcg_swarm.splitter import handle_from_region

GRID = [
    ("Region", "Revenue", "Units"),   # row 1
    ("EMEA", 100, 5),                  # row 2
    ("APAC", 200, 9),                  # row 3
    (None, None, None),               # row 4 (gap)
    ("Product", "Price", None),       # row 5
    ("Widget", 49, None),             # row 6
]


def test_builds_handle_for_top_block():
    h = handle_from_region(GRID, "Data", "A1:C3", header_row=1)
    assert h.sheet == "Data"
    assert h.region == "A1:C3"
    assert h.header_row == 1
    assert h.header_span == 1
    assert [c.name for c in h.columns] == ["Region", "Revenue", "Units"]
    assert h.columns[0].role == "key"
    assert h.columns[1].role == "value"
    assert h.columns[1].dtype == "number"    # 100, 200
    assert h.columns[0].dtype == "string"    # EMEA, APAC


def test_builds_handle_for_offset_block():
    # a second table lower on the sheet, honoured at its real coordinates
    h = handle_from_region(GRID, "Data", "A5:B6", header_row=5)
    assert [c.name for c in h.columns] == ["Product", "Price"]
    assert h.columns[1].dtype == "number"    # 49


def test_two_row_header_span():
    grid = [
        ("Group", "H1", "H1"),        # row 1 (group header)
        ("Region", "Q1", "Q2"),       # row 2 (leaf header)
        ("EMEA", 1, 2),               # row 3
    ]
    h = handle_from_region(grid, "Data", "A1:C3", header_row=1, header_span=2)
    # bottom-first composite naming: leaf row wins where present
    assert [c.name for c in h.columns] == ["Region", "Q1", "Q2"]
    assert h.header_span == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_handle_from_region.py -q`
Expected: FAIL with `ImportError: cannot import name 'handle_from_region'`.

- [ ] **Step 3: Write minimal implementation**

In `mcg_swarm/splitter.py`, change the openpyxl import line 5 from:

```python
from openpyxl.utils import get_column_letter
```

to:

```python
from openpyxl.utils import get_column_letter, range_boundaries
```

Then add at the end of the file (after `split_workbook`):

```python
def handle_from_region(grid: list[tuple], sheet: str, region: str,
                       header_row: int, header_span: int = 1) -> TableHandle:
    """Build a TableHandle honouring an explicit absolute region + header row/span.

    Used to materialise an agent's re-cut proposal into a real handle. Column names come
    from the header span (bottom-row-first composite rule); dtypes are inferred from the
    data rows below the header. `header_row` is a 1-based absolute sheet row.
    """
    min_col, min_row, max_col, max_row = range_boundaries(region)

    def cell(r: int, c: int):
        row = grid[r - 1] if 0 <= r - 1 < len(grid) else ()
        return row[c - 1] if 0 <= c - 1 < len(row) else None

    header_rows = [
        tuple(cell(header_row + k, c) for c in range(min_col, max_col + 1))
        for k in range(header_span)
    ]
    data_rows = [
        tuple(cell(r, c) for c in range(min_col, max_col + 1))
        for r in range(header_row + header_span, max_row + 1)
    ]
    names = _composite_col_names(header_rows, 0, max_col - min_col)
    cols = []
    for j in range(max_col - min_col + 1):
        samples = [dr[j] if j < len(dr) else None for dr in data_rows[:20]]
        cols.append(ColumnSpec(name=names[j], dtype=_infer_dtype(samples),
                               role="key" if j == 0 else "value"))
    return TableHandle(sheet, region, header_row, cols, header_span=header_span)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_handle_from_region.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/splitter.py tests/test_handle_from_region.py
git commit -m "feat(splitter): handle_from_region — deterministic handle from explicit region"
```

---

### Task 2: Optional `system` override on the AgentRunner

The structural agent's task ("fix table boundaries on a whole sheet") differs from the band verifier's ("fix column metadata of one band"). The injected runner hard-codes a band-oriented system prompt. Add an optional `system` parameter so a caller can supply the correct system prompt, defaulting to the existing behaviour.

**Files:**
- Modify: `mcg_swarm/subagent/agent_runner.py` (protocol + `FakeAgentRunner.run`)
- Modify: `agent_runtime/claude_sdk_runner.py` (`run` + `_run_async`)
- Test: `tests/test_agent_runner_system.py`

**Interfaces:**
- Consumes: existing `AgentRunner.run(seed, tools, *, schema)`.
- Produces: `AgentRunner.run(seed, tools, *, schema, system: str | None = None)` — when `system` is None the runner keeps its default system prompt; when set, the runner uses it. `FakeAgentRunner` accepts and ignores `system`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agent_runner_system.py
from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from pydantic import BaseModel


class _P(BaseModel):
    ok: bool = True


def test_fake_runner_accepts_system_kwarg():
    r = FakeAgentRunner(actions=[], final={"ok": True})
    # must not raise when a system prompt is supplied
    out = r.run("seed", [], schema=_P, system="a different system prompt")
    assert out == {"ok": True}


def test_fake_runner_still_works_without_system():
    r = FakeAgentRunner(actions=[], final={"ok": True})
    assert r.run("seed", [], schema=_P) == {"ok": True}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agent_runner_system.py -q`
Expected: FAIL with `TypeError: run() got an unexpected keyword argument 'system'`.

- [ ] **Step 3: Write minimal implementation**

In `mcg_swarm/subagent/agent_runner.py`, update the protocol signature (line 17) to:

```python
    def run(self, seed: str, tools: list[Tool], *, schema, system: str | None = None) -> dict: ...
```

and `FakeAgentRunner.run` (line 41) to:

```python
    def run(self, seed: str, tools: list[Tool], *, schema, system: str | None = None) -> dict:
```

(the body is unchanged — `system` is accepted and ignored).

In `agent_runtime/claude_sdk_runner.py`, change `run` (line 61):

```python
    def run(self, seed: str, tools: list[Tool], *, schema, system: str | None = None) -> dict:
        return asyncio.run(self._run_async(seed, tools, schema, system))
```

and `_run_async` (line 63):

```python
    async def _run_async(self, seed: str, tools: list[Tool], schema, system: str | None = None) -> dict:
```

and the `opt_kwargs` system prompt (line 83):

```python
            system_prompt=system or _SYSTEM,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_agent_runner_system.py tests/test_subagent_table_check.py -q`
Expected: PASS (new tests + existing table-check tests unaffected — they call `run` without `system`).

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/subagent/agent_runner.py agent_runtime/claude_sdk_runner.py tests/test_agent_runner_system.py
git commit -m "feat(runner): optional system-prompt override on AgentRunner.run"
```

---

### Task 3: Whole-sheet toolset — `SheetView` + `build_sheet_toolset`

The band tools are region-clamped, so the agent literally cannot see the dropped table. This gives the structural agent read-only visibility over the *entire* sheet, mirroring `tools.py`/`BandView` at sheet scope. Snapshot once (open-cost sensitive).

**Files:**
- Create: `mcg_swarm/subagent/structural_tools.py`
- Test: `tests/test_structural_tools.py`

**Interfaces:**
- Consumes: `Tool` (from `mcg_swarm/subagent/tools.py`), `as_source` (from `mcg_swarm/source.py`), `range_box` (from `eval.util`).
- Produces:
  - `SheetView(source, sheet: str)` with probes `dimensions() -> dict`, `peek_rows(start_row: int, count: int) -> list[dict]` (absolute 1-based rows), `peek_region(a1: str) -> list[dict]`.
  - `build_sheet_toolset(view: SheetView) -> list[Tool]` — tools named `dimensions`, `peek_rows`, `peek_region`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_structural_tools.py
from mcg_swarm.subagent.structural_tools import SheetView, build_sheet_toolset
from tests.fake_source import FakeSource


def _stacked():
    # table 1 rows 1-3, gap row 4, table 2 rows 5-6
    v = {(1, 1): "Region", (1, 2): "Revenue",
         (2, 1): "EMEA", (2, 2): 100,
         (3, 1): "APAC", (3, 2): 200,
         (5, 1): "Product", (5, 2): "Price",
         (6, 1): "Widget", (6, 2): 49}
    return FakeSource("Data", v, {})


def test_dimensions_spans_whole_sheet():
    view = SheetView(_stacked(), "Data")
    d = view.dimensions()
    assert d["sheet"] == "Data"
    assert d["n_rows"] >= 6
    assert d["n_cols"] >= 2


def test_peek_region_reads_lower_block():
    view = SheetView(_stacked(), "Data")
    rows = view.peek_region("A5:B6")
    assert rows[0]["row"] == 5
    assert rows[0]["cells"][0] == "Product"
    assert rows[1]["cells"][1] == 49


def test_toolset_names_and_dispatch():
    view = SheetView(_stacked(), "Data")
    tools = {t.name: t for t in build_sheet_toolset(view)}
    assert set(tools) == {"dimensions", "peek_rows", "peek_region"}
    out = tools["peek_rows"].handler({"start_row": 1, "count": 2})
    assert out["rows"][0]["cells"][0] == "Region"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_structural_tools.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcg_swarm.subagent.structural_tools'`.

- [ ] **Step 3: Write minimal implementation**

```python
# mcg_swarm/subagent/structural_tools.py
"""Read-only WHOLE-SHEET tool layer for the structural (boundary) agent.

Mirrors tools.py/BandView but at sheet scope: the structural agent must see data OUTSIDE
the deterministically-chosen region (that is the whole point — a dropped table lives off
the region a band agent can see). Snapshots the sheet's used grid ONCE (open-cost
sensitive), serves every probe from memory. Rows are reported with absolute 1-based row
numbers; cells are 0-based within the snapshot's first column (column 1)."""
from __future__ import annotations

from eval.util import range_box
from mcg_swarm.source import as_source
from mcg_swarm.subagent.tools import Tool


class SheetView:
    """Once-snapshotted read-only view over an entire sheet grid."""

    def __init__(self, source, sheet: str) -> None:
        self.sheet = sheet
        src = as_source(source)
        # read_region with no bounds → the whole used sheet, grid[0] == row 1, col 1.
        self._grid = [list(r) for r in src.read_region(sheet)]

    def _row(self, abs_row: int):
        idx = abs_row - 1
        if 0 <= idx < len(self._grid):
            return list(self._grid[idx])
        return None

    def dimensions(self) -> dict:
        n_rows = len(self._grid)
        n_cols = max((len(r) for r in self._grid), default=0)
        return {"sheet": self.sheet, "n_rows": n_rows, "n_cols": n_cols}

    def peek_rows(self, start_row: int = 1, count: int = 20) -> list[dict]:
        out = []
        for ar in range(start_row, start_row + count):
            row = self._row(ar)
            if row is None:
                if ar <= len(self._grid):
                    continue
                break
            out.append({"row": ar, "cells": row})
        return out

    def peek_region(self, a1: str) -> list[dict]:
        min_row, min_col, max_row, max_col = range_box(a1)
        out = []
        for ar in range(min_row, max_row + 1):
            row = self._row(ar)
            if row is None:
                continue
            out.append({"row": ar,
                        "cells": row[(min_col - 1):max_col]})
        return out


def build_sheet_toolset(view: SheetView) -> list[Tool]:
    """Wrap a SheetView's probes as framework-agnostic Tools."""
    return [
        Tool("dimensions",
             "Whole-sheet size: sheet name, number of used rows and columns.",
             {"type": "object", "properties": {}},
             lambda a: view.dimensions()),
        Tool("peek_rows",
             "Read `count` rows starting at an absolute 1-based sheet row `start_row`.",
             {"type": "object", "properties": {
                 "start_row": {"type": "integer"}, "count": {"type": "integer"}}},
             lambda a: {"rows": view.peek_rows(int(a.get("start_row", 1)),
                                               int(a.get("count", 20)))}),
        Tool("peek_region",
             "Read cells in an absolute A1 range (e.g. 'A5:D12') anywhere on the sheet.",
             {"type": "object", "properties": {"a1": {"type": "string"}},
              "required": ["a1"]},
             lambda a: {"rows": view.peek_region(str(a["a1"]))}),
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_structural_tools.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/subagent/structural_tools.py tests/test_structural_tools.py
git commit -m "feat(structural): whole-sheet read-only toolset (SheetView)"
```

---

### Task 4: Re-cut proposal schema + `score_handles`

The agent's output schema and the deterministic acceptance metric. `score_handles` computes `(coverage, errors, gaps)` for a handle set — coverage from the Phase-1 `coverage_score`, errors from residue scans plus pure-static orchestration of each handle, gaps from `_region_gaps` (interior blank rows/cols, the over-claim guard). This is the verify half of verify-before-accept.

**Files:**
- Create: `mcg_swarm/subagent/structural.py` (schema + scoring only in this task)
- Test: `tests/test_structural_score.py`

**Interfaces:**
- Consumes: `coverage_score`, `scan_handle` (from `mcg_swarm/coverage.py`); `orchestrate_table` (from `mcg_swarm/orchestrator.py`); `TableHandle` (from `mcg_swarm/splitter.py`).
- Produces:
  - `ProposedTable(BaseModel)`: `region: str`, `header_row: int`, `header_span: int = 1`, `orientation: Literal["vertical","transposed"] = "vertical"`.
  - `SheetRecutPatch(BaseModel)`: `tables: list[ProposedTable] = []`, `rationale: str = ""`.
  - `_region_gaps(grid, handle) -> int`: fully-blank rows/cols strictly *inside* a handle's region box (the over-claim guard).
  - `score_handles(source, grid: list[tuple], handles: list[TableHandle], sheet: str) -> tuple[int, int, int]` returning `(coverage, error_count, gap_count)` where `gap_count` sums `_region_gaps` across handles. Orchestrates each handle with `subagent=None, table_validator=None` (pure static, no recursion).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_structural_score.py
from mcg_swarm.subagent.structural import ProposedTable, SheetRecutPatch, score_handles
from mcg_swarm.splitter import handle_from_region
from tests.fake_source import FakeSource


def _stacked():
    # two stacked tables: rows 1-3 and rows 5-6, one blank row between
    v = {(1, 1): "Region", (1, 2): "Revenue",
         (2, 1): "EMEA", (2, 2): 100,
         (3, 1): "APAC", (3, 2): 200,
         (5, 1): "Product", (5, 2): "Price",
         (6, 1): "Widget", (6, 2): 49}
    return FakeSource("Data", v, {})


def test_schema_defaults():
    p = SheetRecutPatch(tables=[ProposedTable(region="A1:B3", header_row=1)])
    assert p.tables[0].orientation == "vertical"
    assert p.tables[0].header_span == 1


def test_split_covers_more_than_single_region():
    src = _stacked()
    grid = src.read_region("Data")
    baseline = handle_from_region(grid, "Data", "A1:B3", header_row=1)
    split = [handle_from_region(grid, "Data", "A1:B3", header_row=1),
             handle_from_region(grid, "Data", "A5:B6", header_row=5)]
    base_cov, base_err, base_gap = score_handles(src, grid, [baseline], "Data")
    cand_cov, cand_err, cand_gap = score_handles(src, grid, split, "Data")
    # the second table's cells are only covered by the split
    assert cand_cov > base_cov
    # splitting must not manufacture new errors or interior gaps
    assert cand_err <= base_err
    assert cand_gap <= base_gap        # two tight regions, no interior blank rows/cols


def test_bad_split_does_not_beat_baseline():
    src = _stacked()
    grid = src.read_region("Data")
    baseline = handle_from_region(grid, "Data", "A1:B3", header_row=1)
    # a "re-cut" that is just the same single region → no coverage gain
    same = [handle_from_region(grid, "Data", "A1:B3", header_row=1)]
    base = score_handles(src, grid, [baseline], "Data")
    cand = score_handles(src, grid, same, "Data")
    assert not (cand[0] > base[0] and cand[1] <= base[1] and cand[2] <= base[2])


def test_overclaiming_region_is_penalised():
    # the degenerate proposal: one giant region swallowing the blank separator
    # row 4 AND the lower table. It covers more non-empty cells and drops the
    # uncovered-data residue error — but it fuses two tables, so it has an
    # interior blank-row gap the tight baseline does not.
    src = _stacked()
    grid = src.read_region("Data")
    tight = [handle_from_region(grid, "Data", "A1:B3", header_row=1)]
    giant = [handle_from_region(grid, "Data", "A1:B6", header_row=1)]
    t = score_handles(src, grid, tight, "Data")
    g = score_handles(src, grid, giant, "Data")
    assert g[0] > t[0]                 # greedy region covers more non-empty cells...
    assert g[2] > t[2]                 # ...but introduces an interior blank-row gap
    # so it must NOT satisfy the three-way strict-better acceptance rule
    assert not (g[0] > t[0] and g[1] <= t[1] and g[2] <= t[2])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_structural_score.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcg_swarm.subagent.structural'` (4 tests once the module exists).

- [ ] **Step 3: Write minimal implementation**

```python
# mcg_swarm/subagent/structural.py
"""Layer 2 — agent boundary alteration (verify-before-accept).

Phase 1 DETECTS a dropped table (`uncovered-data`) but keeps the deterministic single
region. Layer 2, when a runner is injected, lets the agent propose a whole-sheet re-cut
into multiple vertical tables. Every proposal is materialised into a real handle, scored
`(coverage, errors, gaps)` against the deterministic baseline, and accepted ONLY if strictly
better (more covered non-empty cells, no more errors, no more interior fragmentation) — and
then only if it survives live re-validation in run_swarm. A rejected or hallucinated re-cut
is a no-op: the deterministic handle is kept and the finding is annotated. Never raises.

Scope: vertical re-cuts only. Transposed proposals are never built (detection-only).
"""
from __future__ import annotations

from typing import Literal

from openpyxl.utils import range_boundaries
from pydantic import BaseModel

from mcg_swarm.coverage import coverage_score, scan_handle
from mcg_swarm.orchestrator import orchestrate_table


class ProposedTable(BaseModel):
    """One table in an agent re-cut proposal (absolute coordinates)."""
    region: str
    header_row: int
    header_span: int = 1
    orientation: Literal["vertical", "transposed"] = "vertical"


class SheetRecutPatch(BaseModel):
    """The structural agent's `finalize` output: the full set of tables on the sheet."""
    tables: list[ProposedTable] = []
    rationale: str = ""


def _region_gaps(grid: list[tuple], handle) -> int:
    """Count fully-blank rows/cols strictly INSIDE a handle's region box.

    A coherent single table has none. A region that fuses two tables has >=1: a
    stacked pair leaves a blank separator row between them, a side-by-side pair
    leaves a blank gutter column. This is the deterministic guard against a greedy
    "one giant region" proposal that inflates coverage_score (a monotone count of
    claimed non-empty cells) while dropping uncovered-data residue errors — the two
    static signals that otherwise move together when you over-claim. Edge rows/cols
    are excluded so a tight cut scores 0; only interior blanks count.
    `range_boundaries` returns (min_col, min_row, max_col, max_row); grid[0] == row 1.
    """
    min_col, min_row, max_col, max_row = range_boundaries(handle.region)

    def cell(r: int, c: int):
        row = grid[r - 1] if 0 <= r - 1 < len(grid) else ()
        return row[c - 1] if 0 <= c - 1 < len(row) else None

    gaps = 0
    for r in range(min_row + 1, max_row):        # interior rows only
        if all(cell(r, c) in (None, "") for c in range(min_col, max_col + 1)):
            gaps += 1
    for c in range(min_col + 1, max_col):        # interior cols only
        if all(cell(r, c) in (None, "") for r in range(min_row, max_row + 1)):
            gaps += 1
    return gaps


def score_handles(source, grid: list[tuple], handles, sheet: str) -> tuple[int, int, int]:
    """Deterministic acceptance metric for a handle set: (coverage, error_count, gap_count).

    coverage:    non-empty cells covered by the union of handle regions (Phase-1 metric).
    error_count: residue-scan error findings + pure-static orchestration errors, summed.
    gap_count:   fully-blank interior rows/cols across handles — the over-claim guard.
    Orchestration runs WITHOUT a subagent/validator so Layer 2 cannot recurse; the
    accepted candidate is re-validated with the live pipeline later, in run_swarm.
    """
    coverage = coverage_score(grid, [h.region for h in handles])
    errors, gaps = 0, 0
    for i, h in enumerate(handles):
        errors += sum(1 for f in scan_handle(grid, h, sheet) if f.severity == "error")
        table = orchestrate_table(
            source, h, table_id=f"__score_{i}",
            llm=None, subagent=None, table_validator=None)
        errors += len(table.errors)
        gaps += _region_gaps(grid, h)
    return coverage, errors, gaps
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_structural_score.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/subagent/structural.py tests/test_structural_score.py
git commit -m "feat(structural): re-cut schema + three-way score_handles (coverage, errors, gaps)"
```

---

### Task 5: `StructuralReviewer` — propose → build → score → accept → annotate

The Layer-2 orchestrator. Runs the agent over the whole sheet, builds vertical candidate handles, applies verify-before-accept via `score_handles`, and returns a `SheetReview` carrying the handles to orchestrate plus fully-annotated findings (`resolution` + `agent_action`). Never raises.

**Files:**
- Modify: `mcg_swarm/subagent/structural.py` (add `SheetReview`, `StructuralPolicy`, `STRUCTURAL_SYSTEM`, `StructuralReviewer`)
- Test: `tests/test_structural_reviewer.py`

**Interfaces:**
- Consumes: `handle_from_region` (from `mcg_swarm/splitter.py`); `SheetView`, `build_sheet_toolset` (from `mcg_swarm/subagent/structural_tools.py`); `score_handles`, `SheetRecutPatch` (Task 4); `Finding` (from `mcg_swarm/schemas.py`); an injected `AgentRunner`.
- Produces:
  - `SheetReview` dataclass: `handles: list[TableHandle]`, `detect_findings: list[list[Finding]]` (aligned with `handles`, table/column/cell scope), `sheet_findings: list[Finding]` (workbook/sheet scope, annotated), `recut: bool = False` (True only when a candidate replaced the baseline — the signal `run_swarm` uses to trigger live re-validation).
  - `StructuralPolicy` dataclass: `max_tables: int = 12`.
  - `StructuralReviewer(runner, policy=None)` with `review(source, handle, grid, scan) -> SheetReview`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_structural_reviewer.py
from mcg_swarm.coverage import scan_handle
from mcg_swarm.splitter import handle_from_region
from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from mcg_swarm.subagent.structural import StructuralReviewer
from tests.fake_source import FakeSource


def _stacked():
    v = {(1, 1): "Region", (1, 2): "Revenue",
         (2, 1): "EMEA", (2, 2): 100,
         (3, 1): "APAC", (3, 2): 200,
         (5, 1): "Product", (5, 2): "Price",
         (6, 1): "Widget", (6, 2): 49}
    return FakeSource("Data", v, {})


def _setup():
    src = _stacked()
    grid = src.read_region("Data")
    handle = handle_from_region(grid, "Data", "A1:B3", header_row=1)
    scan = scan_handle(grid, handle, "Data")   # contains an uncovered-data finding
    assert any(f.category == "uncovered-data" for f in scan)
    return src, grid, handle, scan


def test_good_split_is_accepted_and_findings_marked_fixed():
    src, grid, handle, scan = _setup()
    runner = FakeAgentRunner(actions=[], final={"tables": [
        {"region": "A1:B3", "header_row": 1},
        {"region": "A5:B6", "header_row": 5}]})
    review = StructuralReviewer(runner).review(src, handle, grid, scan)
    assert len(review.handles) == 2
    assert {h.region for h in review.handles} == {"A1:B3", "A5:B6"}
    fixed = [f for f in review.sheet_findings if f.category == "uncovered-data"]
    assert fixed and all(f.resolution == "fixed" for f in fixed)
    assert all(f.agent_action for f in fixed)


def test_bad_split_is_rejected_baseline_kept():
    src, grid, handle, scan = _setup()
    # proposal that drops the lower table (no coverage gain) → must be rejected
    runner = FakeAgentRunner(actions=[], final={"tables": [
        {"region": "A1:B3", "header_row": 1}]})
    review = StructuralReviewer(runner).review(src, handle, grid, scan)
    assert [h.region for h in review.handles] == ["A1:B3"]
    rej = [f for f in review.sheet_findings if f.category == "uncovered-data"]
    assert rej and all(f.resolution == "rejected" for f in rej)


def test_empty_proposal_is_no_op_open():
    src, grid, handle, scan = _setup()
    runner = FakeAgentRunner(actions=[], final={"tables": []})
    review = StructuralReviewer(runner).review(src, handle, grid, scan)
    assert [h.region for h in review.handles] == ["A1:B3"]
    open_f = [f for f in review.sheet_findings if f.category == "uncovered-data"]
    assert open_f and all(f.resolution == "open" for f in open_f)


def test_transposed_proposal_not_built():
    src, grid, handle, scan = _setup()
    runner = FakeAgentRunner(actions=[], final={"tables": [
        {"region": "A1:B6", "header_row": 1, "orientation": "transposed"}]})
    review = StructuralReviewer(runner).review(src, handle, grid, scan)
    # nothing vertical to build → baseline kept
    assert [h.region for h in review.handles] == ["A1:B3"]


def test_overclaiming_recut_rejected():
    # a single giant vertical region that swallows the blank row + lower table:
    # more coverage, but an interior gap → fails the three-way gate, baseline kept.
    src, grid, handle, scan = _setup()
    runner = FakeAgentRunner(actions=[], final={"tables": [
        {"region": "A1:B6", "header_row": 1}]})
    review = StructuralReviewer(runner).review(src, handle, grid, scan)
    assert [h.region for h in review.handles] == ["A1:B3"]
    assert review.recut is False
    rej = [f for f in review.sheet_findings if f.category == "uncovered-data"]
    assert rej and all(f.resolution == "rejected" for f in rej)


def test_agent_failure_falls_back_to_baseline():
    src, grid, handle, scan = _setup()

    class Boom:
        def run(self, *a, **k):
            raise RuntimeError("sdk down")

    review = StructuralReviewer(Boom()).review(src, handle, grid, scan)
    assert [h.region for h in review.handles] == ["A1:B3"]
    # detection survives the failure
    assert any(f.category == "uncovered-data" for f in review.sheet_findings)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_structural_reviewer.py -q`
Expected: FAIL with `ImportError: cannot import name 'StructuralReviewer'` (6 tests once implemented).

- [ ] **Step 3: Write minimal implementation**

Append to `mcg_swarm/subagent/structural.py`:

```python
import dataclasses
import json

from mcg_swarm.schemas import Finding
from mcg_swarm.splitter import handle_from_region
from mcg_swarm.subagent.structural_tools import SheetView, build_sheet_toolset


STRUCTURAL_SYSTEM = (
    "You are correcting the TABLE BOUNDARIES of ONE spreadsheet. A fast deterministic pass "
    "cut the sheet into a single region, but a whole-sheet scan found tabular data OUTSIDE "
    "that region — a second table was likely dropped. Use the read-only tools to inspect the "
    "ENTIRE sheet, then call `finalize` with the COMPLETE set of vertically-oriented tables "
    "you can see. Each table needs its absolute A1 `region`, the absolute `header_row`, and "
    "`header_span` (1 unless there is a genuine two-row header). List EVERY real table on the "
    "sheet, not only the dropped one. If the single existing region is already correct, return "
    "an empty `tables` list. Never invent cells, regions, or tables."
)


@dataclasses.dataclass
class SheetReview:
    handles: list                       # TableHandle(s) to orchestrate
    detect_findings: list               # list[list[Finding]] aligned with handles (table scope)
    sheet_findings: list                # list[Finding] workbook/sheet scope, annotated
    recut: bool = False                 # True only when a candidate replaced the baseline


@dataclasses.dataclass
class StructuralPolicy:
    max_tables: int = 12                 # guard against runaway proposals


class StructuralReviewer:
    """Agent boundary alteration over one sheet, verify-before-accept. Never raises."""

    def __init__(self, runner, policy: "StructuralPolicy | None" = None) -> None:
        self._runner = runner
        self._policy = policy or StructuralPolicy()

    def review(self, source, handle, grid: list[tuple], scan) -> SheetReview:
        sheet_scope = [f for f in scan if f.scope == "sheet"]
        table_scope = [f for f in scan if f.scope != "sheet"]
        try:
            patch = self._run_agent(source, handle, grid)
            if not patch.tables:
                return self._declined(handle, sheet_scope, table_scope)
            candidate = self._build_candidate(grid, handle.sheet, patch)
            if candidate and len(candidate) <= self._policy.max_tables:
                base = score_handles(source, grid, [handle], handle.sheet)
                cand = score_handles(source, grid, candidate, handle.sheet)
                # three-way strict-better: more coverage, no new errors, no new gaps
                if cand[0] > base[0] and cand[1] <= base[1] and cand[2] <= base[2]:
                    return self._accept(candidate, grid, sheet_scope, base, cand)
            return self._reject(handle, sheet_scope, table_scope)
        except Exception:
            return SheetReview([handle], [table_scope], sheet_scope)

    # -- agent + candidate construction -------------------------------------

    def _run_agent(self, source, handle, grid) -> SheetRecutPatch:
        view = SheetView(source, handle.sheet)
        tools = build_sheet_toolset(view)
        refs = [f.ref for f in _sheet_findings(grid) ] if False else None  # noqa: (kept simple)
        seed = _structural_seed(handle)
        raw = self._runner.run(seed, tools, schema=SheetRecutPatch,
                               system=STRUCTURAL_SYSTEM)
        return SheetRecutPatch.model_validate(raw)

    def _build_candidate(self, grid, sheet, patch: SheetRecutPatch):
        out = []
        for pt in patch.tables:
            if pt.orientation != "vertical":
                continue  # transpose alteration is out of scope (detection-only)
            try:
                out.append(handle_from_region(
                    grid, sheet, pt.region, pt.header_row, pt.header_span))
            except Exception:
                continue  # a malformed region must not sink the whole proposal
        return out

    # -- outcomes -----------------------------------------------------------

    def _accept(self, candidate, grid, sheet_scope, base, cand) -> SheetReview:
        action = (f"re-cut sheet into {len(candidate)} tables "
                  f"[{', '.join(h.region for h in candidate)}]; "
                  f"coverage {base[0]}->{cand[0]}, errors {base[1]}->{cand[1]}, "
                  f"gaps {base[2]}->{cand[2]}")
        fixed = [f.model_copy(update={"resolution": "fixed", "agent_action": action})
                 for f in sheet_scope]
        per_handle, residual = [], []
        for h in candidate:
            s = scan_handle(grid, h, h.sheet)
            per_handle.append([f for f in s if f.scope != "sheet"])
            residual.extend(f for f in s if f.scope == "sheet")
        return SheetReview(list(candidate), per_handle, fixed + residual, recut=True)

    def _reject(self, handle, sheet_scope, table_scope) -> SheetReview:
        action = ("agent proposed a re-cut that did not strictly improve coverage without "
                  "adding errors — kept deterministic boundaries")
        rej = [f.model_copy(update={"resolution": "rejected", "agent_action": action})
               for f in sheet_scope]
        return SheetReview([handle], [table_scope], rej)

    def _declined(self, handle, sheet_scope, table_scope) -> SheetReview:
        action = "agent reviewed the whole sheet and proposed no re-cut"
        seen = [f.model_copy(update={"agent_action": action}) for f in sheet_scope]
        return SheetReview([handle], [table_scope], seen)


def _structural_seed(handle) -> str:
    return "\n".join([
        "A deterministic pass cut this sheet into a SINGLE table, but a whole-sheet scan "
        "found tabular data outside it (a dropped table).",
        f"Sheet: {handle.sheet}   deterministically-chosen region: {handle.region}   "
        f"header_row: {handle.header_row}",
        "Inspect the entire sheet with `dimensions`, `peek_rows`, and `peek_region`, then "
        "call `finalize` with the full set of vertical tables (region, header_row, "
        "header_span). If the single region is already correct, return empty `tables`.",
    ])
```

Then delete the dead placeholder line inside `_run_agent` (it was only illustrative). The final `_run_agent` body is:

```python
    def _run_agent(self, source, handle, grid) -> SheetRecutPatch:
        view = SheetView(source, handle.sheet)
        tools = build_sheet_toolset(view)
        seed = _structural_seed(handle)
        raw = self._runner.run(seed, tools, schema=SheetRecutPatch,
                               system=STRUCTURAL_SYSTEM)
        return SheetRecutPatch.model_validate(raw)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_structural_reviewer.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/subagent/structural.py tests/test_structural_reviewer.py
git commit -m "feat(structural): StructuralReviewer — verify-before-accept boundary re-cut"
```

---

### Task 6: Wire Layer 2 into `run_swarm` + config flag + factory

Turn the reviewer on in production: add the `alter_boundaries` config flag, a `build_structural_reviewer` factory, and the per-sheet trigger in `run_swarm` (only when an `uncovered-data` finding fired AND a reviewer is present). Preserve the never-raise contract and existing single-table IDs.

This task also adds the **live re-validation guard**: the reviewer's `recut` acceptance is proven only against the cheap static proxy, so when a re-cut is accepted `run_swarm` orchestrates both the candidate handles and the baseline handle with the REAL `subagent`/`table_validator` and keeps the re-cut only if it does not raise the live error count. The candidate orchestration is reused as the emitted tables (no double work); the baseline is orchestrated once — the only added cost, on the rare re-cut sheets. This is what makes acceptance monotone against the pipeline production actually runs, not just the static gate.

**Files:**
- Modify: `mcg_swarm/config.py` (add `alter_boundaries: bool = True`)
- Modify: `mcg_swarm/subagent/__init__.py` (add `build_structural_reviewer`)
- Modify: `mcg_swarm/runner.py` (per-sheet Layer-2 trigger + multi-handle loop)
- Test: `tests/test_runner_structural.py`

**Interfaces:**
- Consumes: `StructuralReviewer` (Task 5); `SwarmConfig` (from `mcg_swarm/config.py`); existing `scan_handle`, `orchestrate_table`.
- Produces:
  - `SwarmConfig.alter_boundaries: bool = True`.
  - `build_structural_reviewer(runner=None, config: SwarmConfig = SwarmConfig())` → `StructuralReviewer` or `None` (None when `runner is None` or `alter_boundaries` is False).
  - `run_swarm` unchanged signature; an accepted re-cut sheet yields multiple `CanonicalTable`s with IDs `{sheet}__{i}_{j}` — unless live re-validation rejects it, in which case the single baseline `{sheet}__{i}` is kept and the finding flips to `resolution="rejected"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_runner_structural.py
from mcg_swarm.config import SwarmConfig
from mcg_swarm.runner import run_swarm
from mcg_swarm.subagent import build_structural_reviewer
from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from tests.fake_source import FakeSource


def _stacked():
    v = {(1, 1): "Region", (1, 2): "Revenue",
         (2, 1): "EMEA", (2, 2): 100,
         (3, 1): "APAC", (3, 2): 200,
         (5, 1): "Product", (5, 2): "Price",
         (6, 1): "Widget", (6, 2): 49}
    return FakeSource("Data", v, {})


def test_factory_off_when_flag_false():
    r = FakeAgentRunner(actions=[], final={"tables": []})
    assert build_structural_reviewer(runner=r,
                                     config=SwarmConfig(alter_boundaries=False)) is None
    assert build_structural_reviewer(runner=None) is None
    assert build_structural_reviewer(runner=r) is not None


def test_accepted_recut_yields_two_tables():
    runner = FakeAgentRunner(actions=[], final={"tables": [
        {"region": "A1:B3", "header_row": 1},
        {"region": "A5:B6", "header_row": 5}]})
    ext = run_swarm(_stacked(), runner=runner,
                    config=SwarmConfig(validate=False, alter_boundaries=True))
    assert len(ext.tables) == 2
    regions = {t.region for t in ext.tables}
    assert regions == {"A1:B3", "A5:B6"}
    # the dropped-table signal is now marked fixed at workbook scope
    fixed = [f for f in ext.findings
             if f.category == "uncovered-data" and f.resolution == "fixed"]
    assert fixed


def test_no_runner_still_detects_only():
    # runner=None: Phase-1 detection only, single table, uncovered-data still error
    ext = run_swarm(_stacked())
    assert len(ext.tables) == 1
    assert any(f.category == "uncovered-data" and f.severity == "error"
               for f in ext.findings)


def test_recut_rejected_when_live_pipeline_regresses(monkeypatch):
    # The static-vs-live divergence, forced: the split scores strictly better on the
    # static gate (reviewer accepts, recut=True), but the LIVE per-table pipeline emits
    # an error on a candidate sub-table. The re-validation guard must keep the
    # deterministic baseline and flip the finding to rejected.
    import mcg_swarm.runner as R
    from mcg_swarm.schemas import CanonicalTable, Finding

    real = R.orchestrate_table

    def wrap(source, handle, *, table_id, **kw):
        t = real(source, handle, table_id=table_id, **kw)
        if "__0_" in table_id:   # only the accepted re-cut's candidate sub-tables
            bad = Finding(category="messy-tab", severity="error", scope="table",
                          message="injected live-pipeline regression", source="gate")
            # re-validate (NOT model_copy — that skips validators) so errors re-derive
            t = CanonicalTable.model_validate(
                {**t.model_dump(), "findings": [*[f.model_dump() for f in t.findings],
                                                bad.model_dump()]})
        return t

    monkeypatch.setattr(R, "orchestrate_table", wrap)
    runner = FakeAgentRunner(actions=[], final={"tables": [
        {"region": "A1:B3", "header_row": 1},
        {"region": "A5:B6", "header_row": 5}]})
    ext = run_swarm(_stacked(), runner=runner,
                    config=SwarmConfig(validate=False, alter_boundaries=True))
    # baseline kept (single table), detection flipped fixed -> rejected
    assert len(ext.tables) == 1
    assert any(t.region == "A1:B3" for t in ext.tables)
    assert any(f.category == "uncovered-data" and f.resolution == "rejected"
               for f in ext.findings)
    assert not any(f.resolution == "fixed" for f in ext.findings)
```

> Note: `score_handles` imports `orchestrate_table` from `mcg_swarm.orchestrator` directly, so monkeypatching `mcg_swarm.runner.orchestrate_table` affects only the runner's live orchestration/re-validation — the reviewer's static gate still accepts cleanly, which is exactly the divergence being tested.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_runner_structural.py -q`
Expected: FAIL with `ImportError: cannot import name 'build_structural_reviewer'`.

- [ ] **Step 3: Write minimal implementation**

In `mcg_swarm/config.py`, add the field to `SwarmConfig`:

```python
    alter_boundaries: bool = True
```

In `mcg_swarm/subagent/__init__.py`, add after `build_table_validator`:

```python
def build_structural_reviewer(runner=None, config: SwarmConfig = SwarmConfig()):
    """Construct the sheet-level structural reviewer, or `None`.

    None when no runner is injected or `config.alter_boundaries` is False (detection-only).
    """
    if runner is None or not config.alter_boundaries:
        return None
    from mcg_swarm.subagent.structural import StructuralReviewer
    return StructuralReviewer(runner)
```

and add `"build_structural_reviewer"` to `__all__`.

In `mcg_swarm/runner.py`, add the import:

```python
from mcg_swarm.subagent import build_subagent, build_table_validator, build_structural_reviewer
```

and replace the per-sheet loop body (lines 39-52) with:

```python
    reviewer = build_structural_reviewer(runner=runner, config=config)
    tables, sheets, wb_findings = [], [], []
    for i, h in enumerate(handles):
        sheets.append(h.sheet)
        try:
            grid = source.read_region(h.sheet)
            scan = scan_handle(grid, h, h.sheet)
        except Exception:
            grid, scan = None, []  # never let detection break extraction

        review = None
        if (reviewer is not None and grid is not None
                and any(f.category == "uncovered-data" for f in scan)):
            try:
                review = reviewer.review(source, h, grid, scan)
            except Exception:
                review = None  # never let alteration break extraction

        if review is not None and review.recut:
            # Live re-validation: the static gate proved the re-cut structurally
            # better, but the real per-table pipeline (band ReAct verifier, which
            # patches column role/dtype unconditionally, + table validator) can
            # behave differently on the smaller tables — a split can newly cross the
            # ReAct escalation threshold the monolithic baseline never hit. Never let
            # an accepted re-cut raise the live error count above the baseline.
            try:
                cand_tables = [orchestrate_table(
                        source, sh, table_id=f"{h.sheet}__{i}_{j}", llm=llm,
                        subagent=subagent, table_validator=table_validator,
                        detect_findings=tf)
                    for j, (sh, tf) in enumerate(
                        zip(review.handles, review.detect_findings))]
                base_table = orchestrate_table(
                    source, h, table_id=f"{h.sheet}__{i}", llm=llm,
                    subagent=subagent, table_validator=table_validator,
                    detect_findings=[f for f in scan if f.scope != "sheet"])
                cand_err = sum(len(t.errors) for t in cand_tables)
                base_err = len(base_table.errors)
            except Exception:
                cand_tables, base_table = None, None  # never let it break extraction

            if cand_tables is not None and cand_err <= base_err:
                tables.extend(cand_tables)
                wb_findings.extend(review.sheet_findings)      # stays 'fixed'
            else:
                # live pipeline regressed (or failed) → keep deterministic baseline,
                # flip the detection annotation from fixed to rejected.
                tables.append(base_table if base_table is not None else orchestrate_table(
                    source, h, table_id=f"{h.sheet}__{i}", llm=llm,
                    subagent=subagent, table_validator=table_validator,
                    detect_findings=[f for f in scan if f.scope != "sheet"]))
                note = "re-cut raised live-pipeline errors; kept deterministic baseline"
                wb_findings.extend(
                    f.model_copy(update={"resolution": "rejected", "agent_action": note})
                    for f in scan if f.scope == "sheet")
            continue  # tables + findings already committed for this sheet

        if review is None:
            sheet_handles = [h]
            per_handle = [[f for f in scan if f.scope != "sheet"]]
            wb_findings.extend(f for f in scan if f.scope == "sheet")
        else:
            sheet_handles = review.handles          # baseline kept (reject/declined/open)
            per_handle = review.detect_findings
            wb_findings.extend(review.sheet_findings)

        multi = len(sheet_handles) > 1
        for j, (sh, tf) in enumerate(zip(sheet_handles, per_handle)):
            table_id = f"{h.sheet}__{i}_{j}" if multi else f"{h.sheet}__{i}"
            tables.append(orchestrate_table(
                source, sh, table_id=table_id, llm=llm,
                subagent=subagent, table_validator=table_validator,
                detect_findings=tf))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_runner_structural.py tests/test_runner_detection.py tests/test_runner.py -q`
Expected: PASS (4 new structural tests incl. the live re-validation guard + existing runner/detection tests unaffected — single-table sheets keep `{sheet}__{i}` IDs).

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/config.py mcg_swarm/subagent/__init__.py mcg_swarm/runner.py tests/test_runner_structural.py
git commit -m "feat(runner): wire Layer-2 structural re-cut into run_swarm (alter_boundaries flag)"
```

---

### Task 7: End-to-end repair regression + docs

Prove the whole loop offline with a `FakeAgentRunner`: a stacked-table workbook goes from one dropped table to two extracted tables with the finding marked `fixed`; a bad proposal is rejected and detection survives. Then run the full suite and update the design/spec docs.

**Files:**
- Create: `tests/test_structural_repair_e2e.py`
- Modify: `docs/superpowers/specs/2026-06-30-boundary-detection-and-repair-design.md` (mark Phase 2 status)
- Test: full suite

**Interfaces:**
- Consumes: `run_swarm`, `SwarmConfig`, `FakeAgentRunner`, `FakeSource` — all existing.
- Produces: no new production interface (regression + docs only).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_structural_repair_e2e.py
"""End-to-end: Layer-2 turns a Phase-1 uncovered-data DETECTION into an actual repair,
offline, via a scripted FakeAgentRunner. The no-runner path stays detection-only."""
from mcg_swarm.config import SwarmConfig
from mcg_swarm.runner import run_swarm
from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from tests.fake_source import FakeSource


def _side_by_side():
    # left table cols A-B rows 1-3; right table cols D-E rows 1-3 (blank col C)
    v = {(1, 1): "Region", (1, 2): "Revenue",
         (2, 1): "EMEA", (2, 2): 100,
         (3, 1): "APAC", (3, 2): 200,
         (1, 4): "Product", (1, 5): "Price",
         (2, 4): "Widget", (2, 5): 49,
         (3, 4): "Gadget", (3, 5): 99}
    return FakeSource("Data", v, {})


def test_side_by_side_repaired_when_runner_present():
    runner = FakeAgentRunner(actions=[], final={"tables": [
        {"region": "A1:B3", "header_row": 1},
        {"region": "D1:E3", "header_row": 1}]})
    ext = run_swarm(_side_by_side(), runner=runner,
                    config=SwarmConfig(validate=False, alter_boundaries=True))
    assert {t.region for t in ext.tables} == {"A1:B3", "D1:E3"}
    assert [f for f in ext.findings
            if f.category == "uncovered-data" and f.resolution == "fixed"]


def test_side_by_side_detected_when_no_runner():
    ext = run_swarm(_side_by_side())
    assert len(ext.tables) == 1
    assert any(f.category == "uncovered-data" and f.severity == "error"
               for f in ext.findings)


def test_hallucinated_recut_rejected_no_corruption():
    # agent proposes a single wrong region that would DROP the left table's data
    runner = FakeAgentRunner(actions=[], final={"tables": [
        {"region": "D1:E3", "header_row": 1}]})
    ext = run_swarm(_side_by_side(), runner=runner,
                    config=SwarmConfig(validate=False, alter_boundaries=True))
    # baseline kept (deterministic left table), still flagged, marked rejected
    assert any(f.category == "uncovered-data" and f.resolution == "rejected"
               for f in ext.findings)
    assert any(t.region == "A1:B3" for t in ext.tables)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_structural_repair_e2e.py -q`
Expected: PASS if Tasks 1-6 are correct (this is an integration regression). If any assertion fails, fix the offending task before proceeding — do not weaken the test.

- [ ] **Step 3: Update the design doc status**

In `docs/superpowers/specs/2026-06-30-boundary-detection-and-repair-design.md`, change the `**Status:**` line near the top to:

```markdown
**Status:** Phase 1 (detection) MERGED 2026-06-30. Phase 2 (agent alteration) implemented per docs/superpowers/plans/2026-07-01-boundary-detection-phase2.md — vertical re-cuts only; transpose stays detection-only.
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — 277 prior + new tests, 1 skipped, zero failures.

- [ ] **Step 5: Commit**

```bash
git add tests/test_structural_repair_e2e.py docs/superpowers/specs/2026-06-30-boundary-detection-and-repair-design.md
git commit -m "test(structural): end-to-end re-cut repair regression + spec status"
```

---

## Self-Review

**Spec coverage** (against `2026-06-30-boundary-detection-and-repair-design.md`, Layer 2):
- "structural agent sees the full sheet grid + current handles + findings" → Task 3 (`SheetView`/toolset) + Task 5 seed. ✓
- "proposes candidate handle set(s): split into N tables / re-anchor header / fix span" → Task 4 schema + Task 1 builder (split + re-anchor via explicit header_row/span). ✓
- "score_handles(candidate, grid) → (coverage, gate_errors)" → Task 4 `score_handles`, hardened to `(coverage, errors, gaps)`. ✓
- "accept ONLY if strictly better … more covered non-empty cells AND no new errors; else keep baseline" → Task 5 three-way acceptance rule (coverage↑, errors≤, gaps≤). ✓
- **Over-claim guard (added):** `coverage_score` is a monotone count of claimed non-empty cells and `uncovered-data` errors fire only *outside* a region, so a giant region inflates coverage while dropping residue errors — both static signals move together. `_region_gaps` (Task 4) breaks the tie by counting interior blank rows/cols, which a fused-two-tables region has and a coherent one does not. Covered by `test_overclaiming_region_is_penalised` (Task 4) and `test_overclaiming_recut_rejected` (Task 5). ✓
- **Static-vs-live reconciliation (added):** the static gate is a cheap proxy that skips the band ReAct verifier (unconditional role/dtype patch) and table validator, and a split can newly cross the ReAct escalation threshold. Task 6 re-validates an accepted re-cut against the baseline with the real `subagent`/`table_validator` and keeps it only if the live error count does not rise; otherwise baseline is kept and the finding flips to `rejected`. Covered by `test_recut_rejected_when_live_pipeline_regresses` (Task 6). ✓
- "record outcome on the Finding: agent_action + resolution(fixed|rejected|open)" → Task 5 `_accept`/`_reject`/`_declined` + Task 6 live-regression flip to `rejected`. ✓
- "never raise (any failure → baseline + findings unchanged)" → Task 5 outer try/except + Task 6 try/except around both the reviewer call and the live re-validation orchestration. ✓
- "With no runner, Layer 1 still runs in full" → Task 6 `test_no_runner_still_detects_only`. ✓
- "transpose alteration … if it can't be scored as strictly better it stays detection-only" → Global Constraint + Task 5 `_build_candidate` skips transposed. ✓
- Reuses injected `AgentRunner` DI seam → Task 5 uses `runner`; Task 2 adds the system-prompt override the structural task needs. ✓

**Placeholder scan:** No TBD/TODO. The one illustrative dead line in Task 5's first `_run_agent` draft is explicitly deleted in the same task (Step 3 shows the final body). Every code step contains full code.

**Type consistency:** `handle_from_region(grid, sheet, region, header_row, header_span=1)` — same signature in Tasks 1, 4, 5. `score_handles(source, grid, handles, sheet) -> (int, int, int)` — three-way tuple, same in Tasks 4, 5; every acceptance predicate reads `cand[0]/cand[1]/cand[2]`. `SheetReview(handles, detect_findings, sheet_findings, recut=False)` — `recut` produced in Task 5 `_accept`, consumed in Task 6 to trigger live re-validation. `run(..., system=None)` — added Task 2, used Task 5. `build_structural_reviewer(runner, config)` — Task 6. `orchestrate_table` public signature accepts `table_validator=` (line 176) — the static gate passes `table_validator=None`, the live re-validation passes the real one. Trigger category string `"uncovered-data"` matches `coverage.py`. Region/header coordinates are absolute throughout.

**Cost note:** Layer 2 fires only when an `uncovered-data` finding is present (rare — 3 of 18 battery cases), and `score_handles` orchestrates candidates statically (no live agent). The live re-validation adds exactly one extra baseline orchestration per *accepted* re-cut (candidate orchestration is reused as the emitted tables, not repeated) — bounded to the same rare sheets. The 13 clean workbooks never invoke any of it.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-01-boundary-detection-phase2.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
