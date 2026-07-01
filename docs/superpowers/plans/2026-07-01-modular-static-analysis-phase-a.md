# Modular Static Analysis — Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a pluggable sheet-analyzer abstraction (protocol, registry, config, deterministic assessor) and refactor today's `detect_table`/`split_workbook` into the first analyzer — with **zero behavior change** when `analyzers=("vertical",)`.

**Architecture:** A new `mcg_swarm/analyzers/` package defines a `SheetAnalyzer` protocol whose implementations turn a sheet grid into `LayoutCandidate`s. A registry maps names → analyzer factories; `SwarmConfig.analyzers` selects the active set (default `("vertical",)`). A deterministic `assess()` picks the best candidate (Stage 0 dedup + Stage 1 score). `split_workbook` is rewritten to run the active analyzers per sheet and assess — but keeps its exact `list[TableHandle]` return type so all existing callers and downstream orchestration are untouched. `VerticalSplitAnalyzer` wraps the unchanged `detect_table`, so a single-candidate assessment returns the identical handle.

**Tech Stack:** Python 3, `dataclasses`, `typing.Protocol`, openpyxl (only transitively, via the existing source layer), pytest.

## Global Constraints

- **Neutrality is the exit criterion.** With `analyzers=("vertical",)` (the default), `split_workbook` output and the full `WorkbookExtraction` must be identical to `main`. The refactor is neutral *by construction*: `VerticalSplitAnalyzer` calls the unchanged `detect_table`, and single-candidate assessment returns that same handle object.
- **`split_workbook(source, config=None) -> list[TableHandle]`** — signature and return type are frozen for Phase A. ~15 test files call `split_workbook(p)[0]`. Do **not** change the return to pairs/views; that is Phase B.
- **The band layer, orchestrator, index, and quality gate are not touched** in Phase A.
- **No new runtime dependencies.** Deterministic only — no runner, no LLM, no agentic arbiter in Phase A (that is Phase B).
- **`SwarmConfig` stays provider-agnostic** — it names analyzers by string id only (`config.py` docstring rule).
- **Static never raises** (`DATA-REQUIREMENTS.md §4`) — analyzers return `[]` on internal failure rather than throwing; `VerticalSplitAnalyzer` always returns exactly one candidate because `detect_table` always returns a handle (even an ambiguous stub).
- Spec: `docs/superpowers/specs/2026-07-01-modular-static-analysis-design.md` (this plan implements **Phase A** only).

### Deliberate scope deferrals (documented divergences from the spec)
- The spec's `LayoutCandidate.view: SourceView | None` field and `SheetView` input type are **Phase B**. Phase A uses the raw `grid: list[tuple]` + `sheet: str` as the analyzer input (exactly what `detect_table` consumes) and omits `view` entirely. No normalization/transpose in Phase A.
- Assessor Stages 2–4 (agentic arbiter, verify-before-accept, live re-validation) are **Phase B**. Phase A ships Stage 0 (dedup) + Stage 1 (deterministic score/pick) only.
- The Layer-2 `StructuralReviewer` is **not** subsumed in Phase A — it keeps running exactly as today via `runner.py`. Subsumption is Phase B.

---

## File Structure

**Create:**
- `mcg_swarm/analyzers/__init__.py` — package exports (`SheetAnalyzer`, `LayoutCandidate`, `build_analyzers`, `assess`, `register`).
- `mcg_swarm/analyzers/base.py` — `LayoutCandidate` dataclass + `SheetAnalyzer` Protocol.
- `mcg_swarm/analyzers/vertical.py` — `VerticalSplitAnalyzer` (wraps `detect_table`).
- `mcg_swarm/analyzers/registry.py` — name→factory registry + `build_analyzers`.
- `mcg_swarm/analyzers/assess.py` — `assess()` (Stage 0 dedup + Stage 1 score).
- `tests/test_analyzers.py` — unit tests for base/vertical/registry/assess.
- `tests/test_split_neutrality.py` — end-to-end neutrality tests for the rewritten `split_workbook`.

**Modify:**
- `mcg_swarm/config.py` — add `analyzers: tuple[str, ...] = ("vertical",)`.
- `mcg_swarm/splitter.py` — rewrite `split_workbook` to use analyzers + assess (lazy imports to avoid the import cycle). `detect_table`, `TableHandle`, and helpers stay put and unchanged.
- `mcg_swarm/runner.py:26` — pass `config` into `split_workbook`.

**Import-cycle note:** `base.py`/`vertical.py` import `TableHandle`/`detect_table` from `splitter`, and `vertical.py` imports `coverage_score` from `coverage` (which imports `splitter`). To avoid a cycle, `splitter.split_workbook` imports `registry`/`assess` **lazily inside the function body**, never at module top. Load order `analyzers.vertical → splitter → coverage → splitter(already loaded)` has no cycle.

---

### Task 1: Analyzer base types (`LayoutCandidate` + `SheetAnalyzer` protocol)

**Files:**
- Create: `mcg_swarm/analyzers/__init__.py`
- Create: `mcg_swarm/analyzers/base.py`
- Test: `tests/test_analyzers.py`

**Interfaces:**
- Consumes: `TableHandle` (`mcg_swarm/splitter.py:10`), `Finding` (`mcg_swarm/schemas.py:25`).
- Produces:
  - `LayoutCandidate(method: str, handles: tuple[TableHandle, ...], coverage: int = 0, findings: tuple[Finding, ...] = (), confidence: float = 1.0)` — frozen dataclass.
  - `SheetAnalyzer` Protocol with attribute `name: str` and method `analyze(self, grid: list[tuple], sheet: str) -> list[LayoutCandidate]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_analyzers.py`:

```python
from mcg_swarm.analyzers.base import LayoutCandidate, SheetAnalyzer
from mcg_swarm.splitter import TableHandle


def test_layout_candidate_defaults():
    h = TableHandle("Sheet1", "A1:B3", 1)
    c = LayoutCandidate(method="vertical", handles=(h,))
    assert c.method == "vertical"
    assert c.handles == (h,)
    assert c.coverage == 0
    assert c.findings == ()
    assert c.confidence == 1.0


def test_layout_candidate_is_frozen():
    import dataclasses
    c = LayoutCandidate(method="x", handles=())
    try:
        c.method = "y"
        assert False, "expected FrozenInstanceError"
    except dataclasses.FrozenInstanceError:
        pass


class _Dummy:
    name = "dummy"
    def analyze(self, grid, sheet):
        return []


def test_sheet_analyzer_protocol_runtime_check():
    assert isinstance(_Dummy(), SheetAnalyzer)
    assert not isinstance(object(), SheetAnalyzer)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_analyzers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcg_swarm.analyzers'`.

- [ ] **Step 3: Create the package init**

Create `mcg_swarm/analyzers/__init__.py`:

```python
"""Pluggable sheet-level structural analysis.

Each SheetAnalyzer is a *lens* over a sheet grid, emitting LayoutCandidate(s).
A registry selects the active lenses (SwarmConfig.analyzers); assess() picks the
winning candidate. See docs/superpowers/specs/2026-07-01-modular-static-analysis-design.md.
"""
from mcg_swarm.analyzers.base import LayoutCandidate, SheetAnalyzer

__all__ = ["LayoutCandidate", "SheetAnalyzer"]
```

- [ ] **Step 4: Write the base types**

Create `mcg_swarm/analyzers/base.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from mcg_swarm.schemas import Finding
from mcg_swarm.splitter import TableHandle


@dataclass(frozen=True)
class LayoutCandidate:
    """One analyzer's interpretation of a whole sheet.

    handles:    one or more tables (Phase A: exactly one, from detect_table).
    coverage:   non-empty cells claimed by the handles (deterministic score input).
    findings:   excluded regions / warnings (Phase A: empty).
    confidence: analyzer self-report; advisory tie-breaker for the assessor.
    """

    method: str
    handles: tuple[TableHandle, ...]
    coverage: int = 0
    findings: tuple[Finding, ...] = ()
    confidence: float = 1.0


@runtime_checkable
class SheetAnalyzer(Protocol):
    name: str

    def analyze(self, grid: list[tuple], sheet: str) -> list[LayoutCandidate]:
        ...
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_analyzers.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add mcg_swarm/analyzers/__init__.py mcg_swarm/analyzers/base.py tests/test_analyzers.py
git commit -m "feat(analyzers): SheetAnalyzer protocol + LayoutCandidate type"
```

---

### Task 2: `VerticalSplitAnalyzer` (wraps the unchanged `detect_table`)

**Files:**
- Create: `mcg_swarm/analyzers/vertical.py`
- Test: `tests/test_analyzers.py` (append)

**Interfaces:**
- Consumes: `detect_table` (`mcg_swarm/splitter.py:150`), `coverage_score` (`mcg_swarm/coverage.py:35`), `LayoutCandidate` (Task 1).
- Produces: `VerticalSplitAnalyzer` with `name = "vertical"` and `analyze(grid, sheet) -> [LayoutCandidate]` returning exactly one candidate whose single handle equals `detect_table(grid, sheet)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_analyzers.py`:

```python
from mcg_swarm.analyzers.vertical import VerticalSplitAnalyzer
from mcg_swarm.splitter import detect_table
from mcg_swarm.coverage import coverage_score

_GRID = [("Region", "Sales"), ("North", 10), ("South", 20)]


def test_vertical_analyzer_wraps_detect_table():
    a = VerticalSplitAnalyzer()
    cands = a.analyze(_GRID, "Sheet1")
    assert len(cands) == 1
    c = cands[0]
    assert c.method == "vertical"
    assert len(c.handles) == 1
    assert c.handles[0] == detect_table(_GRID, "Sheet1")


def test_vertical_analyzer_sets_coverage():
    a = VerticalSplitAnalyzer()
    c = a.analyze(_GRID, "Sheet1")[0]
    assert c.coverage == coverage_score(_GRID, [c.handles[0].region])
    assert c.coverage > 0


def test_vertical_analyzer_name_attr():
    assert VerticalSplitAnalyzer().name == "vertical"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_analyzers.py -k vertical -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcg_swarm.analyzers.vertical'`.

- [ ] **Step 3: Write the analyzer**

Create `mcg_swarm/analyzers/vertical.py`:

```python
from __future__ import annotations

from mcg_swarm.analyzers.base import LayoutCandidate
from mcg_swarm.coverage import coverage_score
from mcg_swarm.splitter import detect_table


class VerticalSplitAnalyzer:
    """The baseline lens: one clean vertical table per sheet.

    Wraps the unchanged detect_table so a single-candidate assessment reproduces
    today's behavior byte-for-byte. This is the neutrality anchor for Phase A.
    """

    name = "vertical"

    def analyze(self, grid: list[tuple], sheet: str) -> list[LayoutCandidate]:
        handle = detect_table(grid, sheet)
        coverage = coverage_score(grid, [handle.region])
        return [LayoutCandidate(method="vertical", handles=(handle,), coverage=coverage)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_analyzers.py -k vertical -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/analyzers/vertical.py tests/test_analyzers.py
git commit -m "feat(analyzers): VerticalSplitAnalyzer wrapping detect_table"
```

---

### Task 3: Registry + `SwarmConfig.analyzers`

**Files:**
- Create: `mcg_swarm/analyzers/registry.py`
- Modify: `mcg_swarm/config.py`
- Test: `tests/test_analyzers.py` (append)

**Interfaces:**
- Consumes: `SheetAnalyzer` (Task 1), `VerticalSplitAnalyzer` (Task 2).
- Produces:
  - `register(name: str, factory: Callable[[], SheetAnalyzer]) -> None`
  - `build_analyzers(names: tuple[str, ...]) -> list[SheetAnalyzer]` — raises `KeyError` on an unknown name.
  - `"vertical"` registered at import → `VerticalSplitAnalyzer`.
  - `SwarmConfig.analyzers: tuple[str, ...] = ("vertical",)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_analyzers.py`:

```python
import pytest
from mcg_swarm.analyzers.registry import register, build_analyzers
from mcg_swarm.config import SwarmConfig


def test_build_default_analyzer_set():
    analyzers = build_analyzers(("vertical",))
    assert len(analyzers) == 1
    assert analyzers[0].name == "vertical"


def test_build_analyzers_unknown_name_raises():
    with pytest.raises(KeyError):
        build_analyzers(("does_not_exist",))


def test_register_and_build_custom():
    class _Fake:
        name = "fake"
        def analyze(self, grid, sheet):
            return []
    register("fake", _Fake)
    built = build_analyzers(("vertical", "fake"))
    assert [a.name for a in built] == ["vertical", "fake"]


def test_swarmconfig_has_default_analyzers():
    assert SwarmConfig().analyzers == ("vertical",)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_analyzers.py -k "analyzer_set or unknown_name or register_and_build or default_analyzers" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcg_swarm.analyzers.registry'` (and `AttributeError` for `analyzers`).

- [ ] **Step 3: Write the registry**

Create `mcg_swarm/analyzers/registry.py`:

```python
from __future__ import annotations

from typing import Callable

from mcg_swarm.analyzers.base import SheetAnalyzer
from mcg_swarm.analyzers.vertical import VerticalSplitAnalyzer

_REGISTRY: dict[str, Callable[[], SheetAnalyzer]] = {}


def register(name: str, factory: Callable[[], SheetAnalyzer]) -> None:
    """Register an analyzer factory under a stable string id."""
    _REGISTRY[name] = factory


def build_analyzers(names: tuple[str, ...]) -> list[SheetAnalyzer]:
    """Instantiate the named analyzers in order. Raises KeyError on unknown name."""
    built = []
    for name in names:
        if name not in _REGISTRY:
            raise KeyError(
                f"unknown analyzer {name!r} (registered: {sorted(_REGISTRY)})"
            )
        built.append(_REGISTRY[name]())
    return built


register("vertical", VerticalSplitAnalyzer)
```

- [ ] **Step 4: Add the config field**

In `mcg_swarm/config.py`, add the field to `SwarmConfig` (after `alter_boundaries` at line 22) and document it in the docstring:

```python
    validate: bool = True
    repair_max_passes: int = 3
    alter_boundaries: bool = True
    analyzers: tuple[str, ...] = ("vertical",)
```

Also add to the class docstring (after the `repair_max_passes` line):

```
    analyzers:         active sheet-analyzer lenses, by registry id. Default ("vertical",)
                       reproduces the pre-modular behavior exactly.
```

- [ ] **Step 5: Export from the package**

Update `mcg_swarm/analyzers/__init__.py` to re-export the registry helpers:

```python
"""Pluggable sheet-level structural analysis.

Each SheetAnalyzer is a *lens* over a sheet grid, emitting LayoutCandidate(s).
A registry selects the active lenses (SwarmConfig.analyzers); assess() picks the
winning candidate. See docs/superpowers/specs/2026-07-01-modular-static-analysis-design.md.
"""
from mcg_swarm.analyzers.base import LayoutCandidate, SheetAnalyzer
from mcg_swarm.analyzers.registry import build_analyzers, register

__all__ = ["LayoutCandidate", "SheetAnalyzer", "build_analyzers", "register"]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_analyzers.py -v`
Expected: PASS (all analyzer tests so far).

- [ ] **Step 7: Commit**

```bash
git add mcg_swarm/analyzers/registry.py mcg_swarm/analyzers/__init__.py mcg_swarm/config.py tests/test_analyzers.py
git commit -m "feat(analyzers): registry + SwarmConfig.analyzers knob"
```

---

### Task 4: Deterministic assessor (`assess`)

**Files:**
- Create: `mcg_swarm/analyzers/assess.py`
- Test: `tests/test_analyzers.py` (append)

**Interfaces:**
- Consumes: `LayoutCandidate` (Task 1), `TableHandle` (`splitter.py:10`).
- Produces: `assess(candidates: list[LayoutCandidate]) -> LayoutCandidate`.
  - Empty input → raises `ValueError`.
  - Stage 0: dedup by region signature (`tuple(sorted(h.region for h in handles))`), keeping the highest-confidence candidate per signature.
  - Stage 1: return the candidate maximizing `(coverage, confidence)`.
  - Single candidate → returned unchanged (passthrough — the neutrality anchor).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_analyzers.py`:

```python
from mcg_swarm.analyzers.assess import assess


def _cand(method, region, coverage, confidence=1.0):
    h = TableHandle("S", region, 1)
    return LayoutCandidate(method=method, handles=(h,),
                           coverage=coverage, confidence=confidence)


def test_assess_single_candidate_passthrough():
    c = _cand("vertical", "A1:B3", 6)
    assert assess([c]) is c  # same object — byte-identical downstream


def test_assess_empty_raises():
    with pytest.raises(ValueError):
        assess([])


def test_assess_picks_higher_coverage():
    lo = _cand("vertical", "A1:B3", 6)
    hi = _cand("multitable", "A1:C9", 20)
    assert assess([lo, hi]) is hi


def test_assess_dedups_same_region_by_confidence():
    weak = _cand("a", "A1:B3", 6, confidence=0.4)
    strong = _cand("b", "A1:B3", 6, confidence=0.9)
    # identical region signature → collapse to the higher-confidence one
    assert assess([weak, strong]) is strong


def test_assess_coverage_beats_confidence():
    # coverage is the primary key; a lower-confidence but higher-coverage wins
    big = _cand("a", "A1:C9", 20, confidence=0.5)
    small = _cand("b", "A1:B3", 6, confidence=1.0)
    assert assess([big, small]) is big
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_analyzers.py -k assess -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcg_swarm.analyzers.assess'`.

- [ ] **Step 3: Write the assessor**

Create `mcg_swarm/analyzers/assess.py`:

```python
from __future__ import annotations

from mcg_swarm.analyzers.base import LayoutCandidate


def _signature(candidate: LayoutCandidate) -> tuple[str, ...]:
    """Region-set identity of a candidate — two candidates that claim the same
    regions are considered the same interpretation."""
    return tuple(sorted(h.region for h in candidate.handles))


def assess(candidates: list[LayoutCandidate]) -> LayoutCandidate:
    """Pick the winning candidate for a sheet.

    Phase A: deterministic only.
      Stage 0 — dedup by region signature, keeping the highest-confidence per signature.
      Stage 1 — return the candidate maximizing (coverage, confidence).
    A single candidate is returned unchanged (the Phase-A neutrality anchor).
    """
    if not candidates:
        raise ValueError("assess requires at least one candidate")

    # Stage 0: dedup — collapse identical region signatures.
    best_by_sig: dict[tuple[str, ...], LayoutCandidate] = {}
    for c in candidates:
        sig = _signature(c)
        cur = best_by_sig.get(sig)
        if cur is None or c.confidence > cur.confidence:
            best_by_sig[sig] = c

    # Stage 1: rank by coverage, then confidence.
    return max(best_by_sig.values(), key=lambda c: (c.coverage, c.confidence))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_analyzers.py -k assess -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Export `assess` from the package**

Update `mcg_swarm/analyzers/__init__.py` imports/exports:

```python
from mcg_swarm.analyzers.assess import assess
from mcg_swarm.analyzers.base import LayoutCandidate, SheetAnalyzer
from mcg_swarm.analyzers.registry import build_analyzers, register

__all__ = ["LayoutCandidate", "SheetAnalyzer", "build_analyzers", "register", "assess"]
```

(Keep the module docstring at the top of the file.)

- [ ] **Step 6: Commit**

```bash
git add mcg_swarm/analyzers/assess.py mcg_swarm/analyzers/__init__.py tests/test_analyzers.py
git commit -m "feat(analyzers): deterministic assessor (dedup + score/pick)"
```

---

### Task 5: Rewrite `split_workbook` through the ensemble + wire `run_swarm` + neutrality gate

**Files:**
- Modify: `mcg_swarm/splitter.py:262-271` (`split_workbook`)
- Modify: `mcg_swarm/runner.py:26`
- Test: `tests/test_split_neutrality.py`

**Interfaces:**
- Consumes: `build_analyzers`, `assess` (Tasks 3–4), `SwarmConfig` (`config.py:11`), `as_source` (`source.py:95`).
- Produces: `split_workbook(source, config: SwarmConfig | None = None) -> list[TableHandle]` — same return type as before; flattens each sheet's winning candidate's `handles`.

- [ ] **Step 1: Write the failing neutrality test**

Create `tests/test_split_neutrality.py`:

```python
"""Phase A neutrality: the analyzer-based split_workbook reproduces the pre-refactor
per-sheet detect_table output exactly when analyzers=("vertical",)."""
from mcg_swarm.splitter import split_workbook, detect_table
from mcg_swarm.config import SwarmConfig


class _FakeSource:
    """Minimal in-memory WorkbookSource (satisfies the runtime_checkable Protocol)."""

    def __init__(self, sheets):
        self._sheets = sheets  # {sheet_name: list[tuple]}

    def sheet_names(self):
        return list(self._sheets)

    def read_region(self, sheet, min_row=None, min_col=None, max_row=None, max_col=None):
        return self._sheets[sheet]

    def read_cell(self, sheet, row, col):
        grid = self._sheets[sheet]
        r = grid[row - 1] if row - 1 < len(grid) else ()
        return r[col - 1] if col - 1 < len(r) else None

    def read_formula_region(self, sheet, min_row=None, min_col=None, max_row=None, max_col=None):
        return self._sheets[sheet]


_SHEETS = {
    "Sales": [("Region", "Sales"), ("North", 10), ("South", 20)],
    "Costs": [("Dept", "Cost"), ("Eng", 100), ("Ops", 50)],
}


def test_split_workbook_matches_detect_table_per_sheet():
    src = _FakeSource(_SHEETS)
    expected = [detect_table(grid, name) for name, grid in _SHEETS.items()]
    assert split_workbook(src, config=SwarmConfig()) == expected


def test_split_workbook_default_config_is_neutral():
    src = _FakeSource(_SHEETS)
    # No config arg → default SwarmConfig() → analyzers=("vertical",)
    assert split_workbook(src) == [detect_table(g, n) for n, g in _SHEETS.items()]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_split_neutrality.py -v`
Expected: FAIL — `TypeError: split_workbook() got an unexpected keyword argument 'config'`.

- [ ] **Step 3: Rewrite `split_workbook`**

Replace `split_workbook` in `mcg_swarm/splitter.py` (lines 262-271) with:

```python
def split_workbook(source, config=None) -> list[TableHandle]:
    """Split a workbook into TableHandles via the active analyzer lenses.

    Accepts a path string, ``{"main": path}`` dict, or any ``WorkbookSource``.
    For each sheet, every analyzer in ``config.analyzers`` emits LayoutCandidate(s);
    ``assess`` picks the winner and its handles are flattened into the result.
    Default config → analyzers=("vertical",) → identical to the pre-modular behavior.
    """
    # Lazy imports break the splitter<->analyzers import cycle (analyzers import
    # detect_table/TableHandle from this module at their top level).
    from mcg_swarm.analyzers.registry import build_analyzers
    from mcg_swarm.analyzers.assess import assess
    from mcg_swarm.config import SwarmConfig

    if config is None:
        config = SwarmConfig()
    src = as_source(source)
    analyzers = build_analyzers(config.analyzers)

    handles: list[TableHandle] = []
    for name in src.sheet_names():
        grid = src.read_region(name)
        candidates = [c for a in analyzers for c in a.analyze(grid, name)]
        winner = assess(candidates)
        handles.extend(winner.handles)
    return handles
```

- [ ] **Step 4: Run the neutrality test to verify it passes**

Run: `pytest tests/test_split_neutrality.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Wire `config` through `run_swarm`**

In `mcg_swarm/runner.py`, change line 26 from:

```python
        handles = split_workbook(source)
```

to:

```python
        handles = split_workbook(source, config=config)
```

- [ ] **Step 6: Run the full existing test suite (the real neutrality gate)**

Run: `pytest -q`
Expected: PASS — every pre-existing test green, including all `split_workbook(p)[0]` callers, the structural/boundary suites, and `test_swarm_adapter`. Zero failures. If anything fails, the refactor is not neutral — fix before proceeding.

- [ ] **Step 7: Run the eval-corpus end-to-end equality check**

Create a throwaway script `/tmp/neutrality_corpus.py` (do not commit) and run it on both `main` and the branch, then diff:

```python
import glob, json
from mcg_swarm.runner import run_swarm

out = {}
for wb in sorted(glob.glob("eval/data/workbooks/*.xlsx")):
    ex = run_swarm(wb)  # default config, no runner → deterministic
    out[wb] = [
        {"sheet": t.sheet, "region": t.region, "header_row": t.header_row,
         "cols": [(c.name, c.dtype, c.role) for c in t.columns],
         "errors": list(t.errors)}
        for t in ex.tables
    ]
print(json.dumps(out, sort_keys=True, indent=2))
```

Run on the branch: `python /tmp/neutrality_corpus.py > /tmp/branch.json`
Run on main (in a worktree or after stashing): `python /tmp/neutrality_corpus.py > /tmp/main.json`
Then: `diff /tmp/main.json /tmp/branch.json`
Expected: **no diff** — the deterministic extraction over the full corpus is byte-identical.

- [ ] **Step 8: Commit**

```bash
git add mcg_swarm/splitter.py mcg_swarm/runner.py tests/test_split_neutrality.py
git commit -m "refactor(splitter): route split_workbook through analyzer ensemble (neutral)"
```

---

## Self-Review

**1. Spec coverage (Phase A subset):**
- SheetAnalyzer protocol → Task 1. ✓
- LayoutCandidate type → Task 1 (minus `view`, deferred to Phase B — documented). ✓
- Analyzer registry → Task 3. ✓
- `SwarmConfig.analyzers` → Task 3. ✓
- Refactor `detect_table`/`split_workbook` into `VerticalSplitAnalyzer`, zero behavior change → Tasks 2 + 5. ✓
- Deterministic assessor Stage 0/1 only → Task 4. ✓
- Exit criterion (full suite green + corpus byte-identical) → Task 5 Steps 6–7. ✓
- Explicitly deferred to Phase B (not gaps): normalization/`view`/`SheetView`, agentic arbiter, verify-before-accept, live re-validation, StructuralReviewer subsumption, new lenses. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases". Every code step shows complete code; every command shows expected output. ✓

**3. Type consistency:** `LayoutCandidate(method, handles, coverage, findings, confidence)` is used identically across Tasks 1/2/4. `assess(list[LayoutCandidate]) -> LayoutCandidate`, `build_analyzers(tuple[str,...]) -> list[SheetAnalyzer]`, `analyze(grid, sheet) -> list[LayoutCandidate]`, and `split_workbook(source, config=None) -> list[TableHandle]` match across all tasks and the neutrality test. ✓
