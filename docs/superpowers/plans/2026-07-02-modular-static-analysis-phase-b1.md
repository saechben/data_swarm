# Modular Static Analysis — Phase B1 Implementation Plan (Views + Rich Contract + Assessor Hardening)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the deterministic half of spec Phase B — the `TransposedView` normalization seam, the rich per-sheet contract (`SheetAnalysis` / `analyze_workbook` surfacing view/method/findings), never-raise + stub-fallback hardening, widened dedup, float coverage, and `score_handles`-based candidate ranking — all corpus-neutral with the default config.

**Architecture:** A `TransposedView` wraps any `WorkbookSource` and swaps axes, so analyzers hand downstream a canonical vertical view (spec §4.3). A new `analyzers/pipeline.py` runs the active lenses per sheet with a never-raise guard, assesses, and returns rich `SheetAnalysis` records; `split_workbook` becomes a back-compat shim and `run_swarm` consumes the rich records, reading through `view or source`. `assess.py` gains `rank_candidates`/`assess_sheet` using the existing three-way `score_handles` metric (coverage ↑, errors ↓, gaps ↓) plus a dominance short-circuit — the engine the Phase B2 arbiter will plug into.

**Tech Stack:** Python 3, `dataclasses`, `typing.Protocol`, pytest. No new dependencies. Deterministic only — no runner/LLM in B1 (that is B2).

## Global Constraints

- **Corpus neutrality with default config is the exit criterion** (spec §6 Phase B: "no eval regression"). With `SwarmConfig()` (analyzers=`("vertical",)`, no runner), `run_swarm` output must be byte-identical to current `main`. The controller runs the corpus diff (same procedure as Phase A); each task's suite run is the local gate.
- Test command is `.venv/bin/python -m pytest -q` (NOT bare `pytest`). Baseline before Task 1: **322 passed, 1 skipped**. Zero pre-existing failures tolerated at any commit.
- `detect_table`, `TableHandle`, and all splitter helpers stay UNCHANGED. `split_workbook(source, config=None) -> list[TableHandle]` keeps its signature and return type as a back-compat shim (~15 test files call `split_workbook(p)[0]`).
- The Layer-2 `StructuralReviewer` and the `run_swarm` re-cut branch stay functionally intact in B1 — subsumption is B2. Do not delete or bypass the reviewer flow; only thread the new types around it.
- `run_swarm`'s fail-fast `build_analyzers(config.analyzers)` validation call BEFORE the `try:` (commit 7afe196) must remain — `analyze_workbook` runs inside the `try`, so removing the pre-validation would re-swallow config `KeyError`s.
- Analyzers/pipeline never raise (spec §5): a lens exception becomes a `Finding(severity="warning")`; zero candidates for a sheet becomes an ambiguous stub handle. `Finding` requires `message` (schemas.py:25) — always set it.
- Import direction: `analyzers/*` may import `splitter`/`schemas`/`source`/`coverage` at top level; `splitter` imports `analyzers` ONLY lazily inside function bodies. `assess.py` imports `subagent.structural` (for `score_handles`) ONLY lazily inside `rank_candidates` — structural pulls in the whole orchestration stack.
- Spec: `docs/superpowers/specs/2026-07-01-modular-static-analysis-design.md`. This plan implements the deterministic half of **Phase B**; the agentic half (arbiter, verify-before-accept floor, live re-validation generalization, StructuralReviewer subsumption) is **Plan B2**, written after B1 merges.

### Deferred-items ledger coverage (from `.superpowers/sdd/progress.md` final review)
- assess([]) never-raise + stub fallback → Task 3. Dedup signature widening → Task 2. Coverage int-vs-float spec alignment → Task 2. `split_workbook` drops findings/method → Task 3 (`SheetAnalysis` surfaces them; shim documents the narrowing).

---

## File Structure

**Create:**
- `mcg_swarm/views.py` — `TransposedView` (WorkbookSource decorator). Identity = `None`, no class (spec §4.3 "effectively None").
- `mcg_swarm/analyzers/pipeline.py` — `analyze_sheet` / `analyze_workbook` + fallback stub.
- `tests/test_views.py`, `tests/test_pipeline.py`, `tests/test_assess_sheet.py`.

**Modify:**
- `mcg_swarm/analyzers/base.py` — `LayoutCandidate.view` field, `coverage: float`, new `SheetAnalysis` dataclass.
- `mcg_swarm/analyzers/vertical.py` — coverage as fraction.
- `mcg_swarm/analyzers/assess.py` — widened `_signature`; add `rank_candidates`, `_dominates`, `assess_sheet`.
- `mcg_swarm/analyzers/__init__.py` — export new names.
- `mcg_swarm/splitter.py` — `split_workbook` body becomes the shim over `analyze_workbook`.
- `mcg_swarm/runner.py` — consume `analyze_workbook`, thread `view or source`.
- `tests/test_analyzers.py` — coverage-float updates + new dedup tests.

---

### Task 1: `TransposedView` (the normalization seam)

**Files:**
- Create: `mcg_swarm/views.py`
- Test: `tests/test_views.py`

**Interfaces:**
- Consumes: `WorkbookSource` Protocol (`mcg_swarm/source.py:11` — `sheet_names()`, `read_region(sheet, min_row=None, min_col=None, max_row=None, max_col=None)`, `read_cell(sheet, row, col)`, `read_formula_region(...)`).
- Produces: `TransposedView(inner: WorkbookSource)` — itself a `WorkbookSource` (satisfies the runtime_checkable Protocol) presenting rows/columns swapped. Task 5 passes instances as the per-table source; Phase C's transpose lens will construct them.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_views.py`:

```python
"""TransposedView: downstream sees a vertical table; the raw sheet is horizontal."""
from mcg_swarm.views import TransposedView
from mcg_swarm.source import WorkbookSource
from mcg_swarm.splitter import detect_table
from mcg_swarm.extraction import build_index


class _GridSource:
    """Minimal in-memory WorkbookSource over {sheet: list[tuple]} grids."""

    def __init__(self, sheets):
        self._sheets = sheets

    def sheet_names(self):
        return list(self._sheets)

    def _window(self, grid, min_row, min_col, max_row, max_col):
        n_rows = len(grid)
        n_cols = max((len(r) for r in grid), default=0)
        r0 = 1 if min_row is None else min_row
        c0 = 1 if min_col is None else min_col
        r1 = n_rows if max_row is None else max_row
        c1 = n_cols if max_col is None else max_col
        out = []
        for r in range(r0, r1 + 1):
            row = grid[r - 1] if r - 1 < len(grid) else ()
            out.append(tuple(row[c - 1] if c - 1 < len(row) else None
                             for c in range(c0, c1 + 1)))
        return out

    def read_region(self, sheet, min_row=None, min_col=None, max_row=None, max_col=None):
        return self._window(self._sheets[sheet], min_row, min_col, max_row, max_col)

    def read_cell(self, sheet, row, col):
        grid = self._sheets[sheet]
        r = grid[row - 1] if row - 1 < len(grid) else ()
        return r[col - 1] if col - 1 < len(r) else None

    def read_formula_region(self, sheet, min_row=None, min_col=None, max_row=None, max_col=None):
        return self.read_region(sheet, min_row, min_col, max_row, max_col)


# Horizontal (transposed) layout: fields as rows, records as columns.
_HORIZONTAL = {"S": [("Region", "North", "South"),
                     ("Sales", 10, 20)]}


def test_view_satisfies_workbook_source_protocol():
    view = TransposedView(_GridSource(_HORIZONTAL))
    assert isinstance(view, WorkbookSource)
    assert view.sheet_names() == ["S"]


def test_full_sheet_read_region_is_transposed():
    view = TransposedView(_GridSource(_HORIZONTAL))
    assert view.read_region("S") == [("Region", "Sales"),
                                     ("North", 10),
                                     ("South", 20)]


def test_read_cell_swaps_axes():
    view = TransposedView(_GridSource(_HORIZONTAL))
    # view (row=3, col=2) == inner (row=2, col=3) == 20
    assert view.read_cell("S", 3, 2) == 20


def test_bounded_read_region_window_in_view_coords():
    view = TransposedView(_GridSource(_HORIZONTAL))
    # view rows 2..3, col 2 == the Sales values column
    assert view.read_region("S", min_row=2, min_col=2, max_row=3, max_col=2) == [(10,), (20,)]


def test_formula_region_transposed_too():
    view = TransposedView(_GridSource(_HORIZONTAL))
    assert view.read_formula_region("S")[1] == ("North", 10)


def test_downstream_index_resolves_correct_axis_through_view():
    """Spec §7: build_index through a TransposedView with NO band-layer changes."""
    view = TransposedView(_GridSource(_HORIZONTAL))
    handle = detect_table(view.read_region("S"), "S")  # sees a normal vertical table
    assert handle.region == "A1:B3" and handle.header_row == 1
    idx = build_index(view, handle, row_key=["Region"])
    assert idx.query("North", "Sales").value == 10
    assert idx.query("South", "Sales").value == 20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_views.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcg_swarm.views'`.

- [ ] **Step 3: Write `TransposedView`**

Create `mcg_swarm/views.py`:

```python
"""SourceView decorators (spec §4.3) — WorkbookSource wrappers presenting a
transformed coordinate system, so downstream (bands, index, gate) only ever
sees canonical vertical tables. Identity is represented by ``None`` (no wrapper).
"""
from __future__ import annotations

from mcg_swarm.source import WorkbookSource


def _transpose(rows) -> list[tuple]:
    """Transpose a list of row tuples, padding ragged rows with None."""
    rows = list(rows)
    if not rows:
        return []
    width = max(len(r) for r in rows)
    padded = [tuple(r) + (None,) * (width - len(r)) for r in rows]
    return [tuple(col) for col in zip(*padded)]


class TransposedView:
    """Present ``inner``'s sheets with rows and columns swapped.

    A cell at (row=r, col=c) in this view reads inner cell (row=c, col=r).
    An analyzer that detects a fields-as-rows table attaches this view and
    expresses its TableHandle in VIEW coordinates; downstream reads through
    the view and stays vertical-only by construction (spec §2 principle 2).
    """

    def __init__(self, inner: WorkbookSource) -> None:
        self._inner = inner

    def sheet_names(self) -> list[str]:
        return self._inner.sheet_names()

    def read_cell(self, sheet, row, col):
        return self._inner.read_cell(sheet, col, row)

    def read_region(self, sheet, min_row=None, min_col=None, max_row=None, max_col=None):
        return _transpose(self._inner.read_region(
            sheet, min_row=min_col, min_col=min_row,
            max_row=max_col, max_col=max_row))

    def read_formula_region(self, sheet, min_row=None, min_col=None, max_row=None, max_col=None):
        return _transpose(self._inner.read_formula_region(
            sheet, min_row=min_col, min_col=min_row,
            max_row=max_col, max_col=max_row))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_views.py -v`
Expected: PASS (6 tests). The last test is the load-bearing one: `build_index`/`query` resolve the correct axis with zero changes to extraction code.

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/views.py tests/test_views.py
git commit -m "feat(views): TransposedView — axis-swapping WorkbookSource decorator"
```

---

### Task 2: Candidate plumbing — `view` field, float coverage, widened dedup

**Files:**
- Modify: `mcg_swarm/analyzers/base.py`
- Modify: `mcg_swarm/analyzers/vertical.py`
- Modify: `mcg_swarm/analyzers/assess.py` (only `_signature`)
- Test: `tests/test_analyzers.py` (edit 3 existing tests, add 3)

**Interfaces:**
- Consumes: `LayoutCandidate` (base.py), `nonempty_cells`/`coverage_score` (`mcg_swarm/coverage.py:20,35`).
- Produces: `LayoutCandidate.view: Any = None` and `coverage: float = 0.0` (fraction of the sheet's non-empty cells claimed — spec §4.2 alignment); `_signature` distinguishing header_row/header_span/view. Tasks 3-5 rely on the `view` field.

- [ ] **Step 1: Update existing tests + add failing tests**

In `tests/test_analyzers.py`:

(a) In `test_layout_candidate_defaults`, change the coverage assertion and add view:

```python
    assert c.coverage == 0.0
    assert c.view is None
```

(b) Replace `test_vertical_analyzer_sets_coverage` with the fraction version:

```python
def test_vertical_analyzer_sets_coverage():
    from mcg_swarm.coverage import coverage_score, nonempty_cells
    a = VerticalSplitAnalyzer()
    c = a.analyze(_GRID, "Sheet1")[0]
    expected = coverage_score(_GRID, [c.handles[0].region]) / len(nonempty_cells(_GRID))
    assert c.coverage == expected
    assert 0.0 < c.coverage <= 1.0
```

(c) Append new dedup tests (the existing `_cand` helper builds handles with `header_row=1`):

```python
def _cand_at(method, region, header_row, header_span=1, confidence=1.0):
    h = TableHandle("S", region, header_row, header_span=header_span)
    return LayoutCandidate(method=method, handles=(h,), coverage=0.5,
                           confidence=confidence)


def test_dedup_distinguishes_header_row():
    a = _cand_at("a", "A1:B3", 1)
    b = _cand_at("b", "A1:B3", 2)   # same region, different header interpretation
    got = assess([a, b])
    assert got in (a, b)            # ranked, not collapsed
    # both signatures survive dedup: removing either input changes nothing
    assert assess([a]) is a and assess([b]) is b


def test_dedup_distinguishes_header_span():
    a = _cand_at("a", "A1:B4", 1, header_span=1, confidence=0.4)
    b = _cand_at("b", "A1:B4", 1, header_span=2, confidence=0.9)
    # different span -> different signature -> NOT collapsed by confidence
    assert assess([a, b]) in (a, b)
    assert assess([b, a]) in (a, b)


def test_dedup_distinguishes_view():
    from mcg_swarm.views import TransposedView
    plain = _cand_at("a", "A1:B3", 1, confidence=0.9)
    h = TableHandle("S", "A1:B3", 1)
    viewed = LayoutCandidate(method="b", handles=(h,), coverage=0.5,
                             confidence=0.4, view=TransposedView(None))
    # same region but one reads through a view: different interpretations
    winner = assess([plain, viewed])
    assert winner is plain          # higher confidence wins the rank, not the dedup
```

- [ ] **Step 2: Run tests to verify failures**

Run: `.venv/bin/python -m pytest tests/test_analyzers.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'view'`, and the coverage-fraction assertion fails (int count vs fraction).

- [ ] **Step 3: Update `base.py`**

In `mcg_swarm/analyzers/base.py` add `Any` to imports and change the dataclass fields:

```python
from typing import Any, Protocol, runtime_checkable
```

```python
    method: str
    handles: tuple[TableHandle, ...]
    coverage: float = 0.0
    findings: tuple[Finding, ...] = ()
    confidence: float = 1.0
    view: Any = None
```

Update the docstring lines for the two changed fields:

```python
    coverage:   fraction of the sheet's non-empty cells claimed by the handles
                (0.0-1.0; spec §4.2).
    view:       normalizing WorkbookSource wrapper (e.g. TransposedView) whose
                coordinates the handles are expressed in; None = identity.
```

- [ ] **Step 4: Update `vertical.py` to emit the fraction**

```python
from mcg_swarm.analyzers.base import LayoutCandidate
from mcg_swarm.coverage import coverage_score, nonempty_cells
from mcg_swarm.splitter import detect_table
```

and in `analyze`:

```python
    def analyze(self, grid: list[tuple], sheet: str) -> list[LayoutCandidate]:
        handle = detect_table(grid, sheet)
        total = len(nonempty_cells(grid))
        covered = coverage_score(grid, [handle.region])
        coverage = covered / total if total else 0.0
        return [LayoutCandidate(method="vertical", handles=(handle,), coverage=coverage)]
```

- [ ] **Step 5: Widen `_signature` in `assess.py`**

```python
def _signature(candidate: LayoutCandidate) -> tuple:
    """Interpretation identity of a candidate. Two candidates are the same
    interpretation only if they claim the same regions WITH the same header
    placement/span AND read through the same kind of view."""
    view_tag = type(candidate.view).__name__ if candidate.view is not None else ""
    return (view_tag, tuple(sorted(
        (h.region, h.header_row, h.header_span) for h in candidate.handles)))
```

- [ ] **Step 6: Run the full suite (behavioral gate)**

Run: `.venv/bin/python -m pytest -q`
Expected: all green (322 baseline − 0 regressions + 3 new = **325 passed, 1 skipped**). Coverage is only a ranking input with an identical per-sheet denominator, and single-candidate assessment is an identity passthrough — so this task cannot change any extraction output.

- [ ] **Step 7: Commit**

```bash
git add mcg_swarm/analyzers/base.py mcg_swarm/analyzers/vertical.py mcg_swarm/analyzers/assess.py tests/test_analyzers.py
git commit -m "feat(analyzers): view field, fractional coverage, interpretation-aware dedup"
```

---

### Task 3: `SheetAnalysis` + `analyze_workbook` pipeline (never-raise, stub fallback, rich contract)

**Files:**
- Modify: `mcg_swarm/analyzers/base.py` (add `SheetAnalysis`)
- Create: `mcg_swarm/analyzers/pipeline.py`
- Modify: `mcg_swarm/analyzers/__init__.py` (exports)
- Modify: `mcg_swarm/splitter.py` (shim body of `split_workbook`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `assess` (Task 2 form), `build_analyzers`, `LayoutCandidate`, `TableHandle`, `Finding(category, severity, scope, message, source, ref=None)` (schemas.py:25 — `message` required), `as_source` (source.py:95), `SwarmConfig` (config.py).
- Produces (Task 5 consumes these exactly):
  - `SheetAnalysis(sheet: str, handles: tuple[TableHandle, ...], view: Any = None, method: str = "vertical", findings: tuple[Finding, ...] = ())` — frozen dataclass in base.py.
  - `analyze_sheet(analyzers, grid, sheet) -> SheetAnalysis` and `analyze_workbook(source, config=None) -> list[SheetAnalysis]` in pipeline.py.
  - `split_workbook(source, config=None) -> list[TableHandle]` unchanged signature, now a shim.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pipeline.py`:

```python
"""analyze_workbook: rich per-sheet contract, never-raise, stub fallback."""
import pytest

from mcg_swarm.analyzers.pipeline import analyze_workbook, analyze_sheet
from mcg_swarm.analyzers.base import SheetAnalysis
from mcg_swarm.analyzers.registry import register, build_analyzers
from mcg_swarm.config import SwarmConfig
from mcg_swarm.splitter import split_workbook, detect_table
from tests.test_views import _GridSource

_SHEETS = {
    "Sales": [("Region", "Sales"), ("North", 10), ("South", 20)],
    "Costs": [("Dept", "Cost"), ("Eng", 100), ("Ops", 50)],
}


def test_analyze_workbook_default_vertical():
    out = analyze_workbook(_GridSource(_SHEETS))
    assert [sa.sheet for sa in out] == ["Sales", "Costs"]
    for sa in out:
        assert isinstance(sa, SheetAnalysis)
        assert sa.method == "vertical" and sa.view is None
        assert sa.handles == (detect_table(_SHEETS[sa.sheet], sa.sheet),)
        assert sa.findings == ()


def test_split_workbook_shim_still_flat_handles():
    src = _GridSource(_SHEETS)
    assert split_workbook(src) == [detect_table(g, n) for n, g in _SHEETS.items()]


class _RaisingLens:
    name = "raising"
    def analyze(self, grid, sheet):
        raise RuntimeError("boom")


class _EmptyLens:
    name = "empty"
    def analyze(self, grid, sheet):
        return []


def test_lens_exception_becomes_finding_not_crash():
    register("raising", _RaisingLens)
    analyzers = build_analyzers(("vertical", "raising"))
    sa = analyze_sheet(analyzers, _SHEETS["Sales"], "Sales")
    assert sa.method == "vertical"                       # vertical still wins
    cats = [(f.category, f.severity) for f in sa.findings]
    assert ("analyzer-error", "warning") in cats
    msg = next(f.message for f in sa.findings if f.category == "analyzer-error")
    assert "raising" in msg and "boom" in msg


def test_all_lenses_empty_falls_back_to_stub():
    register("empty", _EmptyLens)
    sa = analyze_sheet(build_analyzers(("empty",)), _SHEETS["Sales"], "Sales")
    assert sa.method == "fallback"
    assert len(sa.handles) == 1
    stub = sa.handles[0]
    assert stub.ambiguous and stub.region == "A1:A1"
    assert stub.reason == "no analyzer produced a candidate"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcg_swarm.analyzers.pipeline'`.

- [ ] **Step 3: Add `SheetAnalysis` to `base.py`**

Append after `LayoutCandidate` (uses the already-imported `dataclass`, `Any`, `Finding`, `TableHandle`):

```python
@dataclass(frozen=True)
class SheetAnalysis:
    """The assessed result for one sheet — the analyze→orchestrate contract.

    handles:  winning candidate's tables, in view coordinates.
    view:     normalizing WorkbookSource wrapper (None = identity) — downstream
              must read through `view or source`.
    method:   which analyzer won ("fallback" = no candidate; ambiguous stub).
    findings: lens failures + winning candidate's findings (sheet scope).
    """

    sheet: str
    handles: tuple[TableHandle, ...]
    view: Any = None
    method: str = "vertical"
    findings: tuple[Finding, ...] = ()
```

- [ ] **Step 4: Create `pipeline.py`**

Create `mcg_swarm/analyzers/pipeline.py`:

```python
"""Per-sheet analysis pipeline: run the active lenses, assess, return rich results.

Never raises (spec §5): a lens exception becomes a warning Finding; zero
candidates becomes an ambiguous stub handle (today's no-header behavior).
"""
from __future__ import annotations

from mcg_swarm.analyzers.assess import assess
from mcg_swarm.analyzers.base import LayoutCandidate, SheetAnalysis
from mcg_swarm.analyzers.registry import build_analyzers
from mcg_swarm.config import SwarmConfig
from mcg_swarm.schemas import Finding
from mcg_swarm.source import as_source
from mcg_swarm.splitter import TableHandle


def _fallback_candidate(sheet: str) -> LayoutCandidate:
    stub = TableHandle(sheet, "A1:A1", 1, [], ambiguous=True,
                       reason="no analyzer produced a candidate")
    return LayoutCandidate(method="fallback", handles=(stub,))


def analyze_sheet(analyzers, grid: list[tuple], sheet: str) -> SheetAnalysis:
    candidates: list[LayoutCandidate] = []
    findings: list[Finding] = []
    for a in analyzers:
        try:
            candidates.extend(a.analyze(grid, sheet))
        except Exception as e:  # lens failure is a finding, never a crash (spec §5)
            findings.append(Finding(
                category="analyzer-error", severity="warning", scope="sheet",
                message=f"analyzer {a.name!r} failed: {e}", source="static",
                ref=f"{sheet}!A1"))
    winner = assess(candidates) if candidates else _fallback_candidate(sheet)
    return SheetAnalysis(sheet=sheet, handles=winner.handles, view=winner.view,
                         method=winner.method,
                         findings=tuple(findings) + winner.findings)


def analyze_workbook(source, config: SwarmConfig | None = None) -> list[SheetAnalysis]:
    """Run the active analyzer lenses over every sheet. The rich counterpart of
    split_workbook — surfaces view/method/findings per sheet (spec §4.6)."""
    if config is None:
        config = SwarmConfig()
    src = as_source(source)
    analyzers = build_analyzers(config.analyzers)
    return [analyze_sheet(analyzers, src.read_region(name), name)
            for name in src.sheet_names()]
```

- [ ] **Step 5: Turn `split_workbook` into the shim**

Replace the body of `split_workbook` in `mcg_swarm/splitter.py` with:

```python
def split_workbook(source, config=None) -> list[TableHandle]:
    """Split a workbook into TableHandles via the active analyzer lenses.

    Back-compat shim over ``analyze_workbook()``: flattens the per-sheet winning
    handles and drops per-sheet view/method/findings. Rich callers (run_swarm)
    use ``mcg_swarm.analyzers.pipeline.analyze_workbook`` directly.
    """
    # Lazy import: analyzers import TableHandle/detect_table from this module.
    from mcg_swarm.analyzers.pipeline import analyze_workbook
    return [h for sa in analyze_workbook(source, config) for h in sa.handles]
```

- [ ] **Step 6: Export from the package**

`mcg_swarm/analyzers/__init__.py` (keep the module docstring):

```python
from mcg_swarm.analyzers.assess import assess
from mcg_swarm.analyzers.base import LayoutCandidate, SheetAnalysis, SheetAnalyzer
from mcg_swarm.analyzers.pipeline import analyze_sheet, analyze_workbook
from mcg_swarm.analyzers.registry import build_analyzers, register

__all__ = ["LayoutCandidate", "SheetAnalysis", "SheetAnalyzer", "analyze_sheet",
           "analyze_workbook", "assess", "build_analyzers", "register"]
```

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: **329 passed, 1 skipped** (325 + 4 new), zero failures — every `split_workbook(p)[0]` caller and both neutrality tests must stay green (the shim reproduces the Phase-A flow exactly: same lenses, same assess, same flatten).

- [ ] **Step 8: Commit**

```bash
git add mcg_swarm/analyzers/base.py mcg_swarm/analyzers/pipeline.py mcg_swarm/analyzers/__init__.py mcg_swarm/splitter.py tests/test_pipeline.py
git commit -m "feat(analyzers): SheetAnalysis + analyze_workbook pipeline (never-raise, stub fallback)"
```

---

### Task 4: Rich deterministic ranking — `rank_candidates` / `assess_sheet`

**Files:**
- Modify: `mcg_swarm/analyzers/assess.py`
- Modify: `mcg_swarm/analyzers/__init__.py` (export `assess_sheet`)
- Test: `tests/test_assess_sheet.py`

**Interfaces:**
- Consumes: `score_handles(source, grid, handles, sheet) -> tuple[int, int, int]` (`mcg_swarm/subagent/structural.py:67` — coverage↑, errors↓, gaps↓; runs static-only orchestration internally, so it MUST be imported lazily inside the function), `_signature`/dedup from Task 2, `handle_from_region` (`mcg_swarm/splitter.py:274`) in tests.
- Produces (Plan B2's arbiter plugs in between rank and return):
  - `rank_candidates(candidates, *, source, grid, sheet) -> list[tuple[LayoutCandidate, tuple[int, int, int]]]` — deduped, best-first.
  - `_dominates(score_a, score_b) -> bool` — not-worse on all three axes.
  - `assess_sheet(candidates, *, source, grid, sheet) -> LayoutCandidate` — B1: dedup → single passthrough → else rank → top. (B2 inserts the arbiter when top does NOT dominate the runner-up.)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_assess_sheet.py`:

```python
"""Rich deterministic ranking: score_handles-based, dominance-aware."""
import pytest

from mcg_swarm.analyzers.assess import assess_sheet, rank_candidates, _dominates
from mcg_swarm.analyzers.base import LayoutCandidate
from mcg_swarm.splitter import detect_table, handle_from_region
from tests.test_views import _GridSource

# Two stacked tables separated by a blank row — the canonical A1-violation sheet.
_TWO_TABLES = [
    ("Region", "Sales"),
    ("North", 10),
    ("South", 20),
    (None, None),
    ("Dept", "Cost"),
    ("Eng", 100),
    ("Ops", 50),
]
_SRC = _GridSource({"S": _TWO_TABLES})


def _baseline():
    """What today's splitter sees: the top table only."""
    h = detect_table(_TWO_TABLES, "S")
    return LayoutCandidate(method="vertical", handles=(h,))


def _clean_pair():
    """The correct interpretation: both tables, tightly cut."""
    top = handle_from_region(_TWO_TABLES, "S", "A1:B3", 1)
    bottom = handle_from_region(_TWO_TABLES, "S", "A5:B7", 5)
    return LayoutCandidate(method="multitable", handles=(top, bottom))


def _fused():
    """The greedy over-claim: one region swallowing both + the gap row."""
    h = handle_from_region(_TWO_TABLES, "S", "A1:B7", 1)
    return LayoutCandidate(method="fused", handles=(h,))


def test_dominates_semantics():
    assert _dominates((12, 0, 0), (6, 0, 0))       # more coverage
    assert _dominates((12, 0, 0), (12, 1, 0))      # fewer errors
    assert _dominates((12, 0, 0), (12, 0, 1))      # fewer gaps
    assert _dominates((12, 0, 0), (12, 0, 0))      # ties dominate (short-circuit)
    assert not _dominates((12, 1, 0), (6, 0, 0))   # trade-off = genuine disagreement


def test_clean_pair_outranks_baseline_and_fused():
    ranked = rank_candidates([_baseline(), _clean_pair(), _fused()],
                             source=_SRC, grid=_TWO_TABLES, sheet="S")
    assert ranked[0][0].method == "multitable"
    # the pair claims every non-empty cell with zero interior gaps
    cov, errors, gaps = ranked[0][1]
    assert cov == 12 and gaps == 0
    # the fused over-claim is penalized by its interior blank row
    fused_score = next(s for c, s in ranked if c.method == "fused")
    assert fused_score[2] >= 1


def test_assess_sheet_picks_clean_pair():
    winner = assess_sheet([_baseline(), _clean_pair(), _fused()],
                          source=_SRC, grid=_TWO_TABLES, sheet="S")
    assert winner.method == "multitable"
    assert len(winner.handles) == 2


def test_assess_sheet_single_candidate_identity():
    c = _baseline()
    assert assess_sheet([c], source=_SRC, grid=_TWO_TABLES, sheet="S") is c


def test_assess_sheet_empty_raises():
    with pytest.raises(ValueError):
        assess_sheet([], source=_SRC, grid=_TWO_TABLES, sheet="S")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_assess_sheet.py -v`
Expected: FAIL — `ImportError: cannot import name 'assess_sheet'`.

- [ ] **Step 3: Implement in `assess.py`**

Append to `mcg_swarm/analyzers/assess.py` (keep `assess` and `_signature` as-is; reuse the dedup logic via a small extraction):

```python
def _dedup(candidates: list[LayoutCandidate]) -> list[LayoutCandidate]:
    """Stage 0: collapse identical interpretations, keeping highest confidence."""
    best_by_sig: dict[tuple, LayoutCandidate] = {}
    for c in candidates:
        sig = _signature(c)
        cur = best_by_sig.get(sig)
        if cur is None or c.confidence > cur.confidence:
            best_by_sig[sig] = c
    return list(best_by_sig.values())


def _dominates(score_a: tuple, score_b: tuple) -> bool:
    """True when score_a is not-worse than score_b on all three axes
    (coverage higher-or-equal, errors and gaps lower-or-equal)."""
    return (score_a[0] >= score_b[0]
            and score_a[1] <= score_b[1]
            and score_a[2] <= score_b[2])


def rank_candidates(candidates: list[LayoutCandidate], *, source, grid,
                    sheet: str) -> list:
    """Stage 1 (rich): score each deduped candidate with the Layer-2 three-way
    metric and rank best-first: coverage desc, errors asc, gaps asc, confidence
    desc. Returns [(candidate, (coverage, errors, gaps)), ...]."""
    # Lazy: structural pulls in the orchestration stack; keep analyzers light.
    from mcg_swarm.subagent.structural import score_handles

    deduped = _dedup(candidates)
    scored = [(c, score_handles(source, grid, list(c.handles), sheet))
              for c in deduped]
    scored.sort(key=lambda cs: (-cs[1][0], cs[1][1], cs[1][2], -cs[0].confidence))
    return scored


def assess_sheet(candidates: list[LayoutCandidate], *, source, grid,
                 sheet: str) -> LayoutCandidate:
    """Full deterministic sheet assessment (spec §4.5 Stages 0-1).

    Single candidate is returned by identity (the neutrality anchor). Plan B2
    inserts the agentic arbiter where the top candidate does NOT dominate the
    runner-up (genuine disagreement)."""
    if not candidates:
        raise ValueError("assess_sheet requires at least one candidate")
    deduped = _dedup(candidates)
    if len(deduped) == 1:
        return deduped[0]
    ranked = rank_candidates(deduped, source=source, grid=grid, sheet=sheet)
    return ranked[0][0]
```

- [ ] **Step 4: Export `assess_sheet`**

Add `assess_sheet` to the imports and `__all__` in `mcg_swarm/analyzers/__init__.py`:

```python
from mcg_swarm.analyzers.assess import assess, assess_sheet
```

and append `"assess_sheet"` to `__all__`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_assess_sheet.py tests/test_analyzers.py -v`
Expected: PASS (5 new + all existing). The `test_clean_pair_outranks_baseline_and_fused` test is the Phase-C rehearsal: a multi-table interpretation deterministically beats both today's baseline and a greedy over-claim.

- [ ] **Step 6: Commit**

```bash
git add mcg_swarm/analyzers/assess.py mcg_swarm/analyzers/__init__.py tests/test_assess_sheet.py
git commit -m "feat(analyzers): score_handles-based rank_candidates + assess_sheet with dominance"
```

---

### Task 5: `run_swarm` consumes the rich contract (view threading + neutrality gates)

**Files:**
- Modify: `mcg_swarm/runner.py`
- Test: `tests/test_split_neutrality.py` (append 2)

**Interfaces:**
- Consumes: `analyze_workbook(source, config=None) -> list[SheetAnalysis]` (Task 3), `SheetAnalysis(sheet, handles, view, method, findings)`.
- Produces: `run_swarm` behavior — byte-identical for default config; per-table reads and orchestration go through `sa.view or source`; `sa.findings` land in workbook findings; multi-handle `SheetAnalysis` orchestrates every handle with the existing `__{i}_{j}` id convention.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_split_neutrality.py`:

```python
def test_run_swarm_emits_pipeline_findings():
    """Lens-failure findings surface on the WorkbookExtraction."""
    from mcg_swarm.analyzers.registry import register
    from mcg_swarm.runner import run_swarm

    class _Boom:
        name = "boom2"
        def analyze(self, grid, sheet):
            raise RuntimeError("lens exploded")
    register("boom2", _Boom)

    ex = run_swarm(_FakeSource(_SHEETS),
                   config=SwarmConfig(analyzers=("vertical", "boom2")))
    cats = [f.category for f in ex.findings]
    assert cats.count("analyzer-error") == len(_SHEETS)   # one per sheet
    # extraction itself is unharmed — vertical still wins every sheet
    assert len(ex.tables) == len(_SHEETS)
    assert not ex.errors


def test_run_swarm_multi_handle_sheet_orchestrates_all():
    """A SheetAnalysis with N handles yields N tables with __i_j ids."""
    from mcg_swarm.analyzers.registry import register
    from mcg_swarm.splitter import handle_from_region
    from mcg_swarm.analyzers.base import LayoutCandidate
    from mcg_swarm.runner import run_swarm

    two = {"S": [("Region", "Sales"), ("North", 10), ("South", 20),
                 (None, None),
                 ("Dept", "Cost"), ("Eng", 100), ("Ops", 50)]}

    class _Pair:
        name = "pair"
        def analyze(self, grid, sheet):
            top = handle_from_region(grid, sheet, "A1:B3", 1)
            bottom = handle_from_region(grid, sheet, "A5:B7", 5)
            return [LayoutCandidate(method="pair", handles=(top, bottom),
                                    coverage=1.0)]
    register("pair", _Pair)

    ex = run_swarm(_FakeSource(two), config=SwarmConfig(analyzers=("pair",)))
    ids = sorted(t.table_id for t in ex.tables)
    assert ids == ["S__0_0", "S__0_1"]
    assert all(not t.errors for t in ex.tables)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_split_neutrality.py -v`
Expected: the two new tests FAIL (findings not surfaced / only the assessed single flow exists); the three older tests PASS.

- [ ] **Step 3: Rewire `run_swarm`**

In `mcg_swarm/runner.py`:

(a) Replace the splitter import line:

```python
from mcg_swarm.splitter import TableHandle
from mcg_swarm.analyzers.pipeline import analyze_workbook
```

(b) Keep the pre-`try` fail-fast validation exactly as-is (commit 7afe196). Replace `handles = split_workbook(source, config=config)` inside the `try` with:

```python
        sheet_analyses = analyze_workbook(source, config=config)
```

(c) Replace the loop header and the per-sheet read so every read and orchestration goes through the analysis' view (`view=None` ⇒ identical to today):

```python
    for i, sa in enumerate(sheet_analyses):
        sheets.append(sa.sheet)
        wb_findings.extend(sa.findings)
        sheet_src = sa.view or source

        if len(sa.handles) > 1:
            # Multi-table interpretation from a lens: orchestrate each handle.
            # Layer-2 review presumes a single baseline handle, so it is skipped
            # here — multi-handle winners were already assessed at analyze time.
            for j, sh in enumerate(sa.handles):
                tables.append(orchestrate_table(
                    sheet_src, sh, table_id=f"{sa.sheet}__{i}_{j}", llm=llm,
                    subagent=subagent, table_validator=table_validator,
                    detect_findings=[]))
            continue

        h = sa.handles[0]
        try:
            grid = sheet_src.read_region(sa.sheet)
            scan = scan_handle(grid, h, sa.sheet)
        except Exception:
            grid, scan = None, []  # never let detection break extraction
```

(d) In the remainder of the existing single-handle flow (reviewer branch, re-cut live re-validation, and the final `orchestrate_table` calls), replace every `source` argument with `sheet_src` and every `h.sheet` with `sa.sheet`. Do not otherwise touch the reviewer/re-cut logic — it is subsumed in Plan B2, not here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_split_neutrality.py -v`
Expected: PASS (7 tests: 3 prior + your 2 new + Task 3's shim coverage via the older tests).

- [ ] **Step 5: Run the full suite (the local neutrality gate)**

Run: `.venv/bin/python -m pytest -q`
Expected: **336 passed, 1 skipped** (329 + 5 Task-4 + 2 Task-5), zero failures. Every structural/boundary/orchestrator/adapter test must stay green — the default path is `view=None`, single handle, identical control flow.

- [ ] **Step 6: Commit**

```bash
git add mcg_swarm/runner.py tests/test_split_neutrality.py
git commit -m "feat(runner): consume analyze_workbook — view threading, pipeline findings, multi-handle sheets"
```

---

## Controller exit gate (after Task 5, before merge)

Corpus byte-identity: run the Phase-A procedure (`neutrality_corpus.py` on the branch HEAD vs `main` via worktree) — extraction over `eval/data/workbooks/*.xlsx` with default config must produce **no diff**.

## Plan B2 preview (next plan, written after B1 merges)

Agentic arbiter over top-K on genuine disagreement (`not _dominates(top, runner_up)`), choose-among-K only, never-raise; verify-before-accept floor vs the `"vertical"` baseline using the same three-way score; generalization of run_swarm's live re-validation to any non-vertical winner; StructuralReviewer subsumed (its agent proposal becomes a runner-gated "recut" lens; its accept gate becomes assessor Stages 3-4); runner-injected eval gate ("no regression with a runner; arbiter picks baseline when lenses agree").

## Self-Review

**1. Spec coverage (B1 slice):** SourceView/TransposedView + downstream wiring → Tasks 1, 5. Contract change surfacing findings/method → Task 3 (via `SheetAnalysis`; `split_workbook` kept as documented shim — deviation from spec §4.6's literal "pairs return" to preserve 15 call sites; intent honored, noted for B2). Deferred items: never-raise/stub → Task 3; dedup widening → Task 2; coverage float → Task 2. Assessor Stages 2-4, subsumption, runner gates → explicitly Plan B2. ✓
**2. Placeholder scan:** none — every code step is complete; the only prose-directed edit (Task 5 Step 3d) names the exact substitutions and bounds them. ✓
**3. Type consistency:** `SheetAnalysis(sheet, handles, view, method, findings)` identical in Tasks 3/5; `assess_sheet(candidates, *, source, grid, sheet)` and `rank_candidates(...)-> list[(candidate, (cov, err, gaps))]` consistent between Task 4 code and tests; `score_handles(source, grid, handles, sheet)` matches structural.py:67; `Finding` always constructed with `message`. Suite-count arithmetic: 322 → +3 (T2) → +4 (T3) → +5 (T4) → +2 (T5) = 336. ✓
