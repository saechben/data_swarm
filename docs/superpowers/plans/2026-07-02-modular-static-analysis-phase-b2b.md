# Modular Static Analysis — Phase B2b Implementation Plan (Agentic Arbiter + Assessment Guarantees)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the assessor's guarantee stack (spec §4.5 Stages 2–4): an agentic arbiter chooses among top-K candidates on genuine lens disagreement, a verify-before-accept floor makes the ensemble provably never-worse than the vertical baseline, and run_swarm live-re-validates any contested non-baseline winner before commitment — plus the three deterministic hardening items from the B2a final review.

**Architecture:** `assess_sheet_full` (new, in assess.py) returns an `Assessment` record (winner + baseline + contested flag + findings); it implements Stage 2 (arbiter hook, duck-typed) and Stage 3 (floor vs the vertical candidate). `LayoutArbiter` (new module, `analyzers/arbiter.py`) drives the injected `AgentRunner` over the existing read-only sheet toolset with a pick-one-of-K schema. The runner threads `run_swarm → analyze_workbook → analyze_sheet → assess_sheet_full`, gated on `SwarmConfig.arbitrate`. In `run_swarm`, a contested winner whose interpretation differs from the baseline gets the Stage-4 live A/B (mirroring the existing Layer-2 re-cut pattern): orchestrate both, commit whichever has fewer live errors. Views now declare their orientation via an `orientation` attribute (kills the B2a-review `isinstance` dispatch trap).

**Tech Stack:** Python 3, pytest, pydantic (already present). No new dependencies. `FakeAgentRunner` (exists) for all agent tests — no live SDK calls in the suite.

## Global Constraints

- **Corpus neutrality with default config is the exit criterion.** With `SwarmConfig()` (analyzers=`("vertical",)`, no runner) output must be byte-identical to `main`. Neutral by construction: one lens → one candidate → `assess_sheet_full` single-candidate identity return (`contested=False`), so the arbiter, floor, and Stage-4 branch are all unreachable. Controller runs the corpus diff after the last task.
- Test command: `.venv/bin/python -m pytest -q` (NOT bare `pytest`). Baseline before Task 1: **353 passed, 1 skipped**. Zero failures tolerated at any commit.
- Plain `assess()` stays exported and **byte-unchanged**. `rank_candidates` changes ONLY by the Task-1 source guard (2 lines at the top); its scoring loop and sort key are untouched.
- `detect_table`, splitter helpers, `StructuralReviewer`, and run_swarm's Layer-2 re-cut branch stay **functionally intact**. Subsumption of the reviewer and item #8 (multi-handle scan) are explicitly deferred (see Deferred section) — do NOT refactor them.
- Never-raise contract holds: the pipeline's per-lens try/except and guarded winner selection stay structurally intact; `assess_sheet_full` itself never raises past the guard, and arbiter failures degrade to the deterministic top + a warning Finding.
- Import direction rules: `analyzers → subagent` lazy only (`rank_candidates` already lazy-imports `score_handles`; `LayoutArbiter.choose` lazy-imports `structural_tools`); `analyzers → schemas/config/source/splitter` top-level OK; `runner → views` top-level OK.
- `model_copy(update=...)` is NOT safe on `CanonicalTable` — nothing in this plan touches CanonicalTable construction; do not add any.
- `Finding` requires `message`; `severity ∈ {"error","warning","info"}`; `source ∈ {"static","gate","agent"}`. New categories introduced here: `unknown-view`, `arbiter-error`, `arbiter-choice`, `assessor-floor`, `contested-layout`.
- Convention (document, don't enforce): configs should list `"vertical"` first in `analyzers` — `_dedup` keeps the first-seen candidate on confidence ties, so listing vertical first keeps the baseline label stable when another 1.0-confidence lens emits the identical interpretation.
- Spec: `docs/superpowers/specs/2026-07-01-modular-static-analysis-design.md` §4.5 (stages), §7 (assessor test scenarios a–e). Spec's Stage-2 arbiter is **pick-one-of-K, never invent** (§8 open question resolved as pick-one).
- The spec's "no eval regression with a runner injected" exit criterion is covered in-suite by the FakeAgentRunner e2e battery (Task 6); a live-SDK eval run is a manual, user-triggered follow-up, not part of this plan.

---

## File Structure

**Modify:**
- `mcg_swarm/views.py` — `TransposedView.orientation = "transposed"` class attribute (Task 1).
- `mcg_swarm/runner.py` — `_view_orientation` helper + unknown-view warning (Task 1); pass `runner` to `analyze_workbook` (Task 4); Stage-4 contested live A/B branch + `_interpretation` helper (Task 5).
- `mcg_swarm/analyzers/assess.py` — source guard in `rank_candidates` (Task 1); `Assessment` + `assess_sheet_full` + `assess_sheet` wrapper (Task 2).
- `mcg_swarm/analyzers/base.py` — `SheetAnalysis` gains `contested`/`baseline_handles`/`baseline_view` (Task 4).
- `mcg_swarm/analyzers/pipeline.py` — call `assess_sheet_full`, surface baseline/contested, `arbiter` param (Task 4).
- `mcg_swarm/config.py` — `arbitrate: bool = True` (Task 4).

**Create:**
- `mcg_swarm/analyzers/arbiter.py` — `ArbiterVerdict`, `LayoutArbiter`, seed/system prompts (Task 3).
- `tests/test_arbiter.py` (Task 3), `tests/test_runner_stage4.py` (Task 5), `tests/test_arbiter_e2e.py` (Task 6).

---

### Task 1: View-orientation contract + B2a-review hardening items

**Files:**
- Modify: `mcg_swarm/views.py` (class attribute), `mcg_swarm/runner.py` (helper + loop), `mcg_swarm/analyzers/assess.py` (`rank_candidates` guard)
- Test: `tests/test_views.py` (append 1), `tests/test_view_e2e.py` (append 2), `tests/test_assess_sheet.py` (append 1)

**Interfaces:**
- Consumes: `TransposedView` (views.py), run_swarm's `orient` derivation (runner.py:54), `rank_candidates` (assess.py:57), `Finding` (schemas.py), `orchestrate_table(..., orientation=...)`.
- Produces: views declare `orientation` (`"vertical" | "transposed"`) as an attribute; `_view_orientation(view, sheet) -> (orientation, Finding | None)` in runner.py (Task 5 reuses it for the baseline view); `rank_candidates` raises `ValueError` on `source=None` instead of silently mis-scoring.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_views.py`:

```python
def test_transposed_view_declares_orientation():
    """Views must declare their persistable orientation as an attribute —
    run_swarm dispatches on it instead of isinstance (B2a final-review #1)."""
    view = TransposedView(_GridSource({"S": [("a", "b")]}))
    assert view.orientation == "transposed"
```

Append to `tests/test_view_e2e.py`:

```python
def test_error_stub_persists_orientation():
    """An ambiguous handle extracted through a view still records the view's
    orientation on its error stub (closes the B2a-review stub-test gap)."""
    from mcg_swarm.splitter import TableHandle
    src = _GridSource(_HORIZONTAL)
    view = TransposedView(src)
    bad = TableHandle("S", "A1:A1", 1, [], ambiguous=True, reason="forced stub")
    table = orchestrate_table(view, bad, table_id="S__stub",
                              orientation="transposed")
    assert table.errors                      # it IS a failure stub
    assert table.orientation == "transposed"


def test_unknown_view_kind_warns_and_persists_vertical():
    """A lens view with no `orientation` attribute must not silently misread:
    run_swarm persists 'vertical' AND emits an unknown-view warning finding."""
    from mcg_swarm.analyzers.base import LayoutCandidate
    from mcg_swarm.analyzers.registry import register
    from mcg_swarm.config import SwarmConfig
    from mcg_swarm.runner import run_swarm

    class _NamelessView:
        """Identity pass-through WorkbookSource wrapper with NO orientation attr."""
        def __init__(self, inner): self._inner = inner
        def sheet_names(self): return self._inner.sheet_names()
        def read_cell(self, sheet, row, col): return self._inner.read_cell(sheet, row, col)
        def read_region(self, sheet, min_row=None, max_row=None, min_col=None, max_col=None):
            return self._inner.read_region(sheet, min_row, max_row, min_col, max_col)
        def read_formula_region(self, sheet, min_row=None, max_row=None, min_col=None, max_col=None):
            return self._inner.read_formula_region(sheet, min_row, max_row, min_col, max_col)

    class _NamelessLens:
        name = "nameless_view"
        def analyze(self, grid, sheet, source=None):
            view = _NamelessView(source)
            handle = detect_table(view.read_region(sheet), sheet)
            return [LayoutCandidate(method="nameless_view", handles=(handle,),
                                    coverage=1.0, view=view)]

    register("nameless_view", _NamelessLens)
    vertical = {"S": [("Region", "Sales"), ("North", 10)]}
    ex = run_swarm(_GridSource(vertical),
                   config=SwarmConfig(analyzers=("nameless_view",)))
    assert ex.tables[0].orientation == "vertical"
    assert any(f.category == "unknown-view" and f.severity == "warning"
               for f in ex.findings)
```

Append to `tests/test_assess_sheet.py`:

```python
def test_rank_candidates_requires_source():
    """B2a final-review #4: source=None must fail loudly, not mis-score
    every handle into orchestration errors (the pipeline's never-raise guard
    turns the raise into a fallback stub + finding)."""
    import pytest
    grid = [("Region", "Sales"), ("North", 10)]
    c = LayoutCandidate(method="vertical", handles=(detect_table(grid, "S"),))
    with pytest.raises(ValueError):
        rank_candidates([c, c], source=None, grid=grid, sheet="S")
```

(Note: the two identical candidates dedup to one, but the guard must fire BEFORE dedup returns early — place it first in `rank_candidates`, so this test exercises it regardless.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_views.py::test_transposed_view_declares_orientation tests/test_view_e2e.py::test_error_stub_persists_orientation tests/test_view_e2e.py::test_unknown_view_kind_warns_and_persists_vertical tests/test_assess_sheet.py::test_rank_candidates_requires_source -v`
Expected: orientation-attr test FAILS (`AttributeError: 'TransposedView' object has no attribute 'orientation'`); unknown-view test FAILS (no `unknown-view` finding emitted); rank test FAILS (no ValueError — it mis-scores instead); the stub test may already PASS (B2a threaded orientation into `_stub`) — that is fine, it is the missing regression pin, not new behavior.

- [ ] **Step 3: Implement**

(a) In `mcg_swarm/views.py`, add a class attribute to `TransposedView` (first line of the class body, above `__init__`):

```python
    orientation = "transposed"   # persisted on CanonicalTable; runner dispatches on this
```

(b) In `mcg_swarm/runner.py`, add `Finding` to the schemas import (line 3):

```python
from mcg_swarm.schemas import WorkbookExtraction, Finding
```

and add a module-level helper above `run_swarm`:

```python
def _view_orientation(view, sheet: str):
    """Map a lens view to a persistable orientation.

    None → "vertical". Views declare theirs via an `orientation` attribute
    (TransposedView → "transposed"). An unknown view kind persists "vertical"
    plus a warning Finding: extraction still reads through the view, so the
    adapter rebuild may misread — surfacing beats silence.
    Returns (orientation, Finding | None).
    """
    if view is None:
        return "vertical", None
    orient = getattr(view, "orientation", None)
    if orient in ("vertical", "transposed"):
        return orient, None
    return "vertical", Finding(
        category="unknown-view", severity="warning", scope="sheet",
        source="static", ref=f"{sheet}!A1",
        message=(f"view {type(view).__name__} declares no known orientation; "
                 "persisted 'vertical' — adapter rebuilds may misread this sheet"))
```

then in the `run_swarm` loop replace the single line

```python
        orient = "transposed" if isinstance(sa.view, TransposedView) else "vertical"
```

with

```python
        orient, view_finding = _view_orientation(sa.view, sa.sheet)
        if view_finding is not None:
            wb_findings.append(view_finding)
```

(The `TransposedView` import in runner.py stays — `build_indices` still uses it.)

(c) In `mcg_swarm/analyzers/assess.py`, add as the FIRST two lines of `rank_candidates`'s body (before the lazy import):

```python
    if source is None:
        raise ValueError("rank_candidates requires a source (got None)")
```

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_views.py tests/test_view_e2e.py tests/test_assess_sheet.py -q`
Expected: PASS.
Run: `.venv/bin/python -m pytest -q`
Expected: **357 passed, 1 skipped** (353 + 4), zero failures.

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/views.py mcg_swarm/runner.py mcg_swarm/analyzers/assess.py tests/test_views.py tests/test_view_e2e.py tests/test_assess_sheet.py
git commit -m "feat(views,runner): views declare orientation; unknown-view warning; rank_candidates source guard"
```

---

### Task 2: `Assessment` record — Stage-3 floor + contested detection (deterministic core)

**Files:**
- Modify: `mcg_swarm/analyzers/assess.py`
- Test: `tests/test_assess_sheet.py` (append 6)

**Interfaces:**
- Consumes: `_dedup`, `_dominates`, `_signature`, `rank_candidates` (all exist, unchanged), `Finding`, `LayoutCandidate`.
- Produces: `Assessment(winner, baseline, contested, findings=())` frozen dataclass; `assess_sheet_full(candidates, *, source, grid, sheet, arbiter=None) -> Assessment`; `assess_sheet(...)` becomes a thin wrapper returning `.winner` (same signature + new `arbiter=None` kwarg). The arbiter is duck-typed: any object with `choose(ranked_topk, *, source, sheet) -> int` (Task 3 provides the real one; tests here use stubs).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_assess_sheet.py` (the file already imports `_GridSource`, `LayoutCandidate`, `detect_table`, `rank_candidates`; add `assess_sheet_full`, `Assessment` to the assess import and `handle_from_region` from splitter as needed):

```python
# --- Stage 2/3 policy tests: scores are controlled via a patched score_handles ---

_TWO_STACKED = [("Region", "Sales"), ("North", 10), ("South", 20),
                (None, None),
                ("Dept", "Cost"), ("Eng", 100), ("Ops", 50)]


def _cand(method, regions_and_hr, confidence=1.0):
    from mcg_swarm.splitter import handle_from_region
    handles = tuple(handle_from_region(_TWO_STACKED, "S", region, hr)
                    for region, hr in regions_and_hr)
    return LayoutCandidate(method=method, handles=handles, confidence=confidence)


def _patch_scores(monkeypatch, by_regions):
    """score_handles replacement keyed by the frozenset of handle regions."""
    def fake(source, grid, handles, sheet):
        return by_regions[frozenset(h.region for h in handles)]
    monkeypatch.setattr("mcg_swarm.subagent.structural.score_handles", fake)


class _NeverArbiter:
    def choose(self, ranked_topk, *, source, sheet):
        raise AssertionError("arbiter must not be consulted here")


class _PickArbiter:
    def __init__(self, idx): self.idx = idx
    def choose(self, ranked_topk, *, source, sheet): return self.idx


class _BoomArbiter:
    def choose(self, ranked_topk, *, source, sheet):
        raise RuntimeError("agent transport down")


def test_assessment_single_candidate_identity():
    v = _cand("vertical", [("A1:B3", 1)])
    a = assess_sheet_full([v], source=_GridSource({"S": _TWO_STACKED}),
                          grid=_TWO_STACKED, sheet="S", arbiter=_NeverArbiter())
    assert a.winner is v and a.baseline is v
    assert a.contested is False and a.findings == ()


def test_dominant_top_short_circuits_no_arbiter(monkeypatch):
    _patch_scores(monkeypatch, {
        frozenset({"A1:B3", "A5:B7"}): (12, 0, 0),   # dominates
        frozenset({"A1:B3"}): (6, 0, 0),
    })
    v = _cand("vertical", [("A1:B3", 1)])
    p = _cand("pair", [("A1:B3", 1), ("A5:B7", 5)])
    a = assess_sheet_full([v, p], source=_GridSource({"S": _TWO_STACKED}),
                          grid=_TWO_STACKED, sheet="S", arbiter=_NeverArbiter())
    assert a.winner is p and a.contested is False


def test_disagreement_floor_restores_baseline(monkeypatch):
    _patch_scores(monkeypatch, {
        frozenset({"A1:B7"}): (12, 1, 1),            # top by coverage, but errors+gaps
        frozenset({"A1:B3"}): (6, 0, 0),
    })
    v = _cand("vertical", [("A1:B3", 1)])
    big = _cand("big", [("A1:B7", 1)])
    a = assess_sheet_full([v, big], source=_GridSource({"S": _TWO_STACKED}),
                          grid=_TWO_STACKED, sheet="S", arbiter=None)
    assert a.contested is True
    # floor: big has MORE errors than the vertical baseline -> baseline stands
    assert a.winner is v
    assert any(f.category == "assessor-floor" for f in a.findings)


def test_arbiter_choice_honored_when_floor_passes(monkeypatch):
    _patch_scores(monkeypatch, {
        frozenset({"A1:B3"}): (6, 0, 0),             # vertical baseline
        frozenset({"A1:B7"}): (12, 0, 1),            # top (gaps keep it non-dominant)
        frozenset({"A5:B7"}): (11, 0, 0),            # runner-up, floor-passing
    })
    v = _cand("vertical", [("A1:B3", 1)])
    big = _cand("big", [("A1:B7", 1)])
    q = _cand("q", [("A5:B7", 5)])
    a = assess_sheet_full([v, big, q], source=_GridSource({"S": _TWO_STACKED}),
                          grid=_TWO_STACKED, sheet="S", arbiter=_PickArbiter(1))
    assert a.contested is True
    assert a.winner is q                              # arbiter's pick, floor OK
    assert any(f.category == "arbiter-choice" for f in a.findings)


def test_floor_overrides_arbiter_pick_below_baseline(monkeypatch):
    _patch_scores(monkeypatch, {
        frozenset({"A1:B3"}): (6, 0, 0),
        frozenset({"A1:B7"}): (12, 0, 1),
        frozenset({"A5:B7"}): (5, 1, 0),             # below baseline on BOTH axes
    })
    v = _cand("vertical", [("A1:B3", 1)])
    big = _cand("big", [("A1:B7", 1)])
    bad = _cand("bad", [("A5:B7", 5)])
    a = assess_sheet_full([v, big, bad], source=_GridSource({"S": _TWO_STACKED}),
                          grid=_TWO_STACKED, sheet="S", arbiter=_PickArbiter(2))
    assert a.winner is v                              # floor kept the baseline
    assert any(f.category == "arbiter-choice" for f in a.findings)
    assert any(f.category == "assessor-floor" for f in a.findings)


def test_arbiter_failure_and_out_of_range_degrade_to_top(monkeypatch):
    scores = {
        frozenset({"A1:B7"}): (12, 0, 1),
        frozenset({"A5:B7"}): (11, 0, 0),
    }
    _patch_scores(monkeypatch, scores)
    big = _cand("big", [("A1:B7", 1)])
    q = _cand("q", [("A5:B7", 5)])
    # (no vertical candidate at all -> floor is skipped, baseline is None)
    a = assess_sheet_full([big, q], source=_GridSource({"S": _TWO_STACKED}),
                          grid=_TWO_STACKED, sheet="S", arbiter=_BoomArbiter())
    assert a.winner is big and a.baseline is None
    assert any(f.category == "arbiter-error" for f in a.findings)

    b = assess_sheet_full([big, q], source=_GridSource({"S": _TWO_STACKED}),
                          grid=_TWO_STACKED, sheet="S", arbiter=_PickArbiter(7))
    assert b.winner is big
    assert any(f.category == "arbiter-error" for f in b.findings)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_assess_sheet.py -k "assessment or dominant or disagreement or arbiter or floor" -v`
Expected: FAIL — `ImportError`/`NameError` (`assess_sheet_full`, `Assessment` do not exist).

- [ ] **Step 3: Implement in `mcg_swarm/analyzers/assess.py`**

Add imports at the top (below the existing `LayoutCandidate` import):

```python
from dataclasses import dataclass

from mcg_swarm.schemas import Finding
```

Add below `_dominates` (leave `assess`, `_dedup`, `_signature`, `rank_candidates` untouched except Task 1's guard):

```python
@dataclass(frozen=True)
class Assessment:
    """assess_sheet_full result: the winner plus what Stages 3-4 need to guard it.

    baseline:  the deduped vertical-lens candidate when present (the floor and
               run_swarm's live A/B compare against it); None otherwise.
    contested: the top candidate failed to dominate the runner-up — genuine
               lens disagreement. run_swarm live-re-validates contested
               non-baseline winners before commitment (Stage 4).
    findings:  sheet-scope Finding records from the arbiter/floor decisions.
    """
    winner: LayoutCandidate
    baseline: LayoutCandidate | None
    contested: bool
    findings: tuple = ()
```

Replace the existing `assess_sheet` function with:

```python
def assess_sheet_full(candidates: list[LayoutCandidate], *, source, grid,
                      sheet: str, arbiter=None) -> Assessment:
    """Spec §4.5 Stages 0-3. Stage 4 (live re-validation) happens in run_swarm.

    Stage 0: dedup. A single surviving interpretation returns by identity
             (the neutrality anchor) — no scoring, no agent.
    Stage 1: rich rank. When the top dominates the runner-up on all three
             axes, short-circuit deterministically (no agent).
    Stage 2: genuine disagreement — the arbiter (duck-typed: choose(topk,
             source=..., sheet=...) -> int), when injected, picks among the
             top-K (K<=3). Arbiter failures/out-of-range picks degrade to the
             deterministic top with a warning finding. Never raises for that.
    Stage 3: floor — a winner that is not the vertical baseline must be
             provably not-worse (>= baseline coverage, <= baseline errors) or
             the baseline stands. The ensemble can never score below today's
             splitter on any sheet.
    """
    if not candidates:
        raise ValueError("assess_sheet requires at least one candidate")
    deduped = _dedup(candidates)
    baseline = next((c for c in deduped if c.method == "vertical"), None)
    if len(deduped) == 1:
        return Assessment(deduped[0], baseline, contested=False)

    ranked = rank_candidates(deduped, source=source, grid=grid, sheet=sheet)
    (top, top_score), (_, runner_up_score) = ranked[0], ranked[1]
    if _dominates(top_score, runner_up_score):
        return Assessment(top, baseline, contested=False)

    findings: list[Finding] = []
    winner, w_score = top, top_score
    if arbiter is not None:
        topk = ranked[:3]
        idx = 0
        try:
            idx = int(arbiter.choose(topk, source=source, sheet=sheet))
        except Exception as e:  # agent trouble must not sink assessment
            findings.append(Finding(
                category="arbiter-error", severity="warning", scope="sheet",
                source="agent", ref=f"{sheet}!A1",
                message=f"arbiter failed ({e}); kept deterministic top"))
        if not 0 <= idx < len(topk):
            findings.append(Finding(
                category="arbiter-error", severity="warning", scope="sheet",
                source="agent", ref=f"{sheet}!A1",
                message=f"arbiter chose out-of-range candidate {idx}; "
                        "kept deterministic top"))
            idx = 0
        winner, w_score = topk[idx]
        if idx != 0:
            findings.append(Finding(
                category="arbiter-choice", severity="info", scope="sheet",
                source="agent", ref=f"{sheet}!A1",
                message=(f"arbiter chose {winner.method!r} over deterministic "
                         f"top {top.method!r}")))

    if baseline is not None and _signature(winner) != _signature(baseline):
        b_score = next(s for c, s in ranked if _signature(c) == _signature(baseline))
        if not (w_score[0] >= b_score[0] and w_score[1] <= b_score[1]):
            findings.append(Finding(
                category="assessor-floor", severity="info", scope="sheet",
                source="static", ref=f"{sheet}!A1",
                message=(f"winner {winner.method!r} scored below the vertical "
                         f"baseline (coverage {w_score[0]} vs {b_score[0]}, "
                         f"errors {w_score[1]} vs {b_score[1]}); kept baseline")))
            winner = baseline
    return Assessment(winner, baseline, contested=True, findings=tuple(findings))


def assess_sheet(candidates: list[LayoutCandidate], *, source, grid,
                 sheet: str, arbiter=None) -> LayoutCandidate:
    """Back-compat wrapper: the assessed winner only (see assess_sheet_full)."""
    return assess_sheet_full(candidates, source=source, grid=grid, sheet=sheet,
                             arbiter=arbiter).winner
```

(The `ValueError` message stays `"assess_sheet requires at least one candidate"` — existing tests assert on that behavior via `pytest.raises`.)

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_assess_sheet.py -v`
Expected: PASS — including every pre-existing `assess_sheet` test (the wrapper returns the identical winner when no arbiter is given).
Run: `.venv/bin/python -m pytest -q`
Expected: **363 passed, 1 skipped** (357 + 6), zero failures.

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/analyzers/assess.py tests/test_assess_sheet.py
git commit -m "feat(assess): Assessment record — Stage-3 floor + contested detection + arbiter hook"
```

---

### Task 3: `LayoutArbiter` — the Stage-2 agent (pick-one-of-K)

**Files:**
- Create: `mcg_swarm/analyzers/arbiter.py`
- Test: `tests/test_arbiter.py` (new, 3 tests)

**Interfaces:**
- Consumes: `AgentRunner` port (`runner.run(seed, tools, *, schema, system)`), `SheetView`/`build_sheet_toolset` (`subagent/structural_tools.py` — lazy import), `FakeAgentRunner` for tests.
- Produces: `LayoutArbiter(runner)` with `choose(ranked_topk, *, source, sheet) -> int` where `ranked_topk` is `[(LayoutCandidate, (cov, err, gaps)), ...]`. It MAY raise (transport/validation failures) — `assess_sheet_full` owns the never-raise policy. `ArbiterVerdict(choice: int, rationale: str = "")` pydantic schema.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_arbiter.py`:

```python
"""Stage-2 LayoutArbiter: drives the injected AgentRunner over the sheet toolset."""
import pytest

from mcg_swarm.analyzers.arbiter import LayoutArbiter
from mcg_swarm.analyzers.base import LayoutCandidate
from mcg_swarm.splitter import detect_table
from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from tests.test_views import _GridSource

_GRID = [("Region", "Sales"), ("North", 10), ("South", 20)]
_SRC = _GridSource({"S": _GRID})


def _topk():
    h = detect_table(_GRID, "S")
    a = LayoutCandidate(method="vertical", handles=(h,))
    b = LayoutCandidate(method="other", handles=(h,), confidence=0.8)
    return [(a, (6, 0, 0)), (b, (5, 1, 0))]


def test_arbiter_runs_toolset_and_returns_choice():
    runner = FakeAgentRunner(actions=[{"tool": "dimensions"}],
                             final={"choice": 1, "rationale": "matches data"})
    idx = LayoutArbiter(runner).choose(_topk(), source=_SRC, sheet="S")
    assert idx == 1
    assert runner.observations       # the probes ran against the REAL SheetView


def test_arbiter_seed_describes_candidates_and_scores():
    seen = {}

    class _SpyRunner:
        def run(self, seed, tools, *, schema, system=None):
            seen["seed"], seen["system"] = seed, system
            return {"choice": 0}

    LayoutArbiter(_SpyRunner()).choose(_topk(), source=_SRC, sheet="S")
    seed, system = seen["seed"], seen["system"]
    assert "[0]" in seed and "[1]" in seed          # candidates enumerated
    assert "vertical" in seed and "other" in seed   # methods named
    assert "coverage=6" in seed and "errors=1" in seed  # scores exposed
    assert "A1:B3" in seed                          # regions exposed
    assert "never invent" in system.lower()         # pick-one discipline


def test_arbiter_invalid_verdict_raises():
    runner = FakeAgentRunner(actions=[], final={"choice": "not-an-int"})
    with pytest.raises(Exception):                  # pydantic validation error
        LayoutArbiter(runner).choose(_topk(), source=_SRC, sheet="S")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_arbiter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcg_swarm.analyzers.arbiter'`.

- [ ] **Step 3: Create `mcg_swarm/analyzers/arbiter.py`**

```python
"""Stage-2 layout arbiter (spec §4.5): on genuine lens disagreement, an agent
chooses among the top-K ranked candidates. Pick-one-of-K, never invent — the
same bounded-blast-radius discipline as the structural re-cut gate. May raise;
assess_sheet_full owns the never-raise policy and the K-bounds check."""
from __future__ import annotations

from pydantic import BaseModel


class ArbiterVerdict(BaseModel):
    """The arbiter agent's `finalize` output."""
    choice: int
    rationale: str = ""


ARBITER_SYSTEM = (
    "You are choosing between COMPETING LAYOUT INTERPRETATIONS of ONE spreadsheet "
    "sheet. Deterministic lenses disagree about how this sheet is structured. Use "
    "the read-only tools to inspect the actual cells, then call `finalize` with the "
    "`choice` index of the interpretation that best matches the real data layout. "
    "You MUST pick one of the listed candidates — never invent a new layout. Prefer "
    "the interpretation whose header placement, orientation, and table boundaries "
    "match what you can see in the cells."
)


class LayoutArbiter:
    """Runs the injected AgentRunner to pick among ranked candidates."""

    def __init__(self, runner) -> None:
        self._runner = runner

    def choose(self, ranked_topk, *, source, sheet: str) -> int:
        """ranked_topk: [(LayoutCandidate, (coverage, errors, gaps)), ...],
        best-first. Returns the agent's chosen index into that list."""
        # Lazy: subagent pulls in the orchestration stack; keep analyzers light.
        from mcg_swarm.subagent.structural_tools import SheetView, build_sheet_toolset
        tools = build_sheet_toolset(SheetView(source, sheet))
        seed = _arbiter_seed(ranked_topk, sheet)
        raw = self._runner.run(seed, tools, schema=ArbiterVerdict,
                               system=ARBITER_SYSTEM)
        return int(ArbiterVerdict.model_validate(raw).choice)


def _describe(i: int, candidate, score) -> str:
    orientation = (getattr(candidate.view, "orientation", "vertical")
                   if candidate.view is not None else "vertical")
    regions = ", ".join(
        f"{h.region} (header row {h.header_row}, span {h.header_span})"
        for h in candidate.handles)
    cov, errors, gaps = score
    return (f"[{i}] method={candidate.method!r} orientation={orientation} "
            f"tables=[{regions}] coverage={cov} errors={errors} gaps={gaps} "
            f"confidence={candidate.confidence}")


def _arbiter_seed(ranked_topk, sheet: str) -> str:
    lines = [
        f"Sheet {sheet!r} has {len(ranked_topk)} competing layout interpretations "
        "(deterministic scoring could not separate them):",
    ]
    lines += [_describe(i, c, s) for i, (c, s) in enumerate(ranked_topk)]
    lines += [
        "Regions of a 'transposed' interpretation are in TRANSPOSED coordinates "
        "(rows and columns swapped relative to the raw sheet you inspect).",
        "Inspect the sheet with `dimensions`, `peek_rows`, and `peek_region`, then "
        "call `finalize` with the index (`choice`) of the best interpretation.",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_arbiter.py -v`
Expected: PASS (3 tests).
Run: `.venv/bin/python -m pytest -q`
Expected: **366 passed, 1 skipped** (363 + 3), zero failures.

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/analyzers/arbiter.py tests/test_arbiter.py
git commit -m "feat(analyzers): LayoutArbiter — Stage-2 pick-one-of-K agent over the sheet toolset"
```

---

### Task 4: Thread the runner/arbiter through the pipeline; surface baseline on SheetAnalysis

**Files:**
- Modify: `mcg_swarm/config.py`, `mcg_swarm/analyzers/base.py`, `mcg_swarm/analyzers/pipeline.py`, `mcg_swarm/runner.py` (one line)
- Test: `tests/test_pipeline.py` (append 5)

**Interfaces:**
- Consumes: `assess_sheet_full`/`Assessment` (Task 2), `LayoutArbiter` (Task 3), `SwarmConfig`.
- Produces: `SwarmConfig.arbitrate: bool = True`; `SheetAnalysis` gains `contested: bool = False`, `baseline_handles: tuple[TableHandle, ...] = ()`, `baseline_view: Any = None` (appended after `findings`, keyword-constructed); `analyze_sheet(analyzers, grid, sheet, source=None, arbiter=None)`; `analyze_workbook(source, config=None, runner=None)` builds a `LayoutArbiter` when `runner is not None and config.arbitrate`; `run_swarm` passes its `runner` through. Task 5 relies on the three new `SheetAnalysis` fields.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py` (the file already imports `register`, `analyze_workbook`, `analyze_sheet`, `build_analyzers`, `SwarmConfig`, `LayoutCandidate` usage patterns, `_GridSource`, `_SHEETS`; add `FakeAgentRunner` from `mcg_swarm.subagent.agent_runner` and `handle_from_region` where the snippets use them):

```python
# --- B2b: runner/arbiter threading -----------------------------------------

_STACKED = [("Region", "Sales"), ("North", 10), ("South", 20),
            (None, None),
            ("Dept", "Cost"), ("Eng", 100), ("Ops", 50)]


def _register_disagreeing_lenses(monkeypatch):
    """vertical (A1:B3) vs a whole-block lens (A1:B7); patched scores make the
    disagreement genuine (top does not dominate: more coverage but a gap)."""
    from mcg_swarm.splitter import handle_from_region

    class _BigLens:
        name = "biglens"
        def analyze(self, grid, sheet, source=None):
            h = handle_from_region(grid, sheet, "A1:B7", 1)
            return [LayoutCandidate(method="biglens", handles=(h,), coverage=1.0)]
    register("biglens", _BigLens)

    def fake(source, grid, handles, sheet):
        regions = frozenset(h.region for h in handles)
        return {frozenset({"A1:B7"}): (12, 0, 1),
                frozenset({"A1:B3"}): (11, 0, 0)}[regions]
    monkeypatch.setattr("mcg_swarm.subagent.structural.score_handles", fake)


def test_analyze_workbook_agreement_skips_arbiter():
    """Spec exit criterion: when lenses agree, dedup collapses them and the
    arbiter is never consulted."""
    class _AgreeLens:
        name = "agreelens"
        def analyze(self, grid, sheet, source=None):
            from mcg_swarm.splitter import detect_table
            return [LayoutCandidate(method="agreelens",
                                    handles=(detect_table(grid, sheet),),
                                    confidence=0.9)]
    register("agreelens", _AgreeLens)
    runner = FakeAgentRunner(actions=[], final={"choice": 0})
    vertical = {"S": [("Region", "Sales"), ("North", 10)]}
    out = analyze_workbook(_GridSource(vertical),
                           config=SwarmConfig(analyzers=("vertical", "agreelens")),
                           runner=runner)
    assert out[0].method == "vertical"       # higher-confidence label survives dedup
    assert out[0].contested is False
    assert runner.calls == 0


def test_analyze_workbook_disagreement_invokes_arbiter(monkeypatch):
    _register_disagreeing_lenses(monkeypatch)
    runner = FakeAgentRunner(actions=[], final={"choice": 1, "rationale": "r"})
    out = analyze_workbook(_GridSource({"S": _STACKED}),
                           config=SwarmConfig(analyzers=("vertical", "biglens")),
                           runner=runner)
    sa = out[0]
    assert runner.calls == 1
    assert sa.contested is True
    assert sa.method == "vertical"           # arbiter picked index 1 (runner-up)
    assert any(f.category == "arbiter-choice" for f in sa.findings)


def test_arbitrate_config_gate(monkeypatch):
    _register_disagreeing_lenses(monkeypatch)
    runner = FakeAgentRunner(actions=[], final={"choice": 1})
    out = analyze_workbook(_GridSource({"S": _STACKED}),
                           config=SwarmConfig(analyzers=("vertical", "biglens"),
                                              arbitrate=False),
                           runner=runner)
    assert runner.calls == 0
    assert out[0].method == "biglens"        # deterministic top stands


def test_sheet_analysis_carries_baseline(monkeypatch):
    _register_disagreeing_lenses(monkeypatch)
    out = analyze_workbook(_GridSource({"S": _STACKED}),
                           config=SwarmConfig(analyzers=("vertical", "biglens")))
    sa = out[0]
    assert sa.contested is True
    assert sa.method == "biglens"            # no runner -> deterministic top
    assert [h.region for h in sa.baseline_handles] == ["A1:B3"]
    assert sa.baseline_view is None


def test_no_runner_disagreement_unchanged(monkeypatch):
    """Graceful degradation: without a runner the deterministic top wins,
    exactly as before this task."""
    _register_disagreeing_lenses(monkeypatch)
    sa = analyze_sheet(build_analyzers(("vertical", "biglens")), _STACKED, "S",
                       source=_GridSource({"S": _STACKED}))
    assert sa.method == "biglens" and sa.contested is True
```

(Note on the disagreement fixture: `biglens` (12, 0, 1) vs vertical (11, 0, 0) — neither dominates (biglens has more coverage but a gap), the floor passes for biglens (coverage 12 ≥ 11, errors 0 ≤ 0), so the deterministic top is `biglens` and an arbiter choosing index 1 flips to vertical.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -k "arbiter or arbitrate or baseline or no_runner" -v`
Expected: FAIL — `TypeError: analyze_workbook() got an unexpected keyword argument 'runner'` (and `SwarmConfig` has no `arbitrate` field).

- [ ] **Step 3: Implement**

(a) `mcg_swarm/config.py` — add to the dataclass (after `analyzers`) and one docstring line:

```python
    arbitrate: bool = True
```

docstring addition: `arbitrate:         let an injected runner arbitrate genuine lens disagreement (Stage 2). No-op without a runner or without competing lenses.`

(b) `mcg_swarm/analyzers/base.py` — append three fields to `SheetAnalysis` (after `findings`), and document them in the class docstring:

```python
    contested: bool = False
    baseline_handles: tuple[TableHandle, ...] = ()
    baseline_view: Any = None
```

docstring additions: `contested: the winner emerged from genuine lens disagreement (run_swarm live-re-validates it against the baseline before commitment).` / `baseline_handles/baseline_view: the vertical-lens candidate's interpretation, when one was present.`

(c) `mcg_swarm/analyzers/pipeline.py` — change the assess import to:

```python
from mcg_swarm.analyzers.assess import assess_sheet_full
```

and replace `analyze_sheet` and `analyze_workbook` with:

```python
def analyze_sheet(analyzers, grid: list[tuple], sheet: str, source=None,
                  arbiter=None) -> SheetAnalysis:
    candidates: list[LayoutCandidate] = []
    findings: list[Finding] = []
    for a in analyzers:
        try:
            candidates.extend(a.analyze(grid, sheet, source=source))
        except Exception as e:  # lens failure is a finding, never a crash (spec §5)
            findings.append(Finding(
                category="analyzer-error", severity="warning", scope="sheet",
                message=f"analyzer {a.name!r} failed: {e}", source="static",
                ref=f"{sheet}!A1"))
    assessment = None
    if candidates:
        try:
            assessment = assess_sheet_full(candidates, source=source, grid=grid,
                                           sheet=sheet, arbiter=arbiter)
        except Exception as e:  # malformed candidate from a lens — degrade, don't crash
            findings.append(Finding(
                category="analyzer-error", severity="warning", scope="sheet",
                message=f"assessment failed: {e}", source="static",
                ref=f"{sheet}!A1"))
    if assessment is None:
        winner = _fallback_candidate(sheet)
        return SheetAnalysis(sheet=sheet, handles=winner.handles, view=winner.view,
                             method=winner.method,
                             findings=tuple(findings) + winner.findings)
    winner, baseline = assessment.winner, assessment.baseline
    return SheetAnalysis(
        sheet=sheet, handles=winner.handles, view=winner.view, method=winner.method,
        findings=tuple(findings) + winner.findings + assessment.findings,
        contested=assessment.contested,
        baseline_handles=baseline.handles if baseline is not None else (),
        baseline_view=baseline.view if baseline is not None else None)


def analyze_workbook(source, config: SwarmConfig | None = None,
                     runner=None) -> list[SheetAnalysis]:
    """Run the active analyzer lenses over every sheet. The rich counterpart of
    split_workbook — surfaces view/method/findings per sheet (spec §4.6).
    `runner` (an AgentRunner) enables the Stage-2 arbiter on genuine lens
    disagreement; None or `config.arbitrate=False` keeps assessment fully
    deterministic."""
    if config is None:
        config = SwarmConfig()
    src = as_source(source)
    analyzers = build_analyzers(config.analyzers)
    arbiter = None
    if runner is not None and config.arbitrate:
        # Lazy: the arbiter reaches into the subagent toolset stack at call time.
        from mcg_swarm.analyzers.arbiter import LayoutArbiter
        arbiter = LayoutArbiter(runner)
    return [analyze_sheet(analyzers, src.read_region(name), name, source=src,
                          arbiter=arbiter)
            for name in src.sheet_names()]
```

(d) `mcg_swarm/runner.py` — in `run_swarm`, change the analyze call to:

```python
        sheet_analyses = analyze_workbook(source, config=config, runner=runner)
```

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -v`
Expected: PASS — including the pre-existing never-raise tests (`test_malformed_candidate_degrades_to_fallback` etc.: the guard structure is preserved, only the call inside changed).
Run: `.venv/bin/python -m pytest -q`
Expected: **371 passed, 1 skipped** (366 + 5), zero failures.

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/config.py mcg_swarm/analyzers/base.py mcg_swarm/analyzers/pipeline.py mcg_swarm/runner.py tests/test_pipeline.py
git commit -m "feat(pipeline): thread runner/arbiter through analysis; SheetAnalysis carries baseline + contested"
```

---

### Task 5: Stage-4 — live re-validation of contested winners in run_swarm

**Files:**
- Modify: `mcg_swarm/runner.py`
- Test: `tests/test_runner_stage4.py` (new, 3 tests)

**Interfaces:**
- Consumes: `SheetAnalysis.contested/baseline_handles/baseline_view` (Task 4), `_view_orientation` (Task 1), the existing `orchestrate_table` loop machinery, `_signature`-style interpretation identity.
- Produces: `_interpretation(handles, view) -> tuple` module helper in runner.py; a contested branch in `run_swarm`'s loop that live-A/Bs the winner against the vertical baseline and emits a `contested-layout` finding either way. The Layer-2 re-cut branch below it is untouched (contested sheets `continue` before reaching it, exactly like the multi-handle branch already does).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_runner_stage4.py`:

```python
"""Stage 4 (spec §4.5): run_swarm live-re-validates contested non-baseline
winners against the vertical baseline before commitment."""
import mcg_swarm.runner as runner_mod
from mcg_swarm.analyzers.base import LayoutCandidate
from mcg_swarm.analyzers.registry import register
from mcg_swarm.config import SwarmConfig
from mcg_swarm.runner import run_swarm
from mcg_swarm.splitter import TableHandle, handle_from_region
from tests.test_views import _GridSource

_STACKED = [("Region", "Sales"), ("North", 10), ("South", 20),
            (None, None),
            ("Dept", "Cost"), ("Eng", 100), ("Ops", 50)]

# Three-way fixture: vertical (A1:B3) is the baseline; pairlens (A1:B3+A5:B7)
# is the top; qlens (A5:B7) is a runner-up that keeps the top non-dominant
# (pair has more coverage but a fake gap). Floor passes for pair
# (coverage 12 >= 6, errors 0 <= 0), so the committed winner is contested
# AND different from the baseline -> the live A/B branch runs.
_SCORES = {
    frozenset({"A1:B3"}): (6, 0, 0),
    frozenset({"A1:B3", "A5:B7"}): (12, 0, 1),
    frozenset({"A5:B7"}): (11, 0, 0),
}


class _PairLens:
    name = "s4_pair"
    def analyze(self, grid, sheet, source=None):
        return [LayoutCandidate(method="s4_pair", handles=(
            handle_from_region(grid, sheet, "A1:B3", 1),
            handle_from_region(grid, sheet, "A5:B7", 5)), coverage=1.0)]


class _QLens:
    name = "s4_q"
    def analyze(self, grid, sheet, source=None):
        return [LayoutCandidate(method="s4_q", handles=(
            handle_from_region(grid, sheet, "A5:B7", 5),), coverage=0.5)]


register("s4_pair", _PairLens)
register("s4_q", _QLens)

_CFG = SwarmConfig(analyzers=("vertical", "s4_pair", "s4_q"))


def _patch_scores(monkeypatch):
    def fake(source, grid, handles, sheet):
        return _SCORES[frozenset(h.region for h in handles)]
    monkeypatch.setattr("mcg_swarm.subagent.structural.score_handles", fake)


def test_contested_winner_committed_when_live_clean(monkeypatch):
    _patch_scores(monkeypatch)
    ex = run_swarm(_GridSource({"S": _STACKED}), config=_CFG)
    assert len(ex.tables) == 2                       # pair committed
    assert sorted(t.region for t in ex.tables) == ["A1:B3", "A5:B7"]
    assert all(not t.errors for t in ex.tables)
    assert any(f.category == "contested-layout" and f.severity == "info"
               for f in ex.findings)


def test_contested_winner_rejected_on_live_errors(monkeypatch):
    """A winner that fails LIVE (even though its snapshot score won) is
    rejected: the vertical baseline is committed instead."""
    _patch_scores(monkeypatch)
    real = runner_mod.orchestrate_table

    def flaky(source, handle, **kwargs):
        if handle.region == "A5:B7":                 # only the winner's 2nd table
            bad = TableHandle(handle.sheet, handle.region, handle.header_row,
                              [], ambiguous=True, reason="live failure injection")
            return real(source, bad, **kwargs)
        return real(source, handle, **kwargs)

    monkeypatch.setattr(runner_mod, "orchestrate_table", flaky)
    ex = run_swarm(_GridSource({"S": _STACKED}), config=_CFG)
    assert [t.region for t in ex.tables] == ["A1:B3"]   # baseline kept
    assert not ex.tables[0].errors
    assert any(f.category == "contested-layout" and f.severity == "warning"
               for f in ex.findings)


def test_floor_kept_baseline_skips_live_ab(monkeypatch):
    """When the floor already restored the baseline, winner == baseline and
    no live A/B runs (no contested-layout finding, single normal table)."""
    def fake(source, grid, handles, sheet):
        return {frozenset({"A1:B3"}): (6, 0, 0),
                frozenset({"A1:B3", "A5:B7"}): (12, 1, 0),   # errors > baseline
                frozenset({"A5:B7"}): (11, 0, 3)}[frozenset(h.region for h in handles)]
    monkeypatch.setattr("mcg_swarm.subagent.structural.score_handles", fake)
    ex = run_swarm(_GridSource({"S": _STACKED}), config=_CFG)
    assert [t.region for t in ex.tables] == ["A1:B3"]
    assert not any(f.category == "contested-layout" for f in ex.findings)
    assert any(f.category == "assessor-floor" for f in ex.findings)
```

(Fixture arithmetic for the third test: pair (12, 1, 0) vs q (11, 0, 3) — neither dominates (pair has more errors, q has fewer of everything but coverage). Deterministic top is pair; the floor rejects it (errors 1 > baseline 0) and restores vertical. Winner == baseline → the Stage-4 branch must NOT fire.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_runner_stage4.py -v`
Expected: test 1 FAILS with 2-tables-expected vs whatever the pre-Stage-4 path commits — without the branch, run_swarm's multi-handle path commits the pair WITHOUT any `contested-layout` finding (the finding assertion fails); test 2 FAILS (the flaky winner is committed with errors instead of being rejected); test 3 may PASS already (floor landed in Task 2) — it is the regression pin for the branch's guard condition.

- [ ] **Step 3: Implement in `mcg_swarm/runner.py`**

(a) Add a module-level helper (below `_view_orientation`):

```python
def _interpretation(handles, view) -> tuple:
    """Layout identity for the Stage-4 winner-vs-baseline comparison — same
    notion as assess._signature: regions + header placement + view kind."""
    return (type(view).__name__ if view is not None else "",
            tuple(sorted((h.region, h.header_row, h.header_span)
                         for h in handles)))
```

(b) In `run_swarm`'s loop, insert the Stage-4 branch immediately AFTER the zero-handle guard (`if not sa.handles: continue`) and BEFORE the multi-handle branch (`if len(sa.handles) > 1:`):

```python
        if (sa.contested and sa.baseline_handles
                and _interpretation(sa.handles, sa.view)
                != _interpretation(sa.baseline_handles, sa.baseline_view)):
            # Stage 4 (spec §4.5): a contested non-baseline winner must prove
            # itself against the vertical baseline on the LIVE pipeline before
            # commitment — snapshot scores can miss live-only behavior (band
            # verifier, table validator). Mirrors the Layer-2 re-cut pattern.
            base_src = sa.baseline_view or source
            base_orient, base_vf = _view_orientation(sa.baseline_view, sa.sheet)
            if base_vf is not None:
                wb_findings.append(base_vf)

            def _run(src_, handles_, orient_):
                multi = len(handles_) > 1
                return [orchestrate_table(
                            src_, sh,
                            table_id=f"{sa.sheet}__{i}_{j}" if multi else f"{sa.sheet}__{i}",
                            llm=llm, subagent=subagent,
                            table_validator=table_validator,
                            detect_findings=[], orientation=orient_)
                        for j, sh in enumerate(handles_)]

            try:
                cand_tables = _run(sheet_src, sa.handles, orient)
                base_tables = _run(base_src, sa.baseline_handles, base_orient)
                cand_err = sum(len(t.errors) for t in cand_tables)
                base_err = sum(len(t.errors) for t in base_tables)
            except Exception:
                cand_tables, base_tables = None, None  # never break extraction

            if cand_tables is not None and cand_err <= base_err:
                tables.extend(cand_tables)
                wb_findings.append(Finding(
                    category="contested-layout", severity="info", scope="sheet",
                    source="static", ref=f"{sa.sheet}!A1",
                    message=(f"lens disagreement: committed {sa.method!r} "
                             f"(live errors {cand_err} vs baseline {base_err})")))
            else:
                if base_tables is None:  # the A/B itself failed → conservative
                    base_tables = _run(base_src, sa.baseline_handles, base_orient)
                tables.extend(base_tables)
                wb_findings.append(Finding(
                    category="contested-layout", severity="warning", scope="sheet",
                    source="static", ref=f"{sa.sheet}!A1",
                    message=(f"lens disagreement: {sa.method!r} raised live "
                             "errors; kept vertical baseline")))
            continue  # tables + findings committed for this sheet
```

(The final `_run` inside the `else` follows the existing re-cut precedent at runner.py:115 — the last-resort orchestration is unguarded there too. Contested sheets `continue` before the Layer-2 scan/review, matching the multi-handle branch's existing rationale: "multi-handle winners were already assessed at analyze time".)

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_runner_stage4.py tests/test_split_neutrality.py -v`
Expected: PASS — the neutrality suite proves the default path never enters the branch (`contested` is False for single-lens sheets).
Run: `.venv/bin/python -m pytest -q`
Expected: **374 passed, 1 skipped** (371 + 3), zero failures.

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/runner.py tests/test_runner_stage4.py
git commit -m "feat(runner): Stage-4 live re-validation of contested winners vs vertical baseline"
```

---

### Task 6: End-to-end assessor battery (spec §7 scenarios through run_swarm)

**Files:**
- Test: `tests/test_arbiter_e2e.py` (new, 3 tests)

**Interfaces:**
- Consumes: everything Tasks 1–5 produced; `run_swarm(source, runner=..., config=...)`, `build_indices`, `FakeAgentRunner`.
- Produces: the in-suite evidence for the spec's Phase-B exit criteria: the arbiter is demonstrably skipped when lenses agree, an injected runner arbitrates end-to-end without disturbing extraction correctness, and the default config never touches any of it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_arbiter_e2e.py`:

```python
"""E2E: the Stage-2 arbiter inside a full run_swarm pass (FakeAgentRunner).

config(validate=False, alter_boundaries=False) quiets the band verifier and
Layer-2 reviewer so the ONLY runner consumer on clean data is the arbiter."""
from mcg_swarm.analyzers.base import LayoutCandidate
from mcg_swarm.analyzers.registry import register
from mcg_swarm.config import SwarmConfig
from mcg_swarm.runner import build_indices, run_swarm
from mcg_swarm.splitter import detect_table, handle_from_region
from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from tests.test_views import _GridSource

_STACKED = [("Region", "Sales"), ("North", 10), ("South", 20),
            (None, None),
            ("Dept", "Cost"), ("Eng", 100), ("Ops", 50)]


class _E2EPairLens:
    name = "e2e_pair"
    def analyze(self, grid, sheet, source=None):
        return [LayoutCandidate(method="e2e_pair", handles=(
            handle_from_region(grid, sheet, "A1:B3", 1),
            handle_from_region(grid, sheet, "A5:B7", 5)), coverage=1.0)]


class _E2ECloneLens:
    name = "e2e_clone"
    def analyze(self, grid, sheet, source=None):
        return [LayoutCandidate(method="e2e_clone",
                                handles=(detect_table(grid, sheet),),
                                confidence=0.9)]


register("e2e_pair", _E2EPairLens)
register("e2e_clone", _E2ECloneLens)

_QUIET = dict(validate=False, alter_boundaries=False)


def _patch_scores(monkeypatch):
    def fake(source, grid, handles, sheet):
        return {frozenset({"A1:B3"}): (6, 0, 0),
                frozenset({"A1:B3", "A5:B7"}): (12, 0, 1),
                }[frozenset(h.region for h in handles)]
    monkeypatch.setattr("mcg_swarm.subagent.structural.score_handles", fake)


def test_run_swarm_arbiter_end_to_end(monkeypatch):
    """Disagreement -> arbiter consulted once -> its pick extracted with zero
    errors, indexed, and queryable: correctness stays provable."""
    _patch_scores(monkeypatch)
    runner = FakeAgentRunner(actions=[{"tool": "dimensions"}],
                             final={"choice": 0, "rationale": "two tables"})
    src = _GridSource({"S": _STACKED})
    ex = run_swarm(src, runner=runner,
                   config=SwarmConfig(analyzers=("vertical", "e2e_pair"), **_QUIET))
    assert runner.calls == 1
    assert sorted(t.region for t in ex.tables) == ["A1:B3", "A5:B7"]
    assert all(not t.errors for t in ex.tables)
    idx = build_indices(src, ex)
    top = next(t for t in ex.tables if t.region == "A1:B3")
    assert idx[top.table_id].query("North", "Sales").value == 10


def test_run_swarm_agreement_never_calls_runner():
    """Spec exit criterion: lenses agree -> dedup -> no arbiter call, and the
    result matches the default single-lens extraction."""
    runner = FakeAgentRunner(actions=[], final={"choice": 0})
    vertical = {"S": [("Region", "Sales"), ("North", 10), ("South", 20)]}
    ex = run_swarm(_GridSource(vertical), runner=runner,
                   config=SwarmConfig(analyzers=("vertical", "e2e_clone"), **_QUIET))
    base = run_swarm(_GridSource(vertical),
                     config=SwarmConfig(**_QUIET))
    assert runner.calls == 0
    assert [t.region for t in ex.tables] == [t.region for t in base.tables]
    assert [t.table_id for t in ex.tables] == [t.table_id for t in base.tables]


def test_default_config_untouched_by_b2b_machinery():
    """Byte-parity guard: default config, no runner -> no contested/arbiter/
    floor findings anywhere, orientation vertical."""
    vertical = {"S": [("Region", "Sales"), ("North", 10), ("South", 20)]}
    ex = run_swarm(_GridSource(vertical))
    t = ex.tables[0]
    assert t.orientation == "vertical" and not t.errors
    b2b_categories = {"contested-layout", "arbiter-choice", "arbiter-error",
                      "assessor-floor", "unknown-view"}
    assert not [f for f in ex.findings if f.category in b2b_categories]
```

- [ ] **Step 2: Run tests to verify state**

Run: `.venv/bin/python -m pytest tests/test_arbiter_e2e.py -v`
Expected: with Tasks 1–5 landed all three PASS on first run — that is the point (the machinery is complete). If any fails, the integration has a real bug: STOP and report the failure verbatim; do not weaken assertions.

- [ ] **Step 3: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: **377 passed, 1 skipped** (374 + 3), zero failures.

- [ ] **Step 4: Commit**

```bash
git add tests/test_arbiter_e2e.py
git commit -m "test(assessor): e2e battery — arbiter through run_swarm, agreement short-circuit, default parity"
```

---

## Controller exit gate (after Task 6, before merge)

Corpus byte-identity: Phase-A procedure (`neutrality_corpus.py`, branch HEAD vs `main` via worktree, default config) — **no diff** required. (Extraction takes 10+ minutes; run it as a background job.)

## Deferred (explicitly NOT in this plan)

- **StructuralReviewer subsumption → "recut" lens + retirement of run_swarm's re-cut branch.** Deliberately deferred: the reviewer's finding-annotation semantics (`fixed`/`rejected`/`open` partitioning in `_accept`) are pinned by the boundary-detection test suites and migrating them is its own plan-sized chunk. Until then, the re-cut branch and the Stage-4 branch coexist without overlap (re-cut fires on uncontested single-vertical winners with uncovered-data; Stage 4 fires on contested winners, which `continue` before Layer-2).
- **#8 multi-handle `scan_handle` asymmetry** — tangled with the subsumption; goes with it.
- **`build_indices` view-kind dispatch generalization** (currently `orientation == "transposed"` → plain `TransposedView`; fine while that is the only view kind — Task 1's unknown-view warning guards the seam).

## Plan B2c preview — the pure-agentic analyzer lens (written after B2b merges)

B2b is what makes B2c safe; B2c is the user's stated priority ("making it work has the highest priority"). Settled design (from the 2026-07-02 discussion):
- `AgenticLayoutLens` is **just another lens** (`analyze(grid, sheet, source)` → `LayoutCandidate`s) thanks to B2a's lens-source protocol; registered (e.g. `"agentic"`), opt-in via `SwarmConfig.analyzers`. Needs runner-aware lens construction (a `build_analyzers(names, runner=...)` extension — the one registry change B2c owns).
- The agent proposes **structure, never values** (regions/header/orientation per sheet): deterministic re-extraction + the quality gate make its output provable, so a hallucination is caught, not ingested.
- Agent harness: whole-workbook read tools + the excel skill + subagent spawning via the SDK adapter, plus a bash tool sandboxed to one scratch folder (write/edit/delete only there); the **quality gate exposed as a sandbox tool** so the agent iterates to green before finalizing; probe/transform scripts persisted as **replayable artifacts**; **policy caps** (max subagents, wall-clock, iteration limit) even with cost-no-object.
- `MaterializedView` escape hatch for cell-surgery sheets (cleaned grid + provenance map; loses live-read, spot-checkable).
- Its candidates flow through B2b's machinery unchanged: Stage-0 dedup gives the "agreed by both approaches" badge, the arbiter adjudicates disagreement with static lenses, the floor + Stage-4 guarantee it can never make extraction worse. The arbiter slot is also where design-round-2's human-in-the-loop picking plugs in.

## Self-Review

**1. Spec coverage (§4.5 Stages 2-4, §7 scenarios):** Stage 2 → Tasks 3+4 (pick-one-of-K, runner-gated, config-gated); Stage 3 → Task 2 (floor vs vertical baseline, ≥ coverage ∧ ≤ errors exactly as spec §4.5); Stage 4 → Task 5 (live A/B mirroring the re-cut pattern). §7 scenarios: (a) single-candidate passthrough → Task 2 test 1; (b) dominant → short-circuit, no agent → Task 2 test 2 + Task 4 agreement test; (c) genuine disagreement → arbiter invoked → Task 4 + Task 6; (d) agent picks worse → floor keeps baseline → Task 2 test 5; (e) live re-validation rejects snapshot-only winner → Task 5 test 2. Subsumption + #8 explicitly deferred with rationale. ✓
**2. Placeholder scan:** none — every step carries complete code; the two prose-directed edits (config field, SheetAnalysis fields) show the exact lines. ✓
**3. Type consistency:** `assess_sheet_full(candidates, *, source, grid, sheet, arbiter=None) -> Assessment` uniform across assess.py/pipeline.py/tests; `choose(ranked_topk, *, source, sheet) -> int` uniform across arbiter.py/assess.py/test stubs; `SheetAnalysis` new fields keyword-constructed at the single construction site; `_view_orientation(view, sheet)` shared by Task 1's loop edit and Task 5's baseline branch; suite arithmetic 353 → +4 → +6 → +3 → +5 → +3 → +3 = **377**. ✓
