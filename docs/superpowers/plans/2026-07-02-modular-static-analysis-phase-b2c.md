# Modular Static Analysis — Phase B2c Implementation Plan (Gate Hardening + Pure-Agentic Layout Lens)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the quality gate actually prove what the pure-agentic approach needs it to prove (kill the five verified blind spots: tautological Phase 1, duplicate-key shadowing, blank-key collapse, dropped columns, vacuous empty-table passes), fix the B2b-review baseline-label hole, then add the `AgenticLayoutLens` — an agent with no structural assumptions that proposes STRUCTURE (never values), iterates against a deterministic scorer tool until clean, and emits ordinary `LayoutCandidate`s that flow through the B2b arbiter/floor/live-re-validation machinery.

**Architecture:** `ExtractionIndex` records build-time anomalies (`duplicate_row_keys`, `blank_key_rows`) without changing resolution semantics; the gate turns them into failures and gains real coverage checks in both directions. `assess_sheet_full` finds the baseline by signature (dedup-label-proof). New module `mcg_swarm/analyzers/agentic.py`: `SheetLayoutPatch` schema, `AgenticLensPolicy` caps, a pure `_materialize` (proposal → handles + view), a `try_layout` sandbox tool wrapping `score_handles` so the agent iterates to green BEFORE finalizing, and the lens class (`needs_runner = True`). `build_analyzers` grows a `runner=` parameter that feeds runner-marked factories. Agent harness extras (bash sandbox, excel skill, subagents) are application-side `ClaudeSDKAgentRunner` configuration (`host_tools`/`permission_mode` already exist) — documented, not core code.

**Tech Stack:** Python 3, pytest, pydantic, `FakeAgentRunner` for all agent tests (no live SDK calls in the suite).

## Global Constraints

- Test command: `.venv/bin/python -m pytest -q` (NOT bare `pytest`). Baseline before Task 1: **377 passed, 1 skipped**. Zero failures at any commit — EXCEPT: if a new gate check (Task 2) fails a pre-existing test, that is a genuine latent defect surfaced; STOP and report it verbatim (controller adjudicates), do not weaken the check.
- **Corpus exit gate is ADJUDICATED, not blind byte-identity.** Tasks 1/3–6 are corpus-neutral by construction (anomaly records don't change resolution; the agentic lens is opt-in; baseline fix only fires on a today-impossible label steal). Task 2 intentionally strengthens the gate: corpus output may legitimately gain new failure findings on workbooks that really have duplicate/blank keys or dropped columns. The controller runs the corpus diff after Task 6; any diff must consist ONLY of new gate-failure findings/errors — anything else is a regression.
- `ExtractionIndex` resolution semantics (`_key_to_phys`/`_col_to_phys` contents, `query()` behavior) are byte-unchanged — Task 1 only ADDS records. Gate phases 2a/2b/2c/3/4/5 unchanged — Task 2 only replaces the tautological Phase 1 and adds checks.
- The agent proposes STRUCTURE, never values: nothing an agent returns is ever written into a table; handles are re-materialized deterministically (`handle_from_region`) and all downstream extraction/gating is the existing deterministic pipeline. `LayoutCandidate.confidence` for the agentic lens is **0.7** (below vertical's 1.0) so on an identical interpretation `_dedup` keeps the vertical label — the "agreed by both approaches" signal AND the baseline-label protection.
- Import direction: `analyzers → splitter/coverage/schemas/views` top-level OK; `analyzers → subagent` LAZY only (`score_handles`, `structural_tools`, `tools.Tool` imported inside functions).
- Policy caps are mandatory even with cost-no-object: `AgenticLensPolicy(max_tables=12, max_probe_iterations=20)`. The probe counter lives in the lens call, not the agent's goodwill.
- The lens MAY raise out of `analyze` (e.g. runner transport failure) — the pipeline's per-lens never-raise guard already converts that to an `analyzer-error` finding. `runner is None or source is None` → return `[]` (graceful degradation; run_swarm's pre-try `build_analyzers` validation constructs it runner-less and must not raise).
- v1 scope limits (encode, don't fight): one orientation per proposal (mixed → keep the vertical subset + warning finding); `try_layout` scores via `score_handles` (the full `run_table_tests` needs a column schema that only exists after orchestration — running the real gate in the agent loop is a B2c follow-up); `MaterializedView` (cell-surgery escape hatch) and replayable script artifacts are deferred (see Deferred section).
- New Finding categories: `agentic-lens` (warnings from proposal materialization). Gate failure strings introduced: `coverage gap:`, `row-key collision:`, `blank row key:`, `empty index:`, `column-coverage:` — tests assert on these prefixes.
- Spec: `docs/superpowers/specs/2026-07-01-modular-static-analysis-design.md` §4.4 (SemanticAnalyzer slot), §5 (failure behavior). Settled design decisions from the 2026-07-02 discussion are binding: propose-structure-not-values, gate-as-sandbox-tool, policy caps, candidates compete like any lens.

---

## File Structure

**Modify:**
- `mcg_swarm/extraction.py` — anomaly records in `ExtractionIndex.__init__` (Task 1).
- `mcg_swarm/quality_gate.py` — Phase 1 rewrite + reverse column coverage (Task 2).
- `mcg_swarm/analyzers/assess.py` — signature-based baseline lookup (Task 3).
- `mcg_swarm/analyzers/registry.py` — `build_analyzers(names, runner=None)` + register "agentic" (Task 5).
- `mcg_swarm/analyzers/pipeline.py` — pass runner to `build_analyzers` (Task 5).
- `docs/how_to_use.md`, `DATA-REQUIREMENTS.md` (Task 6).

**Create:**
- `mcg_swarm/analyzers/agentic.py` (Tasks 4–5), `tests/test_index_anomalies.py` (Task 1), `tests/test_agentic_lens.py` (Tasks 4–5), `tests/test_agentic_e2e.py` (Task 6).

---

### Task 1: `ExtractionIndex` build-time anomaly records

**Files:**
- Modify: `mcg_swarm/extraction.py` (the `__init__` key loop, extraction.py:80-88)
- Test: `tests/test_index_anomalies.py` (new, 3 tests)

**Interfaces:**
- Consumes: the existing key loop (`for i, row in enumerate(grid[data_start_off:], start=data_start_row)`).
- Produces: `index.duplicate_row_keys: list[tuple]` — `(key, shadowed_row, winning_row)` per overwrite event; `index.blank_key_rows: list[int]` — absolute rows whose key value is empty/None (keyed mode only). Resolution (`_key_to_phys` final contents) BYTE-UNCHANGED. Task 2 consumes both.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_index_anomalies.py`:

```python
"""Build-time anomaly records on ExtractionIndex — the raw material for the
gate's row-coverage checks (silent-shadowing hole, verified live 2026-07-02)."""
import openpyxl

from mcg_swarm.extraction import build_index
from mcg_swarm.splitter import split_workbook


def _wb(tmp_path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    for r in rows:
        ws.append(r)
    p = tmp_path / "t.xlsx"
    wb.save(p)
    return str(p)


def test_duplicate_row_keys_recorded_resolution_unchanged(tmp_path):
    p = _wb(tmp_path, [["Region", "Sales"], ["North", 10], ["South", 20],
                       ["North", 99]])
    h = split_workbook(p)[0]
    idx = build_index(p, h, row_key=["Region"])
    assert idx.duplicate_row_keys == [("North", 2, 4)]   # (key, shadowed, winner)
    assert idx.query("North", "Sales").value == 99       # last-wins UNCHANGED


def test_blank_key_rows_recorded(tmp_path):
    p = _wb(tmp_path, [["Region", "Sales"], ["North", 10], [None, 55],
                       ["South", 20]])
    h = split_workbook(p)[0]
    idx = build_index(p, h, row_key=["Region"])
    assert idx.blank_key_rows == [3]


def test_clean_table_records_empty(tmp_path):
    p = _wb(tmp_path, [["Region", "Sales"], ["North", 10], ["South", 20]])
    h = split_workbook(p)[0]
    idx = build_index(p, h, row_key=["Region"])
    assert idx.duplicate_row_keys == [] and idx.blank_key_rows == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_index_anomalies.py -v`
Expected: FAIL — `AttributeError: 'ExtractionIndex' object has no attribute 'duplicate_row_keys'`.

- [ ] **Step 3: Implement**

In `mcg_swarm/extraction.py`, inside `ExtractionIndex.__init__`, replace the key loop (currently lines 80-88) with:

```python
        self._key_to_phys: dict = {}
        self.duplicate_row_keys: list = []   # (key, shadowed_row, winning_row)
        self.blank_key_rows: list = []       # absolute rows w/ empty key cell
        key_cols = [self._col_to_phys[k] for k in row_key] if row_key else []
        for i, row in enumerate(grid[data_start_off:], start=data_start_row):
            if row_key:
                vals = tuple(row[kc - min_col] for kc in key_cols)
                key = vals[0] if len(vals) == 1 else vals
                blank = (key in (None, "") or (isinstance(key, tuple)
                         and all(v in (None, "") for v in key)))
                if blank:
                    self.blank_key_rows.append(i)
                if key in self._key_to_phys:
                    # Last-wins overwrite (existing behavior, unchanged): record it
                    # so the gate can fail the table instead of losing rows silently.
                    self.duplicate_row_keys.append((key, self._key_to_phys[key], i))
            else:
                key = i - (header_row + header_span - 1)  # positional 1-based
            self._key_to_phys[key] = i
```

(The final `_key_to_phys` contents and all resolution behavior are identical — only the two record lists are new. Positional mode cannot collide or blank.)

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_index_anomalies.py tests/test_extraction*.py tests/test_quality_gate.py -q` (glob may match nothing extra — fine)
Expected: PASS.
Run: `.venv/bin/python -m pytest -q`
Expected: **380 passed, 1 skipped** (377 + 3), zero failures.

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/extraction.py tests/test_index_anomalies.py
git commit -m "feat(extraction): record duplicate/blank row-key anomalies at index build (resolution unchanged)"
```

---

### Task 2: Gate hardening — real coverage, both directions

**Files:**
- Modify: `mcg_swarm/quality_gate.py`
- Test: `tests/test_quality_gate.py` (append 5)

**Interfaces:**
- Consumes: `index.duplicate_row_keys`/`blank_key_rows` (Task 1), the existing `live_col_map`/`live_names` built in Phase 2a.
- Produces: Phase 1 replaced with real checks (schema⊆index, row-key collision, blank keys, empty index); a reverse column-coverage check appended to Phase 2a. Failure-string prefixes as listed in Global Constraints.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_quality_gate.py` (reuse the file's `_wb` and `_canon` helpers; import `handle_from_region` and `Column` where used):

```python
def test_gate_fails_duplicate_row_keys(tmp_path):
    """THE silent-data-loss hole (verified live 2026-07-02): a shadowed key
    passed every phase because row-integrity reads the WINNING row."""
    p = _wb(tmp_path, [["Region", "Sales"], ["North", 10], ["South", 20],
                       ["North", 99]])
    h = split_workbook(p)[0]
    idx = build_index(p, h, row_key=["Region"])
    rep = run_table_tests(OpenpyxlFileSource(p), _canon(h), idx)
    assert not rep.passed
    assert any(f.startswith("row-key collision:") for f in rep.failures)


def test_gate_fails_blank_row_keys(tmp_path):
    p = _wb(tmp_path, [["Region", "Sales"], ["North", 10], [None, 55]])
    h = split_workbook(p)[0]
    idx = build_index(p, h, row_key=["Region"])
    rep = run_table_tests(OpenpyxlFileSource(p), _canon(h), idx)
    assert not rep.passed
    assert any(f.startswith("blank row key:") for f in rep.failures)


def test_gate_fails_empty_index(tmp_path):
    """A table with zero resolvable rows must not pass vacuously."""
    from mcg_swarm.splitter import handle_from_region
    p = _wb(tmp_path, [["Region", "Sales"], ["North", 10]])
    grid = [("Region", "Sales")]
    h = handle_from_region(grid, "Data", "A1:B1", 1)   # header-only region
    idx = build_index(p, h, row_key=["Region"])
    rep = run_table_tests(OpenpyxlFileSource(p), _canon(h), idx)
    assert not rep.passed
    assert any(f.startswith("empty index:") for f in rep.failures)


def test_gate_fails_dropped_live_column(tmp_path):
    """Reverse coverage: a physically-present header missing from
    table.columns is silent data loss for every consumer."""
    p = _wb(tmp_path, [["Region", "Sales", "Cost"], ["North", 10, 5]])
    h = split_workbook(p)[0]
    idx = build_index(p, h, row_key=["Region"])
    table = _canon(h)
    trimmed = table.model_validate({**table.model_dump(),
                                    "columns": [c for c in table.model_dump()["columns"]
                                                if c["name"] != "Cost"]})
    rep = run_table_tests(OpenpyxlFileSource(p), trimmed, idx)
    assert not rep.passed
    assert any(f.startswith("column-coverage:") for f in rep.failures)


def test_gate_fails_schema_column_missing_from_index(tmp_path):
    """Real Phase 1 (the old one checked the index against itself)."""
    p = _wb(tmp_path, [["Region", "Sales"], ["North", 10]])
    h = split_workbook(p)[0]
    idx = build_index(p, h, row_key=["Region"])
    del idx._col_to_phys["Sales"]
    rep = run_table_tests(OpenpyxlFileSource(p), _canon(h), idx)
    assert not rep.passed
    assert any(f.startswith("coverage gap:") for f in rep.failures)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_quality_gate.py -k "duplicate_row or blank_row or empty_index or dropped_live or missing_from_index" -v`
Expected: all 5 FAIL — each fixture currently passes the gate clean (that IS the finding).

- [ ] **Step 3: Implement in `mcg_swarm/quality_gate.py`**

(a) Replace the Phase-1 block (currently quality_gate.py:76-85, the two tautological loops) with:

```python
    # ------------------------------------------------------------------
    # Phase 1: REAL coverage — schema vs index, and row-resolution integrity.
    # (The previous version compared index.column_names()/row_keys() against
    # the very dicts they are read from — a tautology that could never fail.)
    # ------------------------------------------------------------------
    for col in table.columns:
        if col.name not in index._col_to_phys:
            failures.append(
                f"coverage gap: column {col.name!r} in table.columns but not "
                "resolvable via the index")
    for key, shadowed, winner in getattr(index, "duplicate_row_keys", []):
        failures.append(
            f"row-key collision: key {key!r} at row {shadowed} is shadowed by "
            f"row {winner} — the earlier row is unreachable via query()")
    for r in getattr(index, "blank_key_rows", []):
        failures.append(
            f"blank row key: data row {r} has an empty key cell — the row is "
            "not reachable by a meaningful key")
    if not keys:
        failures.append(
            "empty index: zero row keys resolved — the table cannot be queried")
```

(b) Immediately after the Phase-2a duplicate/column-name loops (below the `for name in col_names_list:` block ending at the `column-name: ... not found in live header rows` failure), append:

```python
    # Reverse coverage: every live header in the region must be declared —
    # a dropped column is silent data loss for every downstream consumer.
    declared = {c.name for c in table.columns}
    for name in live_names:
        if name not in declared:
            failures.append(
                f"column-coverage: live header {name!r} in region not declared "
                "in table.columns — column silently dropped")
```

- [ ] **Step 4: Run the gate tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_quality_gate.py -v`
Expected: PASS (all pre-existing + 5 new).
Run: `.venv/bin/python -m pytest -q`
Expected: **385 passed, 1 skipped** (380 + 5), zero failures. If ANY pre-existing test now fails one of the new checks, STOP and report it verbatim — that is a surfaced latent defect for the controller to adjudicate, not a test to fix quietly.

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/quality_gate.py tests/test_quality_gate.py
git commit -m "feat(gate): real coverage checks — row-key collisions, blank keys, empty index, dropped columns"
```

---

### Task 3: Baseline-by-signature (B2b final-review carry #4)

**Files:**
- Modify: `mcg_swarm/analyzers/assess.py` (`assess_sheet_full` baseline lookup only)
- Test: `tests/test_assess_sheet.py` (append 2)

**Interfaces:**
- Consumes: `_dedup`, `_signature`, the existing `baseline = next((c for c in deduped if c.method == "vertical"), None)` line.
- Produces: baseline found even when `_dedup` gave the vertical interpretation another lens's label (higher-confidence identical signature). Everything else in `assess_sheet_full` unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_assess_sheet.py`:

```python
def test_baseline_survives_dedup_label_steal(monkeypatch):
    """B2b final-review #4: an identical interpretation at HIGHER confidence
    steals the dedup label; the floor must still find the vertical baseline
    by signature, not by label."""
    _patch_scores(monkeypatch, {
        frozenset({"A1:B3"}): (6, 0, 0),
        frozenset({"A1:B7"}): (12, 1, 1),          # top, but errors > baseline
    })
    v = _cand("vertical", [("A1:B3", 1)])                    # confidence 1.0
    thief = _cand("hijack", [("A1:B3", 1)], confidence=1.5)  # same signature
    big = _cand("big", [("A1:B7", 1)])
    a = assess_sheet_full([v, thief, big],
                          source=_GridSource({"S": _TWO_STACKED}),
                          grid=_TWO_STACKED, sheet="S", arbiter=None)
    assert a.baseline is not None                  # found via signature
    assert a.winner is a.baseline                  # floor restored it
    assert any(f.category == "assessor-floor" for f in a.findings)


def test_baseline_none_when_no_vertical_lens(monkeypatch):
    _patch_scores(monkeypatch, {
        frozenset({"A1:B7"}): (12, 0, 1),
        frozenset({"A5:B7"}): (11, 0, 0),
    })
    big = _cand("big", [("A1:B7", 1)])
    q = _cand("q", [("A5:B7", 5)])
    a = assess_sheet_full([big, q], source=_GridSource({"S": _TWO_STACKED}),
                          grid=_TWO_STACKED, sheet="S", arbiter=None)
    assert a.baseline is None                      # genuinely no vertical
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_assess_sheet.py -k "label_steal or no_vertical" -v`
Expected: the label-steal test FAILS (`a.baseline is None` — dedup kept the 1.5-confidence "hijack" candidate, so the label lookup misses); the no-vertical test may already PASS (regression pin for the None path).

- [ ] **Step 3: Implement**

In `assess_sheet_full`, replace the single line

```python
    baseline = next((c for c in deduped if c.method == "vertical"), None)
```

with

```python
    # Baseline = the vertical lens's interpretation. Look up by label first;
    # if _dedup handed the identical interpretation to a higher-confidence
    # lens (label steal), recover it by signature so the floor and Stage-4
    # never silently disable (B2b final-review #4).
    baseline = next((c for c in deduped if c.method == "vertical"), None)
    if baseline is None:
        v = next((c for c in candidates if c.method == "vertical"), None)
        if v is not None:
            vsig = _signature(v)
            baseline = next((c for c in deduped if _signature(c) == vsig), None)
```

- [ ] **Step 4: Run the file's tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_assess_sheet.py -q`
Expected: PASS.
Run: `.venv/bin/python -m pytest -q`
Expected: **387 passed, 1 skipped** (385 + 2), zero failures.

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/analyzers/assess.py tests/test_assess_sheet.py
git commit -m "fix(assess): find the vertical baseline by signature when dedup steals its label"
```

---

### Task 4: Agentic lens core — schema, policy, materialization, `try_layout` scorer

**Files:**
- Create: `mcg_swarm/analyzers/agentic.py` (everything except the lens class — that is Task 5)
- Test: `tests/test_agentic_lens.py` (new, 5 tests)

**Interfaces:**
- Consumes: `handle_from_region` (splitter), `coverage_score`/`nonempty_cells` (coverage), `TransposedView` (views), `LayoutCandidate`/`Finding`, `score_handles` (lazy), `Tool`/`SheetView`/`build_sheet_toolset` (lazy).
- Produces: `ProposedLayoutTable(region, header_row, header_span=1, orientation="vertical")` + `SheetLayoutPatch(tables=[], rationale="")` pydantic models; `AgenticLensPolicy(max_tables=12, max_probe_iterations=20)`; `_materialize(patch, grid, sheet, source, policy) -> list[LayoutCandidate]` (pure); `_score_proposal(source, grid, sheet, tables_arg, policy) -> dict`; `_build_agentic_toolset(source, grid, sheet, policy, counter) -> list[Tool]` (sheet probes + `try_layout`). Task 5's lens class calls all of these.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agentic_lens.py`:

```python
"""Pure-agentic layout lens: propose STRUCTURE, verify deterministically."""
from mcg_swarm.analyzers.agentic import (
    AgenticLensPolicy, SheetLayoutPatch, _build_agentic_toolset, _materialize,
    _score_proposal,
)
from tests.test_views import _GridSource

_HORIZONTAL = {"S": [("Region", "North", "South"), ("Sales", 10, 20)]}
_STACKED = {"S": [("Region", "Sales"), ("North", 10), ("South", 20),
                  (None, None),
                  ("Dept", "Cost"), ("Eng", 100), ("Ops", 50)]}
_POLICY = AgenticLensPolicy()


def test_materialize_transposed_proposal():
    src = _GridSource(_HORIZONTAL)
    patch = SheetLayoutPatch(tables=[{"region": "A1:B3", "header_row": 1,
                                      "orientation": "transposed"}])
    out = _materialize(patch, src.read_region("S"), "S", src, _POLICY)
    assert len(out) == 1
    c = out[0]
    assert c.method == "agentic" and c.confidence == 0.7
    assert type(c.view).__name__ == "TransposedView"
    assert c.handles[0].region == "A1:B3"          # view coordinates
    assert [col.name for col in c.handles[0].columns] == ["Region", "Sales"]


def test_materialize_mixed_orientation_keeps_vertical_subset():
    src = _GridSource(_STACKED)
    patch = SheetLayoutPatch(tables=[
        {"region": "A1:B3", "header_row": 1, "orientation": "vertical"},
        {"region": "A5:B7", "header_row": 5, "orientation": "transposed"}])
    out = _materialize(patch, src.read_region("S"), "S", src, _POLICY)
    assert len(out) == 1 and len(out[0].handles) == 1
    assert out[0].handles[0].region == "A1:B3" and out[0].view is None
    assert any(f.category == "agentic-lens" and f.severity == "warning"
               for f in out[0].findings)


def test_materialize_malformed_region_skipped_with_finding():
    src = _GridSource(_STACKED)
    patch = SheetLayoutPatch(tables=[
        {"region": "NOT-A-RANGE", "header_row": 1},
        {"region": "A1:B3", "header_row": 1}])
    out = _materialize(patch, src.read_region("S"), "S", src, _POLICY)
    assert len(out) == 1 and len(out[0].handles) == 1
    assert any("NOT-A-RANGE" in f.message for f in out[0].findings)


def test_score_proposal_returns_deterministic_metrics():
    src = _GridSource(_STACKED)
    res = _score_proposal(src, src.read_region("S"), "S",
                          [{"region": "A1:B3", "header_row": 1},
                           {"region": "A5:B7", "header_row": 5}], _POLICY)
    assert res["ok"] is True and res["tables"] == 2
    assert res["coverage_cells"] == 12 and res["errors"] == 0
    bad = _score_proposal(src, src.read_region("S"), "S",
                          [{"region": "zzz"}], _POLICY)
    assert bad["ok"] is False


def test_try_layout_tool_enforces_probe_budget():
    src = _GridSource(_STACKED)
    counter = {"probes": 0}
    tools = _build_agentic_toolset(src, src.read_region("S"), "S",
                                   AgenticLensPolicy(max_probe_iterations=1),
                                   counter)
    try_layout = next(t for t in tools if t.name == "try_layout")
    ok = try_layout.handler({"tables": [{"region": "A1:B3", "header_row": 1}]})
    assert ok["ok"] is True
    blocked = try_layout.handler({"tables": [{"region": "A1:B3", "header_row": 1}]})
    assert blocked["ok"] is False and "budget" in blocked["error"]
    assert {t.name for t in tools} >= {"dimensions", "peek_rows", "peek_region",
                                       "try_layout"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_agentic_lens.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcg_swarm.analyzers.agentic'`.

- [ ] **Step 3: Create `mcg_swarm/analyzers/agentic.py`**

```python
"""Pure-agentic layout lens (design 2026-07-02): an agent with NO structural
assumptions maps a sheet's complete table layout. The agent proposes STRUCTURE
only — regions/header rows/orientation — never values: handles are re-materialized
deterministically and every downstream value flows through the existing
extraction + quality gate, so a hallucinated layout is caught, not ingested.
`try_layout` exposes the deterministic scorer as a sandbox tool so the agent
iterates until clean BEFORE finalizing. Policy caps bound the loop regardless
of the agent's behavior. Candidates compete in the ensemble like any lens
(confidence 0.7 < vertical's 1.0: identical interpretations dedup to the
vertical label — the "agreed by both approaches" signal)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from mcg_swarm.analyzers.base import LayoutCandidate
from mcg_swarm.coverage import coverage_score, nonempty_cells
from mcg_swarm.schemas import Finding
from mcg_swarm.splitter import handle_from_region
from mcg_swarm.views import TransposedView


class ProposedLayoutTable(BaseModel):
    """One table in an agent layout proposal (view coordinates when transposed)."""
    region: str
    header_row: int
    header_span: int = 1
    orientation: Literal["vertical", "transposed"] = "vertical"


class SheetLayoutPatch(BaseModel):
    """The layout agent's `finalize` output: the full set of tables on the sheet."""
    tables: list[ProposedLayoutTable] = []
    rationale: str = ""


@dataclass(frozen=True)
class AgenticLensPolicy:
    max_tables: int = 12              # guard against runaway proposals
    max_probe_iterations: int = 20    # try_layout calls per sheet


def _finding(sheet: str, message: str) -> Finding:
    return Finding(category="agentic-lens", severity="warning", scope="sheet",
                   source="agent", ref=f"{sheet}!A1", message=message)


def _materialize(patch: SheetLayoutPatch, grid, sheet: str, source,
                 policy: AgenticLensPolicy) -> list[LayoutCandidate]:
    """Proposal -> at most one LayoutCandidate, deterministically. Pure."""
    findings: list[Finding] = []
    tables = list(patch.tables)
    if len(tables) > policy.max_tables:
        findings.append(_finding(
            sheet, f"proposal had {len(tables)} tables; capped at "
                   f"{policy.max_tables}"))
        tables = tables[:policy.max_tables]
    if not tables:
        return []
    if len({t.orientation for t in tables}) > 1:
        findings.append(_finding(
            sheet, "mixed-orientation proposal; kept only the vertical tables "
                   "(one orientation per proposal in v1)"))
        tables = [t for t in tables if t.orientation == "vertical"]
        if not tables:
            return []
    orientation = tables[0].orientation
    view = TransposedView(source) if orientation == "transposed" else None
    eff_grid = view.read_region(sheet) if view is not None else grid
    handles = []
    for pt in tables:
        try:
            handles.append(handle_from_region(
                eff_grid, sheet, pt.region, pt.header_row, pt.header_span))
        except Exception as e:
            findings.append(_finding(
                sheet, f"malformed proposed region {pt.region!r} skipped ({e})"))
    if not handles:
        return []
    total = len(nonempty_cells(eff_grid))
    cov = (coverage_score(eff_grid, [h.region for h in handles]) / total
           if total else 0.0)
    return [LayoutCandidate(method="agentic", handles=tuple(handles),
                            coverage=cov, findings=tuple(findings),
                            confidence=0.7, view=view)]


def _score_proposal(source, grid, sheet: str, tables_arg,
                    policy: AgenticLensPolicy) -> dict:
    """Deterministic scorer behind the try_layout tool: same materialization
    as finalize, scored with the ensemble's own metric. Never raises."""
    try:
        patch = SheetLayoutPatch.model_validate({"tables": tables_arg})
    except Exception as e:
        return {"ok": False, "error": f"invalid proposal: {e}"}
    try:
        cands = _materialize(patch, grid, sheet, source, policy)
        if not cands:
            return {"ok": False, "error": "no valid tables in proposal"}
        c = cands[0]
        # Lazy: structural pulls in the orchestration stack.
        from mcg_swarm.subagent.structural import score_handles
        c_src = c.view if c.view is not None else source
        c_grid = c.view.read_region(sheet) if c.view is not None else grid
        cov, errors, gaps = score_handles(c_src, c_grid, list(c.handles), sheet)
        return {"ok": True, "tables": len(c.handles), "coverage_cells": cov,
                "errors": errors, "gaps": gaps,
                "notes": [f.message for f in c.findings]}
    except Exception as e:  # a hostile proposal must not sink the agent loop
        return {"ok": False, "error": f"scoring failed: {e}"}


def _build_agentic_toolset(source, grid, sheet: str, policy: AgenticLensPolicy,
                           counter: dict) -> list:
    """Read-only sheet probes + the try_layout sandbox scorer (budgeted)."""
    # Lazy: subagent pulls in the orchestration stack.
    from mcg_swarm.subagent.structural_tools import SheetView, build_sheet_toolset
    from mcg_swarm.subagent.tools import Tool

    tools = build_sheet_toolset(SheetView(source, sheet))

    def _try(args):
        counter["probes"] += 1
        if counter["probes"] > policy.max_probe_iterations:
            return {"ok": False,
                    "error": "probe budget exhausted — call finalize now with "
                             "your best layout"}
        return _score_proposal(source, grid, sheet,
                               (args or {}).get("tables", []), policy)

    tools.append(Tool(
        "try_layout",
        "Score a candidate layout WITHOUT committing it: pass the same `tables` "
        "list you would pass to `finalize`. Returns deterministic metrics "
        "(coverage_cells, errors, gaps) — iterate until errors and gaps are 0 "
        "and coverage stops improving, then finalize the same list.",
        {"type": "object",
         "properties": {"tables": {"type": "array"}},
         "required": ["tables"]},
        _try))
    return tools
```

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_agentic_lens.py -v`
Expected: PASS (5 tests).
Run: `.venv/bin/python -m pytest -q`
Expected: **392 passed, 1 skipped** (387 + 5), zero failures.

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/analyzers/agentic.py tests/test_agentic_lens.py
git commit -m "feat(analyzers): agentic lens core — layout schema, deterministic materialization, try_layout scorer"
```

---

### Task 5: `AgenticLayoutLens` + runner-aware registry

**Files:**
- Modify: `mcg_swarm/analyzers/agentic.py` (append the lens class + prompts), `mcg_swarm/analyzers/registry.py`, `mcg_swarm/analyzers/pipeline.py` (one line)
- Test: `tests/test_agentic_lens.py` (append 4)

**Interfaces:**
- Consumes: everything Task 4 produced; `AgentRunner.run(seed, tools, *, schema, system)`; `FakeAgentRunner`.
- Produces: `AgenticLayoutLens(runner=None, policy=None)` with `name = "agentic"`, class attribute `needs_runner = True`, `analyze(grid, sheet, source=None)`; `build_analyzers(names, runner=None)` — factories with a truthy `needs_runner` attribute are called as `factory(runner=runner)`, all others as `factory()` (existing KeyError-before-instantiation behavior preserved); `"agentic"` registered at import; `analyze_workbook` passes its `runner` to `build_analyzers`. Task 6 and the application rely on all of this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agentic_lens.py`:

```python
from mcg_swarm.analyzers.agentic import AgenticLayoutLens
from mcg_swarm.analyzers.registry import build_analyzers
from mcg_swarm.subagent.agent_runner import FakeAgentRunner


def test_lens_returns_empty_without_runner_or_source():
    src = _GridSource(_STACKED)
    assert AgenticLayoutLens(runner=None).analyze(
        src.read_region("S"), "S", source=src) == []
    runner = FakeAgentRunner(actions=[], final={"tables": []})
    assert AgenticLayoutLens(runner=runner).analyze(
        src.read_region("S"), "S", source=None) == []


def test_lens_full_flow_with_fake_runner():
    """The agent probes with try_layout, then finalizes the same layout; the
    lens materializes ONE candidate from the validated patch."""
    tables = [{"region": "A1:B3", "header_row": 1},
              {"region": "A5:B7", "header_row": 5}]
    runner = FakeAgentRunner(
        actions=[{"tool": "dimensions"},
                 {"tool": "try_layout", "args": {"tables": tables}}],
        final={"tables": tables, "rationale": "two stacked tables"})
    src = _GridSource(_STACKED)
    out = AgenticLayoutLens(runner=runner).analyze(
        src.read_region("S"), "S", source=src)
    assert len(out) == 1 and len(out[0].handles) == 2
    assert out[0].method == "agentic"
    probe = runner.observations[1]          # the try_layout observation
    assert probe["ok"] is True and probe["errors"] == 0


def test_build_analyzers_threads_runner_to_marked_factories():
    runner = FakeAgentRunner(actions=[], final={"tables": []})
    built = build_analyzers(("vertical", "agentic"), runner=runner)
    assert built[1]._runner is runner       # agentic got the runner
    assert not hasattr(built[0], "_runner") # vertical untouched
    unbuilt = build_analyzers(("agentic",))  # no runner: constructs fine,
    src = _GridSource(_STACKED)              # analyze degrades to []
    assert unbuilt[0].analyze(src.read_region("S"), "S", source=src) == []


def test_unknown_name_still_raises_before_instantiation():
    import pytest
    with pytest.raises(KeyError):
        build_analyzers(("vertical", "no_such_lens"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_agentic_lens.py -k "lens_returns or full_flow or threads_runner or unknown_name" -v`
Expected: FAIL — `ImportError` (`AgenticLayoutLens` doesn't exist) and `TypeError: build_analyzers() got an unexpected keyword argument 'runner'`.

- [ ] **Step 3: Implement**

(a) Append to `mcg_swarm/analyzers/agentic.py`:

```python
AGENTIC_SYSTEM = (
    "You are mapping the COMPLETE table layout of ONE spreadsheet sheet with NO "
    "prior structural assumptions — the sheet may hold several tables, transposed "
    "tables, title banners, notes, or chart areas. Inspect the actual cells with "
    "the read-only tools. Iterate with `try_layout` until your layout scores "
    "clean (maximal coverage_cells, zero errors, zero gaps), then call `finalize` "
    "with the SAME tables list. Every table needs its A1 `region`, absolute "
    "`header_row`, `header_span` (1 unless a genuine multi-row header), and "
    "`orientation`: 'vertical' when headers run across the top, 'transposed' when "
    "they run down the first column. For transposed tables give region and "
    "header_row in TRANSPOSED coordinates (the sheet as if rows and columns were "
    "swapped). All tables in one proposal must share ONE orientation. Exclude "
    "banners, notes, and chart areas from every region. Never invent cells or "
    "tables."
)


def _agentic_seed(sheet: str, grid) -> str:
    n_rows = len(grid)
    n_cols = max((len(r) for r in grid), default=0)
    return "\n".join([
        f"Map the complete table layout of sheet {sheet!r} "
        f"(~{n_rows} used rows x {n_cols} used columns).",
        "Start with `dimensions` and `peek_rows`, probe candidate layouts with "
        "`try_layout`, and only `finalize` a layout you have scored.",
    ])


class AgenticLayoutLens:
    """The pure-agentic lens: just another SheetAnalyzer to the ensemble."""

    name = "agentic"
    needs_runner = True

    def __init__(self, runner=None, policy: AgenticLensPolicy | None = None):
        self._runner = runner
        self._policy = policy or AgenticLensPolicy()

    def analyze(self, grid, sheet: str, source=None) -> list[LayoutCandidate]:
        if self._runner is None or source is None:
            return []  # graceful degradation; run_swarm's validation build is runner-less
        counter = {"probes": 0}
        tools = _build_agentic_toolset(source, grid, sheet, self._policy, counter)
        raw = self._runner.run(_agentic_seed(sheet, grid), tools,
                               schema=SheetLayoutPatch, system=AGENTIC_SYSTEM)
        patch = SheetLayoutPatch.model_validate(raw)
        return _materialize(patch, grid, sheet, source, self._policy)
```

(b) In `mcg_swarm/analyzers/registry.py`: extend `build_analyzers` to

```python
def build_analyzers(names, runner=None):
```

keeping the existing validate-all-names-first KeyError behavior, and change the instantiation to

```python
    return [f(runner=runner) if getattr(f, "needs_runner", False) else f()
            for f in factories]
```

(adapt to the file's actual local variable names — the two-phase validate-then-instantiate structure must remain). Register the lens where `"vertical"` is registered:

```python
from mcg_swarm.analyzers.agentic import AgenticLayoutLens
register("agentic", AgenticLayoutLens)
```

(c) In `mcg_swarm/analyzers/pipeline.py` (`analyze_workbook`), change

```python
    analyzers = build_analyzers(config.analyzers)
```

to

```python
    analyzers = build_analyzers(config.analyzers, runner=runner)
```

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_agentic_lens.py tests/test_analyzers.py tests/test_pipeline.py -q`
Expected: PASS.
Run: `.venv/bin/python -m pytest -q`
Expected: **396 passed, 1 skipped** (392 + 4), zero failures.

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/analyzers/agentic.py mcg_swarm/analyzers/registry.py mcg_swarm/analyzers/pipeline.py tests/test_agentic_lens.py
git commit -m "feat(analyzers): AgenticLayoutLens + runner-aware build_analyzers"
```

---

### Task 6: E2E battery + operator documentation

**Files:**
- Test: `tests/test_agentic_e2e.py` (new, 3 tests)
- Modify: `docs/how_to_use.md` (runner-profile section), `DATA-REQUIREMENTS.md` (assumption-relaxation note)

**Interfaces:**
- Consumes: everything Tasks 4–5 produced; `run_swarm`, `build_indices`, `SwarmConfig`, `FakeAgentRunner`; B2b's arbiter/floor/Stage-4 (untouched).
- Produces: proof that agentic candidates ride the full pipeline (orientation persistence, adapter rebuild, axis-correct queries) and compete safely in the ensemble; operator docs for configuring the live lens runner.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agentic_e2e.py`:

```python
"""E2E: the pure-agentic lens through run_swarm — propose structure, extract
deterministically, prove by query. FakeAgentRunner throughout (no live SDK).

config(validate=False, alter_boundaries=False) quiets the band verifier and
Layer-2 reviewer so the injected runner reaches ONLY the analyzer layer."""
from mcg_swarm.config import SwarmConfig
from mcg_swarm.runner import build_indices, run_swarm
from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from tests.test_views import _GridSource

_HORIZONTAL = {"S": [("Region", "North", "South"), ("Sales", 10, 20)]}
_STACKED = {"S": [("Region", "Sales"), ("North", 10), ("South", 20),
                  (None, None),
                  ("Dept", "Cost"), ("Eng", 100), ("Ops", 50)]}
_QUIET = dict(validate=False, alter_boundaries=False)


def test_agentic_lens_transposed_sheet_end_to_end():
    """Agent proposes a transposed reading -> run_swarm extracts through the
    view -> orientation persists -> adapter rebuild queries the right axis."""
    proposal = [{"region": "A1:B3", "header_row": 1, "orientation": "transposed"}]
    runner = FakeAgentRunner(
        actions=[{"tool": "try_layout", "args": {"tables": proposal}}],
        final={"tables": proposal, "rationale": "fields run down column A"})
    src = _GridSource(_HORIZONTAL)
    ex = run_swarm(src, runner=runner,
                   config=SwarmConfig(analyzers=("agentic",), **_QUIET))
    assert len(ex.tables) == 1
    t = ex.tables[0]
    assert t.orientation == "transposed" and not t.errors
    idx = build_indices(src, ex)[t.table_id]
    assert idx.query("North", "Sales").value == 10
    assert idx.query("South", "Sales").value == 20


def test_agentic_lens_multi_table_sheet_end_to_end():
    proposal = [{"region": "A1:B3", "header_row": 1},
                {"region": "A5:B7", "header_row": 5}]
    runner = FakeAgentRunner(actions=[], final={"tables": proposal})
    src = _GridSource(_STACKED)
    ex = run_swarm(src, runner=runner,
                   config=SwarmConfig(analyzers=("agentic",), **_QUIET))
    assert sorted(t.region for t in ex.tables) == ["A1:B3", "A5:B7"]
    assert all(not t.errors for t in ex.tables)
    idx = build_indices(src, ex)
    bottom = next(t for t in ex.tables if t.region == "A5:B7")
    assert idx[bottom.table_id].query("Eng", "Cost").value == 100


def test_agentic_agrees_with_vertical_dedups_to_baseline():
    """'Agreed by both approaches': identical interpretation -> Stage-0 dedup
    keeps the vertical label (agentic confidence 0.7 < 1.0), single candidate,
    no arbiter consult, extraction identical to the deterministic run."""
    proposal = [{"region": "A1:B3", "header_row": 1}]
    runner = FakeAgentRunner(actions=[], final={"tables": proposal})
    vertical = {"S": [("Region", "Sales"), ("North", 10), ("South", 20)]}
    ex = run_swarm(_GridSource(vertical), runner=runner,
                   config=SwarmConfig(analyzers=("vertical", "agentic"), **_QUIET))
    base = run_swarm(_GridSource(vertical), config=SwarmConfig(**_QUIET))
    assert [t.region for t in ex.tables] == [t.region for t in base.tables]
    assert [t.table_id for t in ex.tables] == [t.table_id for t in base.tables]
    assert not [f for f in ex.findings
                if f.category in ("contested-layout", "arbiter-choice")]
```

- [ ] **Step 2: Run tests to verify state**

Run: `.venv/bin/python -m pytest tests/test_agentic_e2e.py -v`
Expected: with Tasks 4-5 landed all three PASS on first run. If any fails, the integration has a real bug: STOP and report the failure verbatim; do not weaken assertions. (Note: `runner.calls` in test 3 is 1 — the lens itself consults the agent; what must NOT happen is an arbiter consult, asserted via the findings sweep.)

- [ ] **Step 3: Documentation**

(a) Append to `docs/how_to_use.md` a section **"Enabling the pure-agentic layout lens"**: config is `SwarmConfig(analyzers=("vertical", "agentic"))` plus an injected `AgentRunner`; for the live path construct `ClaudeSDKAgentRunner` (agent_runtime) with a HIGHER turn budget than the band verifier (e.g. `max_turns=24`), and optionally `host_tools=("Bash", "Read")` + `permission_mode` for investigation capability — noting that host-tool sandboxing (e.g. confining Bash to one scratch folder) is configured at the SDK/application permission layer, NOT enforced by mcg_swarm; the swarm's own guarantees are structural (finalize-only output, deterministic re-extraction, quality gate, ensemble floor + live re-validation). Mention `AgenticLensPolicy` caps and that `analyzers=("agentic",)` alone is supported (agent-only analysis).

(b) Append to `DATA-REQUIREMENTS.md` under the assumptions section: with the `"agentic"` lens enabled, assumptions **A1 (one table per tab)** and **A3 (vertical orientation)** become soft — the ensemble can propose and verify multi-table and transposed layouts; A-series assumptions still describe what the DEFAULT config guarantees. Gate hardening (this phase) added row-key collision/blank-key/dropped-column/empty-index failures — note that workbooks with genuinely duplicate row keys now FAIL the gate instead of silently losing rows (was: silent last-wins).

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: **399 passed, 1 skipped** (396 + 3), zero failures.

- [ ] **Step 5: Commit**

```bash
git add tests/test_agentic_e2e.py docs/how_to_use.md DATA-REQUIREMENTS.md
git commit -m "test(agentic): e2e battery — agentic lens through run_swarm; operator docs"
```

---

## Controller exit gate (after Task 6, before merge)

1. Full suite: zero failures (399 expected).
2. Corpus diff (Phase-A procedure, default config, background job): expected byte-identical EXCEPT possibly new gate-failure findings (`row-key collision:`/`blank row key:`/`column-coverage:`/`empty index:`) on workbooks with real defects. Any other diff = regression. New gate findings on corpus workbooks are surfaced to the user for adjudication before merge.

## Deferred (explicitly NOT in this plan)

- **`MaterializedView`** (cell-surgery escape hatch: cleaned grid + provenance map) — needs its own design pass on snapshot-vs-live semantics.
- **Replayable probe/transform script artifacts** — application-side (SDK runner host tools); revisit when the live profile is exercised on the real messy dataset.
- **Full `run_table_tests` as an in-loop agent tool** — requires a column schema that only exists post-orchestration; `try_layout`'s `score_handles` (which embeds static orchestration errors) is the v1 proxy.
- **Human-in-the-loop candidate picking** (business-user verification) — design round 2; plugs into the B2b arbiter slot.
- **`query_cell`/`query_range` dtype fabrication + duplicate-COLUMN hard-fail escalation** — consumption-layer design round 2.
- **StructuralReviewer subsumption + #8 + `build_indices` view-kind generalization** — carried unchanged from B2b's deferred list.
- Gate minors: dtype small-sample skip (<5), `sample_size == 25` sentinel quirk — cosmetic, batch with the subsumption cleanup.

## Self-Review

**1. Coverage:** all five gate findings → Tasks 1-2 (tautology → real Phase 1; duplicate/blank/empty → T1 records + T2 failures; dropped columns → T2 reverse check); B2b carry #4 → Task 3; settled agentic design (structure-not-values, gate-as-tool, policy caps, ensemble competition, "agreed" badge) → Tasks 4/5/6; sandbox/skill/subagent harness → documented as SDK-side (Task 6 docs) with the honest enforcement boundary stated. ✓
**2. Placeholder scan:** none; the two prose steps (Task 5 Step 3b registry adaptation, Task 6 Step 3 docs) name exact targets and bound the edit. ✓
**3. Type consistency:** `_materialize(patch, grid, sheet, source, policy)` / `_score_proposal(source, grid, sheet, tables_arg, policy)` / `_build_agentic_toolset(source, grid, sheet, policy, counter)` uniform between Task 4 code and Task 4/5 tests; `needs_runner` marker consistent between lens class and registry; `duplicate_row_keys` tuple order `(key, shadowed, winner)` consistent between T1 code/tests and T2 failure message; suite arithmetic 377 → +3 → +5 → +2 → +5 → +4 → +3 = **399**. ✓
