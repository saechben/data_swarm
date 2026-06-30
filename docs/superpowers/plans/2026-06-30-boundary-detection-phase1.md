# Boundary Detection — Phase 1 (Deterministic Detection) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guarantee that every case the static splitter gets wrong is **detected and surfaced as a first-class signal**, never silently corrupted — using a deterministic, model-free whole-grid scan. (Phase 2, agent boundary *alteration*, is a separate plan built on these interfaces.)

**Architecture:** Introduce a structured `Finding` record as the signalling source of truth (with `errors`/`provisional_notes` kept as derived views for backward compat). Add a deterministic coverage/residue scan over each sheet's full grid that emits `Finding`s for uncovered data (dropped/side-by-side tables), empty header corners (the `orchestration error: 'A'` cases), false header spans, and suspected transposition. Wire it into `run_swarm` so flags appear in output with no runner present.

**Tech Stack:** Python 3, Pydantic v2 (`_Base(BaseModel)`, `extra="forbid"`), `openpyxl.utils` (`range_boundaries`, `get_column_letter`), pytest.

## Global Constraints

- Test command: `.venv/bin/python -m pytest -q` (NOT bare `pytest`). Run from repo root `/Users/benjaminsaechew/Documents/Claude/Projects/data_swarm`.
- Branch: `feat/boundary-detection-repair` (spec committed there at `4c67a63`). Do not change branches.
- Baseline before Task 1: **251 passed, 1 skipped** (SDK installed) — confirm at start; invariant is zero failures/errors, deltas confined to touched files.
- **Never-raise preserved:** `run_swarm` / `orchestrate_table` must never raise. The scan is pure and wrapped; any failure degrades to "no extra findings," never a crash.
- **`Finding` is the source of truth.** When a model is constructed with non-empty `findings`, `errors` = messages of `severity=="error"` findings and `provisional_notes` = messages of `severity in {"warning","info"}` findings, derived by a `model_validator`. Legacy construction with `errors=`/`provisional_notes=` (no `findings`) must keep working unchanged.
- **Severity mapping:** `uncovered-data`, `empty-header-corner`, `false-header-span` → `severity="error"`; `transpose-suspected` → `severity="warning"`. Existing gate failures → `severity="error"`; static anomalies → `severity="info"`.
- Pydantic v2 idiom only (`Field(default_factory=...)`, `@model_validator(mode="after")`). No dataclasses for new schema models.

---

### Task 1: `Finding` model + derived `errors`/`provisional_notes`

**Files:**
- Modify: `mcg_swarm/schemas.py` (add `Finding`; add `findings` to `CanonicalTable` and `WorkbookExtraction`; add derivation validator)
- Test: `tests/test_finding_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Finding(category: str, severity: Literal["error","warning","info"], scope: Literal["workbook","sheet","table","column","cell"], message: str, source: Literal["static","gate","agent"], ref: str | None = None, agent_action: str | None = None, resolution: Literal["fixed","open","rejected"] = "open")`
  - `CanonicalTable.findings: list[Finding]` and `WorkbookExtraction.findings: list[Finding]`, each deriving `errors` (and `CanonicalTable.provisional_notes`) when non-empty.
  - `finding_from_gate_failure(msg: str) -> Finding` — maps a legacy gate failure string to a `Finding` (category by prefix, `severity="error"`, `source="gate"`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_finding_schema.py`:

```python
"""Finding record + derived errors/provisional_notes views."""
from mcg_swarm.schemas import (
    Finding, CanonicalTable, WorkbookExtraction, ExtractionRef,
    finding_from_gate_failure,
)


def _table(**kw):
    base = dict(table_id="t", sheet="S", region="A1:B2", header_row=1,
                extraction=ExtractionRef(script_name="idx_t"))
    base.update(kw)
    return CanonicalTable(**base)


def test_findings_derive_errors_and_notes():
    t = _table(findings=[
        Finding(category="dtype-mismatch", severity="error", scope="column",
                message="bad dtype", source="gate"),
        Finding(category="anomaly", severity="info", scope="table",
                message="heads up", source="static"),
    ])
    assert t.errors == ["bad dtype"]
    assert t.provisional_notes == ["heads up"]


def test_legacy_errors_still_work_without_findings():
    t = _table(errors=["legacy error"], provisional_notes=["legacy note"])
    assert t.errors == ["legacy error"]
    assert t.provisional_notes == ["legacy note"]
    assert t.findings == []


def test_workbook_findings_derive_errors():
    wb = WorkbookExtraction(
        workbook="w", generator_version="v",
        findings=[Finding(category="uncovered-data", severity="error",
                          scope="sheet", message="dropped table on S", source="static")],
    )
    assert wb.errors == ["dropped table on S"]


def test_finding_from_gate_failure_categorizes():
    f = finding_from_gate_failure("dtype-mismatch: column 'X' declared number ...")
    assert f.category == "dtype-mismatch"
    assert f.severity == "error" and f.source == "gate"
    f2 = finding_from_gate_failure("computed mismatch Sum@1: live=None calc=13")
    assert f2.category == "computed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_finding_schema.py -v`
Expected: FAIL — `ImportError: cannot import name 'Finding'`.

- [ ] **Step 3: Implement in `mcg_swarm/schemas.py`**

Add imports at the top (merge with existing): ensure `from typing import Any, Literal, Optional` and `from pydantic import BaseModel, ConfigDict, Field, model_validator`.

Add the `Finding` model and helper (place after `ColumnSpec`, before `CanonicalTable`):

```python
_GATE_PREFIXES = [
    ("coverage gap", "coverage-gap"),
    ("column-name", "column-name"),
    ("column-integrity", "column-integrity"),
    ("row-integrity", "row-integrity"),
    ("round-trip", "round-trip"),
    ("dtype-mismatch", "dtype-mismatch"),
    ("computed", "computed"),
]


class Finding(_Base):
    category: str
    severity: Literal["error", "warning", "info"]
    scope: Literal["workbook", "sheet", "table", "column", "cell"]
    message: str
    source: Literal["static", "gate", "agent"]
    ref: Optional[str] = None
    agent_action: Optional[str] = None
    resolution: Literal["fixed", "open", "rejected"] = "open"


def finding_from_gate_failure(msg: str) -> "Finding":
    """Map a legacy gate failure string to a Finding (category by prefix)."""
    category = "other"
    for prefix, cat in _GATE_PREFIXES:
        if msg.startswith(prefix):
            category = cat
            break
    return Finding(category=category, severity="error", scope="table",
                   message=msg, source="gate")
```

In `CanonicalTable`, add the field (after `errors`):

```python
    findings: list[Finding] = Field(default_factory=list)

    @model_validator(mode="after")
    def _derive_views(self):
        if self.findings:
            self.errors = [f.message for f in self.findings if f.severity == "error"]
            self.provisional_notes = [
                f.message for f in self.findings if f.severity in ("warning", "info")
            ]
        return self
```

In `WorkbookExtraction`, add:

```python
    findings: list[Finding] = Field(default_factory=list)

    @model_validator(mode="after")
    def _derive_errors(self):
        if self.findings:
            self.errors = [f.message for f in self.findings if f.severity == "error"]
        return self
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_finding_schema.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Full suite (no regressions)**

Run: `.venv/bin/python -m pytest -q`
Expected: 255 passed, 1 skipped (251 + 4 new). Zero failures.

- [ ] **Step 6: Commit**

```bash
git add mcg_swarm/schemas.py tests/test_finding_schema.py
git commit -m "feat(schema): Finding record + derived errors/provisional_notes views"
```

---

### Task 2: Deterministic coverage / residue detector

**Files:**
- Create: `mcg_swarm/coverage.py`
- Test: `tests/test_coverage.py`

**Interfaces:**
- Consumes: `Finding` (Task 1); `_is_header_candidate` from `mcg_swarm.splitter`; `TableHandle` (its `.region`, `.header_row`, `.header_span`, `.sheet`).
- Produces:
  - `nonempty_cells(grid: list[tuple]) -> set[tuple[int,int]]` — 1-based `(row, col)`.
  - `region_cells(region: str) -> set[tuple[int,int]]` — 1-based cells in an A1 range.
  - `coverage_score(grid, regions: list[str]) -> int` — count of non-empty cells inside any region.
  - `scan_handle(grid: list[tuple], handle, sheet: str) -> list[Finding]` — deterministic findings: `uncovered-data` (scope `sheet`), `empty-header-corner`, `false-header-span`, `transpose-suspected` (scope `table`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_coverage.py`:

```python
"""Deterministic coverage/residue detection over a full sheet grid."""
from mcg_swarm.coverage import nonempty_cells, region_cells, coverage_score, scan_handle
from mcg_swarm.splitter import TableHandle


def _cats(findings):
    return sorted(f.category for f in findings)


def test_nonempty_and_region_cells():
    grid = [("a", None), (None, "b")]
    assert nonempty_cells(grid) == {(1, 1), (2, 2)}
    assert region_cells("A1:B2") == {(1, 1), (1, 2), (2, 1), (2, 2)}


def test_coverage_score():
    grid = [("a", "b"), ("c", None)]
    assert coverage_score(grid, ["A1:A2"]) == 2  # a, c covered; b not


def test_uncovered_data_stacked_table():
    # First table A1:C2 captured; a second header+data block sits below at rows 4-5.
    grid = [
        ("Region", "Rev", "Units"),
        ("NA", 1, 2),
        (None, None, None),
        ("Product", "Price", "SKU"),
        ("Widget", 9, "W1"),
    ]
    h = TableHandle("S", "A1:C2", 1)
    cats = _cats(scan_handle(grid, h, "S"))
    assert "uncovered-data" in cats


def test_uncovered_data_side_by_side():
    grid = [
        ("Region", "Rev", None, "Product", "Price"),
        ("NA", 1, None, "Widget", 9),
    ]
    h = TableHandle("S", "A1:B2", 1)
    cats = _cats(scan_handle(grid, h, "S"))
    assert "uncovered-data" in cats


def test_empty_header_corner():
    grid = [("", "Q1", "Q2"), ("Revenue", 1, 2)]
    h = TableHandle("S", "A1:C2", 1)
    cats = _cats(scan_handle(grid, h, "S"))
    assert "empty-header-corner" in cats


def test_false_header_span_value_like_leaf():
    grid = [("Item", "Price", "Margin"), ("X", "$1,200", "15%"), ("Y", "$3,400", "22%")]
    h = TableHandle("S", "A1:C3", 1, header_span=2)
    cats = _cats(scan_handle(grid, h, "S"))
    assert "false-header-span" in cats


def test_clean_table_no_findings():
    grid = [("Region", "Rev"), ("NA", 1), ("EU", 2)]
    h = TableHandle("S", "A1:B3", 1)
    assert scan_handle(grid, h, "S") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_coverage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcg_swarm.coverage'`.

- [ ] **Step 3: Implement `mcg_swarm/coverage.py`**

```python
"""Deterministic coverage / residue detection.

Scans a sheet's full grid against the region(s) the splitter chose and flags
data or structure the static pass would otherwise silently corrupt or drop.
Model-free: this is the detection guarantee, independent of any agent.
"""
from __future__ import annotations

import re

from openpyxl.utils import get_column_letter, range_boundaries

from mcg_swarm.schemas import Finding
from mcg_swarm.splitter import _is_header_candidate

# accounting/currency/percent/number-ish text that signals a "header" row is really data
_VALUE_LIKE = re.compile(r"^\s*[\$€£]?\(?-?[\d.,]+\)?\s*%?\s*$")


def nonempty_cells(grid: list[tuple]) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for i, row in enumerate(grid):
        for j, c in enumerate(row):
            if c not in (None, ""):
                out.add((i + 1, j + 1))
    return out


def region_cells(region: str) -> set[tuple[int, int]]:
    min_col, min_row, max_col, max_row = range_boundaries(region)
    return {(r, c) for r in range(min_row, max_row + 1)
            for c in range(min_col, max_col + 1)}


def coverage_score(grid: list[tuple], regions: list[str]) -> int:
    covered: set[tuple[int, int]] = set()
    for reg in regions:
        covered |= region_cells(reg)
    return len(nonempty_cells(grid) & covered)


def _components(cells: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    """8-connected components over a set of (row, col) cells."""
    remaining = set(cells)
    comps: list[set[tuple[int, int]]] = []
    while remaining:
        seed = remaining.pop()
        stack = [seed]
        comp = {seed}
        while stack:
            r, c = stack.pop()
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    n = (r + dr, c + dc)
                    if n in remaining:
                        remaining.discard(n)
                        comp.add(n)
                        stack.append(n)
        comps.append(comp)
    return comps


def _subgrid(grid, minr, minc, maxr, maxc) -> list[tuple]:
    out = []
    for r in range(minr, maxr + 1):
        row = grid[r - 1] if r - 1 < len(grid) else ()
        out.append(tuple(row[c - 1] if c - 1 < len(row) else None
                         for c in range(minc, maxc + 1)))
    return out


def _a1(minr, minc, maxr, maxc) -> str:
    return f"{get_column_letter(minc)}{minr}:{get_column_letter(maxc)}{maxr}"


def scan_handle(grid: list[tuple], handle, sheet: str) -> list[Finding]:
    """Deterministic detection over one sheet's grid vs its chosen handle region."""
    findings: list[Finding] = []
    try:
        # ---- uncovered-data: nonempty blocks outside the region that look tabular ----
        nonempty = nonempty_cells(grid)
        covered = region_cells(handle.region)
        uncovered = nonempty - covered
        for comp in _components(uncovered):
            minr = min(r for r, _ in comp)
            maxr = max(r for r, _ in comp)
            minc = min(c for _, c in comp)
            maxc = max(c for _, c in comp)
            sub = _subgrid(grid, minr, minc, maxr, maxc)
            if sub and _is_header_candidate(sub[0], sub[1:]):
                findings.append(Finding(
                    category="uncovered-data", severity="error", scope="sheet",
                    source="static", ref=f"{sheet}!{_a1(minr, minc, maxr, maxc)}",
                    message=(f"uncovered tabular block at {sheet}!"
                             f"{_a1(minr, minc, maxr, maxc)} outside detected region "
                             f"{handle.region} — a second table was likely dropped")))

        # ---- header-row inspection (empty corner / false span / transpose) ----
        min_col, min_row, max_col, max_row = range_boundaries(handle.region)
        hr = handle.header_row
        header = _subgrid(grid, hr, min_col, hr, max_col)[0] if hr - 1 < len(grid) else ()
        if header and header[0] in (None, ""):
            findings.append(Finding(
                category="empty-header-corner", severity="error", scope="table",
                source="static", ref=f"{sheet}!{get_column_letter(min_col)}{hr}",
                message=(f"empty top-left header cell at {sheet}!"
                         f"{get_column_letter(min_col)}{hr} — header/orientation is ambiguous "
                         f"(transposed or corner-labelled table)")))

        span = getattr(handle, "header_span", 1)
        if span >= 2 and hr < len(grid):
            leaf = _subgrid(grid, hr + 1, min_col, hr + 1, max_col)[0]
            leaf_vals = [c for c in leaf if c not in (None, "")]
            value_like = [c for c in leaf_vals
                          if isinstance(c, (int, float))
                          or (isinstance(c, str) and _VALUE_LIKE.match(c))]
            if leaf_vals and len(value_like) >= max(1, len(leaf_vals) // 2):
                findings.append(Finding(
                    category="false-header-span", severity="error", scope="table",
                    source="static", ref=f"{sheet}!{handle.region}",
                    message=(f"header_span=2 but row {hr + 1} looks like data "
                             f"(value-like cells) — first data row likely consumed as a header")))

        # ---- transpose-suspected: empty corner + left col labels + top row labels ----
        if header and header[0] in (None, ""):
            below_first = [grid[r - 1][min_col - 1]
                           for r in range(hr + 1, min(max_row, len(grid)) + 1)
                           if min_col - 1 < len(grid[r - 1])]
            top_after = [c for c in header[1:] if c not in (None, "")]
            below_str = [c for c in below_first if c not in (None, "")]
            if (below_str and all(isinstance(c, str) for c in below_str)
                    and top_after and all(isinstance(c, str) for c in top_after)):
                findings.append(Finding(
                    category="transpose-suspected", severity="warning", scope="table",
                    source="static", ref=f"{sheet}!{handle.region}",
                    message=(f"sheet {sheet} may be transposed (labels down column "
                             f"{get_column_letter(min_col)}, periods across row {hr})")))
    except Exception:
        return findings  # never raise — detection is best-effort-safe, deterministic
    return findings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_coverage.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 262 passed, 1 skipped (255 + 7). Zero failures.

- [ ] **Step 6: Commit**

```bash
git add mcg_swarm/coverage.py tests/test_coverage.py
git commit -m "feat(coverage): deterministic residue/boundary detector (model-free)"
```

---

### Task 3: Route orchestrator error sources through `findings`

**Files:**
- Modify: `mcg_swarm/orchestrator.py` (`_stub`, `_orchestrate_core` §3/§5/§7/except, `orchestrate_table` signature)
- Modify: `mcg_swarm/repair_log.py` (`categorize_failures` accepts `Finding` or str)
- Test: `tests/test_orchestrator_findings.py`

**Interfaces:**
- Consumes: `Finding`, `finding_from_gate_failure` (Task 1).
- Produces:
  - `orchestrate_table(source, handle, table_id, llm=None, subagent=None, table_validator=None, max_repairs=2, detect_findings: list[Finding] | None = None) -> CanonicalTable` — `detect_findings` are merged into the returned table's `findings`.
  - All gate failures, merge conflicts, messy-tab, and orchestration errors now flow through `findings` (so `errors` derives). Behavior (which strings appear in `errors`) is unchanged.

- [ ] **Step 1: Write the failing test**

Create `tests/test_orchestrator_findings.py`:

```python
"""Orchestrator routes errors + injected detection findings through findings[]."""
from mcg_swarm.orchestrator import orchestrate_table
from mcg_swarm.splitter import split_workbook
from mcg_swarm.schemas import Finding
from mcg_swarm.source import as_source


def _wb(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Data"
    ws.append(["Region", "Rev"]); ws.append(["NA", 1]); ws.append(["EU", 2])
    p = tmp_path / "ok.xlsx"; wb.save(p)
    return str(p)


def test_detect_findings_merged_into_table(tmp_path):
    path = _wb(tmp_path)
    src = as_source(path)
    handle = split_workbook(src)[0]
    extra = [Finding(category="empty-header-corner", severity="error", scope="table",
                     message="corner empty", source="static")]
    t = orchestrate_table(src, handle, table_id="Data__0", detect_findings=extra)
    assert any(f.category == "empty-header-corner" for f in t.findings)
    assert "corner empty" in t.errors   # derived view includes injected error


def test_clean_table_has_no_error_findings(tmp_path):
    path = _wb(tmp_path)
    src = as_source(path)
    handle = split_workbook(src)[0]
    t = orchestrate_table(src, handle, table_id="Data__0")
    assert [f for f in t.findings if f.severity == "error"] == []
    assert t.errors == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_orchestrator_findings.py -v`
Expected: FAIL — `orchestrate_table()` got an unexpected keyword `detect_findings`.

- [ ] **Step 3: Implement orchestrator changes**

In `mcg_swarm/orchestrator.py`, update the import:

```python
from mcg_swarm.schemas import CanonicalTable, ExtractionRef, Finding, finding_from_gate_failure
```

Replace `_stub` to carry findings:

```python
def _stub(handle, table_id: str, findings: list) -> CanonicalTable:
    """Return a minimal CanonicalTable stub carrying the given findings."""
    return CanonicalTable(
        table_id=table_id,
        sheet=handle.sheet,
        region=handle.region,
        header_row=handle.header_row,
        columns=list(handle.columns),
        description="",
        extraction=ExtractionRef(script_name=f"idx_{table_id}", row_key=[]),
        findings=list(findings),
    )
```

Change `_orchestrate_core` to accept and thread detection findings. Update its signature:

```python
def _orchestrate_core(
    source,
    handle,
    table_id: str,
    llm=None,
    subagent=None,
    max_repairs: int = 2,
    detect_findings: list | None = None,
) -> CanonicalTable:
```

At the top of the body (after the docstring), normalize:

```python
    detect_findings = list(detect_findings or [])
```

Replace the §1 messy-tab stub:

```python
    if handle.ambiguous:
        return _stub(handle, table_id, detect_findings + [Finding(
            category="messy-tab", severity="error", scope="table", source="static",
            message=f"messy tab: {handle.reason or 'ambiguous header'}")])
```

Replace the §3 merge-conflict stub:

```python
        if merged.conflicts:
            return _stub(handle, table_id, detect_findings + [Finding(
                category="merge-conflict", severity="error", scope="table",
                source="static", message=f"merge conflict: {c}") for c in merged.conflicts])
```

Replace the §5 intermediate `CanonicalTable(...)` `provisional_notes=all_notes,` line with findings built from anomalies (the §5 table is only used for the gate; keep it simple — pass anomalies as info findings):

```python
            findings=[Finding(category="anomaly", severity="info", scope="table",
                              source="static", message=n) for n in all_notes],
```
(remove the `provisional_notes=all_notes,` kwarg from the §5 constructor.)

Replace §6 + §7 so all signals become findings:

```python
        # §6  Run quality gate
        report = run_table_tests(source, table, index)
        gate_findings = [] if report.passed else [
            finding_from_gate_failure(str(f)) for f in report.failures]

        # §7  Return fully-populated CanonicalTable (findings = detect + anomalies + gate)
        anomaly_findings = [Finding(category="anomaly", severity="info", scope="table",
                                    source="static", message=n) for n in all_notes]
        return CanonicalTable(
            table_id=table_id,
            sheet=handle.sheet,
            region=handle.region,
            header_row=handle.header_row,
            header_span=getattr(handle, "header_span", 1),
            columns=merged.columns,
            formulas=all_formulas,
            description=merged.description,
            extraction=ExtractionRef(script_name=f"idx_{table_id}", row_key=row_key),
            findings=detect_findings + anomaly_findings + gate_findings,
        )

    except Exception as exc:  # never let a subagent failure escape
        return _stub(handle, table_id, detect_findings + [Finding(
            category="orchestration-error", severity="error", scope="table",
            source="static", message=f"orchestration error: {exc}")])
```

Update `orchestrate_table` to accept and forward `detect_findings`:

```python
def orchestrate_table(
    source,
    handle,
    table_id: str,
    llm=None,
    subagent=None,
    table_validator=None,
    max_repairs: int = 2,
    detect_findings: list | None = None,
) -> CanonicalTable:
```
and forward `detect_findings=detect_findings` into the `_orchestrate_core(...)` call.

In `mcg_swarm/repair_log.py`, make `categorize_failures` tolerant of `Finding` objects. Replace the `for f in failures:` loop with:

```python
    known = {k for _, k in _PREFIXES}
    for f in failures:
        cat = getattr(f, "category", None)
        if cat is not None:                       # Finding: categorize by .category
            key = cat.replace("-", "_")
            cats[key if key in known else "other"] += 1
            continue
        s = str(f)                                # legacy string: categorize by prefix
        for prefix, key in _PREFIXES:
            if s.startswith(prefix):
                cats[key] += 1
                break
        else:
            cats["other"] += 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_orchestrator_findings.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Full suite (watch for tests asserting on errors/provisional_notes)**

Run: `.venv/bin/python -m pytest -q`
Expected: 264 passed, 1 skipped (262 + 2). If any existing test fails because it asserted exact `errors`/`provisional_notes` ordering, confirm the *messages* are unchanged (derivation preserves them) and fix only genuine ordering assumptions; do not weaken assertions. Report any such fix.

- [ ] **Step 6: Commit**

```bash
git add mcg_swarm/orchestrator.py mcg_swarm/repair_log.py tests/test_orchestrator_findings.py
git commit -m "refactor(orchestrator): route all error sources + detection findings through findings[]"
```

---

### Task 4: Wire detection into `run_swarm`

**Files:**
- Modify: `mcg_swarm/runner.py` (per-sheet scan; thread table-scoped findings into `orchestrate_table`; collect sheet/workbook-scoped findings into `WorkbookExtraction`)
- Test: `tests/test_runner_detection.py`

**Interfaces:**
- Consumes: `scan_handle` (Task 2), `orchestrate_table(..., detect_findings=...)` (Task 3), `Finding`.
- Produces: `run_swarm` output where dropped/side-by-side tables yield `WorkbookExtraction.findings`/`errors` with `uncovered-data`, and empty-corner/false-span/transpose land on the relevant `CanonicalTable.findings`. No silent `errors==[]` on the corrupted stress cases.

- [ ] **Step 1: Write the failing test**

Create `tests/test_runner_detection.py`:

```python
"""run_swarm surfaces deterministic detection findings (no silent corruption)."""
import openpyxl
from mcg_swarm.runner import run_swarm


def _save(tmp_path, name, build):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Data"; build(ws)
    p = tmp_path / name; wb.save(p); return str(p)


def test_stacked_tables_flag_uncovered_data(tmp_path):
    def build(ws):
        ws.append(["Region", "Rev", "Units"]); ws.append(["NA", 1, 2])
        ws.append([]); ws.append([])
        ws.append(["Product", "Price", "SKU"]); ws.append(["Widget", 9, "W1"])
    ext = run_swarm(_save(tmp_path, "stacked.xlsx", build))
    cats = [f.category for f in ext.findings]
    assert "uncovered-data" in cats
    assert ext.errors  # derived, non-empty — not silent


def test_side_by_side_flag_uncovered_data(tmp_path):
    def build(ws):
        ws["A1"], ws["B1"] = "Region", "Rev"
        ws["D1"], ws["E1"] = "Product", "Price"
        ws["A2"], ws["B2"] = "NA", 1
        ws["D2"], ws["E2"] = "Widget", 9
    ext = run_swarm(_save(tmp_path, "sbs.xlsx", build))
    assert "uncovered-data" in [f.category for f in ext.findings]


def test_empty_corner_flagged_on_table(tmp_path):
    def build(ws):
        ws.append(["", "Q1", "Q2"]); ws.append(["Revenue", 1, 2]); ws.append(["COGS", 3, 4])
    ext = run_swarm(_save(tmp_path, "transposed.xlsx", build))
    all_cats = [f.category for t in ext.tables for f in t.findings] + \
               [f.category for f in ext.findings]
    assert "empty-header-corner" in all_cats


def test_clean_workbook_no_detection_findings(tmp_path):
    def build(ws):
        ws.append(["Region", "Rev"]); ws.append(["NA", 1]); ws.append(["EU", 2])
    ext = run_swarm(_save(tmp_path, "clean.xlsx", build))
    detection = {"uncovered-data", "empty-header-corner", "false-header-span",
                 "transpose-suspected"}
    found = {f.category for f in ext.findings} | \
            {f.category for t in ext.tables for f in t.findings}
    assert not (found & detection)
    assert ext.errors == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_runner_detection.py -v`
Expected: FAIL — `uncovered-data` not present (detection not wired).

- [ ] **Step 3: Implement `run_swarm` wiring**

In `mcg_swarm/runner.py`, add imports:

```python
from mcg_swarm.coverage import scan_handle
from mcg_swarm.schemas import Finding
```

Replace the happy-path loop + final construction (current lines ~37-48) with:

```python
    subagent = build_subagent(llm=llm, runner=runner, config=config)
    table_validator = build_table_validator(runner=runner, config=config)
    tables, sheets, wb_findings = [], [], []
    for i, h in enumerate(handles):
        sheets.append(h.sheet)
        try:
            grid = source.read_region(h.sheet)
            scan = scan_handle(grid, h, h.sheet)
        except Exception:
            scan = []  # never let detection break extraction
        table_findings = [f for f in scan if f.scope != "sheet"]
        wb_findings.extend(f for f in scan if f.scope == "sheet")
        tables.append(orchestrate_table(
            source, h, table_id=f"{h.sheet}__{i}", llm=llm,
            subagent=subagent, table_validator=table_validator,
            detect_findings=table_findings))
    return WorkbookExtraction(
        workbook=name,
        sheets=sheets,
        tables=tables,
        generator_version=GENERATOR_VERSION,
        findings=wb_findings,
    )
```

(Note: this assumes the signature already threads `runner`/`config` from the merged agent-runner-injection work. If this branch predates that merge, use `build_subagent(llm=llm)` / `build_table_validator(llm=llm)` and `orchestrate_table(..., detect_findings=table_findings)` without `runner`/`config`. Confirm against the current `runner.py` before editing.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_runner_detection.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 268 passed, 1 skipped (264 + 4). Zero failures.

- [ ] **Step 6: Commit**

```bash
git add mcg_swarm/runner.py tests/test_runner_detection.py
git commit -m "feat(runner): wire deterministic detection into run_swarm output"
```

---

### Task 5: Promote the stress battery to a committed no-silent-corruption regression

**Files:**
- Create: `tests/fixtures/nasty_workbooks.py` (generator — adapted from the scratch stress generator)
- Test: `tests/test_no_silent_corruption.py`

**Interfaces:**
- Consumes: `run_swarm`; the generator builds the adversarial workbooks in a pytest `tmp_path`.
- Produces: a regression that asserts the previously-silent cases now carry an `error`-severity finding.

- [ ] **Step 1: Write the failing test**

Create `tests/fixtures/nasty_workbooks.py`:

```python
"""Builders for adversarial workbooks (committed stress fixtures)."""
import openpyxl


def _wb():
    wb = openpyxl.Workbook(); wb.active.title = "Data"; return wb, wb.active


def two_stacked(path):
    wb, ws = _wb()
    ws.append(["Region", "Revenue", "Units"]); ws.append(["NA", 100, 5]); ws.append(["EU", 200, 9])
    ws.append([]); ws.append([])
    ws.append(["Product", "Price", "SKU"]); ws.append(["Widget", 9.99, "W-1"])
    wb.save(path); return str(path)


def side_by_side(path):
    wb, ws = _wb()
    ws["A1"], ws["B1"], ws["C1"] = "Region", "Revenue", "Units"
    ws["E1"], ws["F1"], ws["G1"] = "Product", "Price", "Stock"
    ws["A2"], ws["B2"], ws["C2"] = "NA", 100, 5
    ws["E2"], ws["F2"], ws["G2"] = "Widget", 9.99, 12
    wb.save(path); return str(path)


def preamble_rows(path):
    wb, ws = _wb()
    ws.append(["Report", "generated 2024-06-01", "by system"])
    ws.append(["Confidential", "do not distribute", ""])
    ws.append([])
    ws.append(["Region", "Revenue", "Units"]); ws.append(["NA", 100, 5]); ws.append(["EU", 200, 9])
    wb.save(path); return str(path)


def transposed(path):
    wb, ws = _wb()
    ws.append(["", "Q1", "Q2", "Q3"]); ws.append(["Revenue", 100, 120, 130]); ws.append(["COGS", 40, 48, 52])
    wb.save(path); return str(path)
```

Create `tests/test_no_silent_corruption.py`:

```python
"""Guarantee: cases static gets wrong are detected, never silently corrupted."""
import pytest
from mcg_swarm.runner import run_swarm
from tests.fixtures import nasty_workbooks as nw


def _all_error_categories(ext):
    cats = {f.category for f in ext.findings if f.severity == "error"}
    for t in ext.tables:
        cats |= {f.category for f in t.findings if f.severity == "error"}
    return cats


@pytest.mark.parametrize("builder,expected", [
    (nw.two_stacked, "uncovered-data"),
    (nw.side_by_side, "uncovered-data"),
    (nw.preamble_rows, "uncovered-data"),
    (nw.transposed, "empty-header-corner"),
])
def test_detected_not_silent(builder, expected, tmp_path):
    ext = run_swarm(builder(str(tmp_path / "wb.xlsx")))
    assert expected in _all_error_categories(ext), \
        f"{builder.__name__}: expected {expected} error finding, got none (silent corruption)"
```

- [ ] **Step 2: Run test to verify it fails (then passes)**

Run: `.venv/bin/python -m pytest tests/test_no_silent_corruption.py -v`
Expected: PASS once Tasks 2–4 are in (this task is the proof harness). If `preamble_rows` does not yield `uncovered-data` (its real table is below a mis-detected header), investigate: the uncovered block (rows 4-6) must pass `_is_header_candidate`; if the splitter's chosen region already covers them, adjust the assertion to the actually-correct detection category and report the finding — do not weaken to "any finding."

- [ ] **Step 3: (only if Step 2 surfaced a real gap)** add the missing detector rule in `mcg_swarm/coverage.py` with its own unit test in `tests/test_coverage.py`, then re-run.

- [ ] **Step 4: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 272 passed, 1 skipped (268 + 4 parametrized). Zero failures.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/nasty_workbooks.py tests/test_no_silent_corruption.py
git commit -m "test: committed no-silent-corruption regression over adversarial workbooks"
```

---

## Self-Review

**Spec coverage (Phase 1 portion):**
- Layer 1 deterministic residue scan → Tasks 2, 4 (uncovered-data, empty-header-corner, false-header-span, transpose-suspected).
- `Finding` record as source of truth + `errors`/`provisional_notes` derived → Task 1; all emitters routed through findings → Task 3.
- Empty-header-corner fixes the opaque `orchestration error: 'A'` (now a categorized finding) → Tasks 2/3/4.
- Detection guaranteed with no runner (model-free) → Tasks 2/4 use no LLM.
- Stress battery → committed regression → Task 5.
- Coverage metric (`coverage_score`) for Phase 2 → provided in Task 2 (used later).
- **Deferred to Phase 2 (separate plan):** agent boundary *alteration*, verify-before-accept on re-cuts, the structural agent + new tools, gate coverage-acceptance. This plan delivers the detection guarantee only — matching the `/goal` (detect, don't necessarily solve).

**Placeholder scan:** No TBD/TODO. Every code step shows complete content. (Task 3's `repair_log` step shows one convoluted draft explicitly superseded by a labeled clean replacement — the implementer uses the clean one; called out to avoid ambiguity.)

**Type consistency:** `Finding(category, severity, scope, message, source, ref?, agent_action?, resolution)`, `finding_from_gate_failure(msg)->Finding`, `scan_handle(grid, handle, sheet)->list[Finding]`, `coverage_score(grid, regions)`, `orchestrate_table(..., detect_findings=)`, `_stub(handle, table_id, findings)` — used identically across tasks. Severity routing (`uncovered-data`/`empty-header-corner`/`false-header-span`=error, `transpose-suspected`=warning, anomalies=info, gate=error) matches the Global Constraints.

**Open risk flagged for implementer:** Task 4's note — confirm whether this branch already has the merged `runner`/`config` parameters on `run_swarm`/factories (agent-runner-injection landed on `main`; this branch is off `main`, so it should). Use the matching call form.
