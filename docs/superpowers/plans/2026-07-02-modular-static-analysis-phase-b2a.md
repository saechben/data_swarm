# Modular Static Analysis — Phase B2a Implementation Plan (View Integrity + Lens-Source Protocol)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the four deterministic gaps named by the B1 final review so viewed (e.g. transposed) tables survive end-to-end — lenses can see the source (#4), `CanonicalTable` persists orientation and `build_indices`/the eval adapter rebuild through the view (#3), and the pipeline assesses with the rich view-aware ranking (#5) — all corpus-neutral with the default config.

**Architecture:** `SheetAnalyzer.analyze` gains an optional `source` parameter so a lens can construct a `TransposedView` over the real workbook. `orchestrate_table` gains an `orientation` kwarg threaded from `run_swarm` (which knows the winning candidate's view), and `build_indices` wraps the source in a `TransposedView` for transposed tables — fixing the eval adapter transitively. `analyze_sheet` swaps `assess()` for `assess_sheet()` (inside the existing never-raise guard), and `rank_candidates` scores each candidate through its own view. A test-only transpose lens proves the whole seam through `run_swarm` + `build_indices` + `query`.

**Tech Stack:** Python 3, pytest. No new dependencies. Deterministic only — the agentic arbiter/StructuralReviewer subsumption is **Plan B2b**.

## Global Constraints

- **Corpus neutrality with default config is the exit criterion.** With `SwarmConfig()` (analyzers=`("vertical",)`, no runner), `run_swarm` output must be byte-identical to `main`. Neutral by construction: vertical ignores `source`, single-candidate `assess_sheet` short-circuits before any scoring, `orientation` defaults `"vertical"` everywhere, `build_indices` wraps only transposed tables. Controller runs the corpus diff after the last task.
- Test command: `.venv/bin/python -m pytest -q` (NOT bare `pytest`). Baseline before Task 1: **344 passed, 1 skipped**. Zero pre-existing failures tolerated at any commit.
- The `StructuralReviewer` and `run_swarm`'s re-cut branch stay functionally intact (subsumption is B2b). `detect_table` and splitter helpers stay unchanged. Plain `assess()` stays exported and byte-unchanged (cheap Stage-0/1; still used by older tests).
- Never-raise contract holds: the pipeline's existing try/except around winner selection (B1 fix commit 53007e5) must end up wrapping `assess_sheet` — a scoring failure on a hostile candidate degrades to the fallback stub + warning finding, never a crash.
- `CanonicalTable.orientation` already exists (`schemas.py`, `Literal["vertical", "transposed"]`, default `"vertical"`). Do NOT touch the schema.
- Ledger caution (from Phase 2): `model_copy(update=...)` is NOT safe on `CanonicalTable` (validators re-derive `errors` from `findings`). This plan avoids it entirely by threading `orientation` at construction time.
- **Honest scope limit (encode, don't fight):** a deterministic score often cannot distinguish a *vertical reading* of a transposed table from the correct transposed reading — both can be structurally valid. Do NOT write tests promising the transpose lens beats vertical in an ensemble on ambiguous sheets; that disambiguation is B2b's arbiter (spec §4.5 orientation-consistency + Stage 2). B2a's e2e tests run the transpose lens alone to prove the seam.
- Spec: `docs/superpowers/specs/2026-07-01-modular-static-analysis-design.md`. B1 final-review item numbering (#3/#4/#5/#8) refers to `.superpowers/sdd/progress.md`. Item #8 (multi-handle scan asymmetry) is deliberately deferred to B2b (tangled with subsumption).

---

## File Structure

**Modify:**
- `mcg_swarm/analyzers/base.py` — Protocol: `analyze(self, grid, sheet, source=None)`.
- `mcg_swarm/analyzers/vertical.py` — accept (and ignore) `source`.
- `mcg_swarm/analyzers/pipeline.py` — pass `source` to lenses (Task 1); call `assess_sheet` (Task 3).
- `mcg_swarm/analyzers/assess.py` — `rank_candidates` scores through each candidate's view (Task 3).
- `mcg_swarm/orchestrator.py` — `orientation` kwarg through `_stub`/`_orchestrate_core`/`orchestrate_table` into the three `CanonicalTable(...)` constructions (lines 25, 129, 151).
- `mcg_swarm/runner.py` — compute per-sheet orientation from `sa.view`, pass at every `orchestrate_table` call in the loop; `build_indices` rebuilds transposed tables through a `TransposedView`.
- Existing test lens signatures (Task 1, enumerated below).

**Create:**
- `tests/test_view_e2e.py` — test-only transpose lens + end-to-end seam tests.

---

### Task 1: Lens-source protocol (#4) — lenses can see the WorkbookSource

**Files:**
- Modify: `mcg_swarm/analyzers/base.py` (Protocol signature)
- Modify: `mcg_swarm/analyzers/vertical.py` (signature)
- Modify: `mcg_swarm/analyzers/pipeline.py` (`analyze_sheet` signature + lens call; `analyze_workbook` passes source)
- Modify: in-tree test lenses (signatures only): `tests/test_analyzers.py` (`_Dummy`, `_Fake`), `tests/test_pipeline.py` (`_RaisingLens`, `_EmptyLens`, `_NoHandles`, `_Malformed`), `tests/test_split_neutrality.py` (`_Boom`, `_Pair`)
- Test: `tests/test_pipeline.py` (append 2)

**Interfaces:**
- Consumes: `SheetAnalyzer` Protocol (base.py), `analyze_sheet(analyzers, grid, sheet)` / `analyze_workbook` (pipeline.py), `TransposedView` (`mcg_swarm/views.py`).
- Produces: `SheetAnalyzer.analyze(self, grid: list[tuple], sheet: str, source=None) -> list[LayoutCandidate]`; `analyze_sheet(analyzers, grid, sheet, source=None)`; the pipeline calls `a.analyze(grid, sheet, source=source)`. Tasks 3-4 and Phase C lenses rely on the `source` kwarg.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py`:

```python
def test_lens_receives_source():
    """#4: the pipeline hands each lens the WorkbookSource so it can build views."""
    seen = {}

    class _SourceSpy:
        name = "sourcespy"
        def analyze(self, grid, sheet, source=None):
            seen["source"] = source
            return []
    register("sourcespy", _SourceSpy)

    src = _GridSource(_SHEETS)
    analyze_workbook(src, config=SwarmConfig(analyzers=("sourcespy",)))
    assert seen["source"] is src


def test_lens_can_construct_view_over_source():
    """A lens can wrap the source in a TransposedView and attach it to a candidate."""
    from mcg_swarm.analyzers.base import LayoutCandidate
    from mcg_swarm.splitter import detect_table
    from mcg_swarm.views import TransposedView

    class _ViewLens:
        name = "viewlens"
        def analyze(self, grid, sheet, source=None):
            view = TransposedView(source)
            vgrid = view.read_region(sheet)
            handle = detect_table(vgrid, sheet)
            return [LayoutCandidate(method="viewlens", handles=(handle,),
                                    coverage=1.0, view=view)]
    register("viewlens", _ViewLens)

    horizontal = {"S": [("Region", "North", "South"), ("Sales", 10, 20)]}
    out = analyze_workbook(_GridSource(horizontal),
                           config=SwarmConfig(analyzers=("viewlens",)))
    sa = out[0]
    assert type(sa.view).__name__ == "TransposedView"
    assert sa.handles[0].region == "A1:B3"      # view coordinates (3 rows after transpose)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -k "source or view_over" -v`
Expected: FAIL — `TypeError: analyze() got an unexpected keyword argument 'source'` (pipeline does not pass source yet; once it does, 2-arg lenses fail instead — both states are the RED you fix).

- [ ] **Step 3: Widen the Protocol and the pipeline**

In `mcg_swarm/analyzers/base.py`, change the Protocol method:

```python
    def analyze(self, grid: list[tuple], sheet: str, source=None) -> list[LayoutCandidate]:
        ...
```

In `mcg_swarm/analyzers/pipeline.py`:

```python
def analyze_sheet(analyzers, grid: list[tuple], sheet: str, source=None) -> SheetAnalysis:
```

and inside the lens loop change the call to:

```python
            candidates.extend(a.analyze(grid, sheet, source=source))
```

and in `analyze_workbook` change the per-sheet call to:

```python
    return [analyze_sheet(analyzers, src.read_region(name), name, source=src)
            for name in src.sheet_names()]
```

- [ ] **Step 4: Update every in-tree lens signature**

Mechanical: add `, source=None` to the `analyze` signature of each (bodies unchanged):
- `mcg_swarm/analyzers/vertical.py` → `def analyze(self, grid: list[tuple], sheet: str, source=None) -> list[LayoutCandidate]:` (vertical ignores it — that IS the neutrality property).
- `tests/test_analyzers.py`: `_Dummy.analyze`, `_Fake.analyze` (inside `test_register_and_build_custom`).
- `tests/test_pipeline.py`: `_RaisingLens.analyze`, `_EmptyLens.analyze`, `_Malformed.analyze`, and `_NoHandles.analyze` (inside `test_run_swarm_zero_handle_winner_skips_sheet`).
- `tests/test_split_neutrality.py`: `_Boom.analyze` (in `test_run_swarm_emits_pipeline_findings`), `_Pair.analyze` (in `test_run_swarm_multi_handle_sheet_orchestrates_all`).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: **346 passed, 1 skipped** (344 + 2 new), zero failures.

- [ ] **Step 6: Commit**

```bash
git add mcg_swarm/analyzers/base.py mcg_swarm/analyzers/vertical.py mcg_swarm/analyzers/pipeline.py tests/test_analyzers.py tests/test_pipeline.py tests/test_split_neutrality.py
git commit -m "feat(analyzers): lenses receive the WorkbookSource (view-construction seam)"
```

---

### Task 2: Orientation persistence (#3) — CanonicalTable + build_indices through the view

**Files:**
- Modify: `mcg_swarm/orchestrator.py` (`_stub`, `_orchestrate_core`, `orchestrate_table`, three `CanonicalTable(...)` sites at lines ~25/129/151)
- Modify: `mcg_swarm/runner.py` (orientation computed per sheet + passed at every loop `orchestrate_table` call; `build_indices` view wrap)
- Test: `tests/test_view_e2e.py` (new — first half)

**Interfaces:**
- Consumes: `CanonicalTable.orientation` (schemas.py, exists), `TransposedView` (views.py), `SheetAnalysis.view`, `build_index(source, handle, row_key)` (`extraction.py:192` — accepts any `as_source`-able, including a `WorkbookSource`).
- Produces: `orchestrate_table(..., orientation: str = "vertical")` and `_orchestrate_core(..., orientation: str = "vertical")`; every table (including error stubs) carries the orientation of the view it was extracted through; `build_indices(path, extraction)` rebuilds transposed tables through `TransposedView(as_source(path))`. Task 4 and the eval adapter rely on this.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_view_e2e.py`:

```python
"""#3/#4 end-to-end: viewed tables survive orchestration, persistence, and rebuild."""
from mcg_swarm.orchestrator import orchestrate_table
from mcg_swarm.runner import build_indices
from mcg_swarm.schemas import WorkbookExtraction
from mcg_swarm.splitter import detect_table
from mcg_swarm.views import TransposedView
from tests.test_views import _GridSource

# Raw layout is horizontal (fields as rows); the view presents it vertical.
_HORIZONTAL = {"S": [("Region", "North", "South"), ("Sales", 10, 20)]}


def _viewed_table(table_id="S__0"):
    src = _GridSource(_HORIZONTAL)
    view = TransposedView(src)
    handle = detect_table(view.read_region("S"), "S")
    table = orchestrate_table(view, handle, table_id=table_id,
                              orientation="transposed")
    return src, table


def test_orchestrate_table_persists_orientation():
    _, table = _viewed_table()
    assert table.orientation == "transposed"
    assert not table.errors                       # extraction through the view is clean


def test_orientation_defaults_vertical():
    src = _GridSource({"S": [("Region", "Sales"), ("North", 10)]})
    handle = detect_table(src.read_region("S"), "S")
    table = orchestrate_table(src, handle, table_id="S__0")
    assert table.orientation == "vertical"


def test_build_indices_rebuilds_through_view():
    """#3: the adapter-path rebuild must wrap transposed tables in a TransposedView."""
    src, table = _viewed_table()
    ex = WorkbookExtraction(workbook="wb", sheets=["S"], tables=[table],
                            generator_version="test")
    idx = build_indices(src, ex)[table.table_id]  # build_indices as_sources its arg
    assert idx.query("North", "Sales").value == 10
    assert idx.query("South", "Sales").value == 20   # non-diagonal: axis genuinely correct
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_view_e2e.py -v`
Expected: FAIL — `TypeError: orchestrate_table() got an unexpected keyword argument 'orientation'`.

- [ ] **Step 3: Thread `orientation` through the orchestrator**

In `mcg_swarm/orchestrator.py`:

(a) `_stub` gains the kwarg and forwards it:

```python
def _stub(handle, table_id: str, findings: list, orientation: str = "vertical") -> CanonicalTable:
```

and add `orientation=orientation,` to its `CanonicalTable(...)` construction. Then grep `_stub(` within `_orchestrate_core` and add `orientation=orientation` to each call.

(b) `_orchestrate_core` and `orchestrate_table` each gain `orientation: str = "vertical"` as a keyword parameter (after `detect_findings`); `orchestrate_table` forwards it to `_orchestrate_core`.

(c) Add `orientation=orientation,` to the two remaining `CanonicalTable(...)` constructions inside `_orchestrate_core` (the §5 intermediate at ~line 129 and the §7 final at ~line 151).

- [ ] **Step 4: Thread it through `run_swarm` and fix `build_indices`**

In `mcg_swarm/runner.py`:

(a) Top-level import (no cycle — views imports only source):

```python
from mcg_swarm.views import TransposedView
```

(b) In the per-sheet loop, immediately after `sheet_src = sa.view or source`, add:

```python
        orient = "transposed" if isinstance(sa.view, TransposedView) else "vertical"
```

(c) Add `orientation=orient` to EVERY `orchestrate_table(...)` call inside the loop — the multi-handle branch, the re-cut `cand_tables` list-comp, the `base_table` call, the reject-fallback call, and the final single-handle loop call. (Grep `orchestrate_table(` in runner.py; every call site inside `run_swarm` gets it; `build_indices` below has none.)

(d) Rewrite `build_indices` to rebuild through the view:

```python
def build_indices(path, extraction: WorkbookExtraction) -> dict:
    """Rebuild ExtractionIndex objects deterministically for the adapter.

    Skips tables that have errors (failed tables have no valid index).
    Transposed tables (extracted through a TransposedView) are rebuilt through
    the same view kind so their view-coordinate regions resolve correctly.
    """
    from mcg_swarm.source import as_source

    out = {}
    for t in extraction.tables:
        if t.errors:  # don't build an index for a failed table
            continue
        handle = TableHandle(
            sheet=t.sheet,
            region=t.region,
            header_row=t.header_row,
            columns=t.columns,
            header_span=getattr(t, "header_span", 1),
        )
        src = as_source(path)
        if t.orientation == "transposed":
            src = TransposedView(src)
        out[t.table_id] = build_index(src, handle, row_key=t.extraction.row_key)
    return out
```

- [ ] **Step 5: Run the new tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_view_e2e.py -v`
Expected: PASS (3 tests).
Run: `.venv/bin/python -m pytest -q`
Expected: **349 passed, 1 skipped** (346 + 3), zero failures — orientation defaults keep every existing path identical.

- [ ] **Step 6: Commit**

```bash
git add mcg_swarm/orchestrator.py mcg_swarm/runner.py tests/test_view_e2e.py
git commit -m "feat(runner,orchestrator): persist orientation; build_indices rebuilds through the view"
```

---

### Task 3: `assess_sheet` wiring (#5) — rich, view-aware assessment in the pipeline

**Files:**
- Modify: `mcg_swarm/analyzers/assess.py` (`rank_candidates` view-aware scoring)
- Modify: `mcg_swarm/analyzers/pipeline.py` (call `assess_sheet` instead of `assess`)
- Test: `tests/test_assess_sheet.py` (append 1), `tests/test_pipeline.py` (append 1)

**Interfaces:**
- Consumes: `assess_sheet(candidates, *, source, grid, sheet)` and `rank_candidates` (assess.py), `score_handles(source, grid, handles, sheet)` (structural.py), the pipeline's existing never-raise winner-selection guard (commit 53007e5).
- Produces: `rank_candidates` scores each candidate through `c.view or source` with `c.view.read_region(sheet)` when viewed; `analyze_sheet` winner selection = `assess_sheet(candidates, source=source, grid=grid, sheet=sheet)` inside the existing try/except. Plain `assess()` stays byte-unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_assess_sheet.py`:

```python
def test_rank_scores_viewed_candidate_through_its_view():
    """#5: a viewed candidate's handles are in view coordinates — score them there."""
    from mcg_swarm.views import TransposedView

    horizontal = {"S": [("Region", "North", "South"), ("Sales", 10, 20)]}
    src = _GridSource(horizontal)
    raw_grid = src.read_region("S")

    view = TransposedView(src)
    vhandle = detect_table(view.read_region("S"), "S")
    viewed = LayoutCandidate(method="transposed", handles=(vhandle,), view=view)

    ranked = rank_candidates([viewed], source=src, grid=raw_grid, sheet="S")
    cov, errors, _gaps = ranked[0][1]
    assert cov == 6            # all 6 non-empty cells, counted in VIEW coordinates
    assert errors == 0         # scored through the view, the table is clean
```

Append to `tests/test_pipeline.py`:

```python
def test_pipeline_uses_rich_ranking_for_multi_candidate():
    """#5: with competing lenses, the pipeline picks by score_handles, not raw coverage."""
    from mcg_swarm.splitter import handle_from_region
    from mcg_swarm.analyzers.base import LayoutCandidate

    two = [("Region", "Sales"), ("North", 10), ("South", 20),
           (None, None),
           ("Dept", "Cost"), ("Eng", 100), ("Ops", 50)]

    class _PairLens:
        name = "pairlens"
        def analyze(self, grid, sheet, source=None):
            top = handle_from_region(grid, sheet, "A1:B3", 1)
            bottom = handle_from_region(grid, sheet, "A5:B7", 5)
            return [LayoutCandidate(method="pairlens", handles=(top, bottom),
                                    coverage=1.0)]
    register("pairlens", _PairLens)

    sa = analyze_sheet(build_analyzers(("vertical", "pairlens")), two, "S",
                       source=_GridSource({"S": two}))
    assert sa.method == "pairlens"        # 12-cell coverage beats vertical's 6
    assert len(sa.handles) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_assess_sheet.py::test_rank_scores_viewed_candidate_through_its_view tests/test_pipeline.py::test_pipeline_uses_rich_ranking_for_multi_candidate -v`
Expected: the rank test FAILS (viewed handles scored against the RAW grid → wrong coverage/errors); the pipeline test FAILS (`sa.method == "vertical"` — plain `assess` ranks by raw `coverage` float where vertical's 1.0-confidence wins or ordering decides; either way not "pairlens" by rich score).

- [ ] **Step 3: Make `rank_candidates` view-aware**

In `mcg_swarm/analyzers/assess.py`, replace the scoring loop inside `rank_candidates`:

```python
    deduped = _dedup(candidates)
    scored = []
    for c in deduped:
        # A viewed candidate's handles live in view coordinates: score them
        # against the view's source and grid, not the raw sheet (spec §4.3).
        c_src = c.view if c.view is not None else source
        c_grid = c.view.read_region(sheet) if c.view is not None else grid
        scored.append((c, score_handles(c_src, c_grid, list(c.handles), sheet)))
    scored.sort(key=lambda cs: (-cs[1][0], cs[1][1], cs[1][2], -cs[0].confidence))
    return scored
```

- [ ] **Step 4: Wire `assess_sheet` into the pipeline**

In `mcg_swarm/analyzers/pipeline.py`, change the import and the guarded winner selection:

```python
from mcg_swarm.analyzers.assess import assess_sheet
```

and inside `analyze_sheet`'s existing `if candidates:` try/except, replace `winner = assess(candidates)` with:

```python
            winner = assess_sheet(candidates, source=source, grid=grid, sheet=sheet)
```

(The surrounding try/except and fallback stay exactly as they are — a scoring exception on a hostile candidate already degrades to the fallback stub + `analyzer-error` finding.) Remove the now-unused `assess` import from pipeline.py if nothing else references it.

- [ ] **Step 5: Run the new tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_assess_sheet.py tests/test_pipeline.py -v`
Expected: PASS, including `test_malformed_candidate_degrades_to_fallback` (the guard now catches `assess_sheet` failures identically).
Run: `.venv/bin/python -m pytest -q`
Expected: **351 passed, 1 skipped** (349 + 2), zero failures. Default path: one lens → one candidate → `assess_sheet` dedup → single → identity return BEFORE any `score_handles` call — no behavior or performance change.

- [ ] **Step 6: Commit**

```bash
git add mcg_swarm/analyzers/assess.py mcg_swarm/analyzers/pipeline.py tests/test_assess_sheet.py tests/test_pipeline.py
git commit -m "feat(analyzers): pipeline assesses with view-aware rich ranking (assess_sheet)"
```

---

### Task 4: End-to-end seam proof — transpose lens through run_swarm + adapter path

**Files:**
- Test: `tests/test_view_e2e.py` (append — second half)

**Interfaces:**
- Consumes: everything Tasks 1-3 produced; `run_swarm`, `build_indices` (runner.py), `SwarmConfig(analyzers=...)`, `register` (registry.py).
- Produces: the closing e2e evidence for B1-final-review #4 ("view threading untestable end-to-end") — a real lens constructs a view over the real source, `run_swarm` extracts through it, orientation persists, and the adapter-path rebuild queries correctly.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_view_e2e.py`:

```python
from mcg_swarm.analyzers.base import LayoutCandidate
from mcg_swarm.analyzers.registry import register
from mcg_swarm.config import SwarmConfig
from mcg_swarm.coverage import coverage_score, nonempty_cells
from mcg_swarm.runner import run_swarm


class _TransposeLens:
    """Test-only skeleton of Phase C's transpose lens: unconditionally presents
    the sheet through a TransposedView. Registered under a test-unique name."""

    name = "transpose_e2e"

    def analyze(self, grid, sheet, source=None):
        if source is None:
            return []
        view = TransposedView(source)
        vgrid = view.read_region(sheet)
        handle = detect_table(vgrid, sheet)
        total = len(nonempty_cells(vgrid))
        cov = coverage_score(vgrid, [handle.region]) / total if total else 0.0
        return [LayoutCandidate(method="transpose_e2e", handles=(handle,),
                                coverage=cov, view=view)]


register("transpose_e2e", _TransposeLens)


def test_run_swarm_extracts_transposed_sheet_through_view():
    """The full seam: lens builds view -> run_swarm orchestrates through it ->
    orientation persists -> adapter-path rebuild queries the right axis."""
    src = _GridSource(_HORIZONTAL)
    ex = run_swarm(src, config=SwarmConfig(analyzers=("transpose_e2e",)))

    assert len(ex.tables) == 1
    t = ex.tables[0]
    assert t.orientation == "transposed"
    assert not t.errors
    assert [c.name for c in t.columns] == ["Region", "Sales"]

    idx = build_indices(src, ex)[t.table_id]
    assert idx.query("North", "Sales").value == 10
    assert idx.query("South", "Sales").value == 20


def test_vertical_workbook_unaffected_by_transpose_lens_availability():
    """Default config never touches the registered e2e lens: byte-parity guard."""
    vertical = {"S": [("Region", "Sales"), ("North", 10), ("South", 20)]}
    ex = run_swarm(_GridSource(vertical))          # default SwarmConfig()
    t = ex.tables[0]
    assert t.orientation == "vertical"
    assert not t.errors
    idx = build_indices(_GridSource(vertical), ex)[t.table_id]
    assert idx.query("North", "Sales").value == 10
```

- [ ] **Step 2: Run tests to verify state**

Run: `.venv/bin/python -m pytest tests/test_view_e2e.py -v`
Expected: with Tasks 1-3 landed these should PASS on first run — that is the point (the seam is complete). If either fails, the seam has a real integration bug: STOP and report the failure verbatim; do not weaken assertions.

- [ ] **Step 3: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: **353 passed, 1 skipped** (351 + 2), zero failures.

- [ ] **Step 4: Commit**

```bash
git add tests/test_view_e2e.py
git commit -m "test(views): e2e transpose seam — lens view through run_swarm + adapter rebuild"
```

---

## Controller exit gate (after Task 4, before merge)

Corpus byte-identity: Phase-A procedure (`neutrality_corpus.py`, branch HEAD vs `main` via worktree, default config) — **no diff** required.

## Plan B2b preview (written after B2a merges)

Agentic arbiter (Stage 2: top-K on `not _dominates(top, runner_up)`, choose-among-K via read-only sheet toolset, never-raise); verify-before-accept floor vs the vertical baseline (Stage 3); `run_swarm` live re-validation generalized to any non-vertical winner (Stage 4); StructuralReviewer subsumed (agent re-cut proposal → runner-gated "recut" lens; accept gate → Stages 3-4; retire the runner.py re-cut branch); #8 union-aware multi-handle `scan_handle`; runner-injected eval gates ("no regression with runner; arbiter picks baseline when lenses agree"). B2b also inherits the design-round-2 hooks: the arbiter slot doubles as the human-verification insertion point, and the lens-source protocol (#4, landed here) is what the pure-agentic lens will consume.

## Self-Review

**1. Coverage of the four named items:** #4 → Task 1; #3 → Task 2 (persistence + build_indices + adapter transitively); #5 → Task 3 (inside the existing never-raise guard, view-aware scoring); e2e evidence gap → Task 4. #8 explicitly deferred to B2b with rationale. ✓
**2. Placeholder scan:** none; the two prose-directed steps (Task 1 Step 4 signature list, Task 2 Step 4c call-site sweep) enumerate exact targets and bound the edit. ✓
**3. Type consistency:** `analyze(self, grid, sheet, source=None)` uniform across Protocol/vertical/pipeline/test lenses; `orientation: str = "vertical"` kwarg name identical across `_stub`/`_orchestrate_core`/`orchestrate_table`/call sites; `build_indices(path, extraction)` keeps its public signature (arg now `as_source`-able — used by tests); suite arithmetic 344 → +2 → +3 → +2 → +2 = **353**. ✓
