# Modular Static Analysis — Parallel Analyzer Lenses with an Ensemble Assessor

_Design spec. 2026-07-01._

## 1. Problem

Real-world workbooks violate the swarm's core assumptions (A1–A8, `DATA-REQUIREMENTS.md`).
A dataset tried in production broke several at once on a single sheet: **multiple tables per
tab (A1)**, **irregular/hierarchical headers (A2/A8)**, **transposed regions (A3)**, and
**non-table content (diagrams)** mixed in.

Today's static analysis has exactly one strategy, hardcoded and privileged:
`split_workbook()` calls `detect_table()` once per sheet (`splitter.py:262`, `:150`), which
returns after the **first** header it finds, assumes vertical orientation
(`role="key" if j==0 else "value"`), and caps headers at two rows. There is no seam to try a
different structural interpretation, and no way to reconcile competing interpretations.

**Goal:** make the sheet-level structural analysis *modular* — several analyzers, each viewing
the sheet through a different lens, run in parallel per sheet; a strong assessor (deterministic
score + optional agentic decision) picks the winning layout. The existing framework
(Protocol ports + factory injection) is kept; the band-level agent is kept unchanged.

**Non-goals:** rewriting the band-level `StaticSubagent` (dtype/unit/role inference stays,
`static.py:32`), changing the downstream orchestration/index/gate control flow, or modeling
cross-table dependencies (A7, still out of scope).

## 2. Guiding principles

1. **Modularity lives at the sheet/structure level, not the band level.** The lowest layer —
   the band agent analyzing a slice — stays. Refinements to it are separate work.
2. **Normalize at the analyzer boundary.** An analyzer is responsible for handing downstream a
   *canonical vertical view* of each table it proposes. The band layer keeps its vertical-only
   assumption **by construction**; orientation messiness is absorbed at the boundary via the
   existing `WorkbookSource` seam — not by teaching downstream new tricks.
3. **Deterministic-first, agent-optional, never-worse-than-baseline.** Matches the existing
   ReAct / Layer-2 philosophy. With no runner injected, the system degrades to a deterministic
   ensemble that can never score below today's splitter.
4. **The splitter is not privileged.** Today's `detect_table` becomes *one competing analyzer*,
   assessed like any other candidate.

## 3. Architecture

```
                     ┌─────────────── per sheet ───────────────┐
   WorkbookSource →  │  parallel analyzer lenses (registry)     │
                     │    • VerticalSplitAnalyzer  (= today)    │
                     │    • MultiTableAnalyzer     (geometry)   │
                     │    • TransposeAnalyzer      (content)    │
                     │    • (SemanticAnalyzer, agentic — later) │
                     │            │  each emits                 │
                     │            ▼  list[LayoutCandidate]      │
                     │  ┌──────────────────────────────────┐   │
                     │  │ ASSESSOR (two-stage)              │   │
                     │  │  1. dedup + deterministic score   │   │
                     │  │  2. short-circuit if dominant/agree│  │
                     │  │  3. else agentic arbiter (top-K)  │   │
                     │  │  4. verify-before-accept floor    │   │
                     │  │  5. live re-validation            │   │
                     │  └──────────────────────────────────┘   │
                     │            │ winning candidate           │
                     │            ▼ normalized TableHandle[s]    │
                     └──────────────────────────────────────────┘
                                  │
     [UNCHANGED] orchestrate_table → plan_bands → StaticSubagent → build_index → quality_gate
```

Three layers: **analyze (parallel) → assess (pick) → orchestrate bands (unchanged)**.

## 4. Components

### 4.1 `SheetAnalyzer` protocol (new)

A new Protocol, sitting beside the existing `WorkbookSource` (`source.py:11`) and `Subagent`
(`subagent/__init__.py:20`) ports.

```python
class SheetAnalyzer(Protocol):
    name: str                      # stable id, e.g. "vertical", "multitable", "transpose"
    def analyze(self, sheet: SheetView) -> list[LayoutCandidate]: ...
```

- Input `SheetView`: a read-only whole-sheet view. Reuse / extend the existing
  `SheetView` in `structural_tools.py` (already built for the Layer-2 reviewer) rather than
  inventing a second one.
- Output: zero or more `LayoutCandidate`s. Zero = "this lens sees nothing here" (valid; e.g.
  the transpose lens on a normal vertical sheet). An analyzer **never raises** — internal
  failure yields `[]` plus a `Finding` (consistent with the "static never raises" contract,
  `DATA-REQUIREMENTS.md §4`).

### 4.2 `LayoutCandidate` (new type, `schemas.py`)

The unit the assessor ranks. A candidate describes an entire sheet interpretation.

```python
@dataclass(frozen=True)
class LayoutCandidate:
    method: str                    # which analyzer produced it
    handles: tuple[TableHandle, ...]   # one or more tables (A1 relaxed)
    view: SourceView | None        # normalizing transform; None = identity (vertical)
    coverage: float                # fraction of non-empty cells claimed by handles
    findings: tuple[Finding, ...]  # excluded regions, warnings, transpose flags
    confidence: float              # analyzer's own self-report (advisory to the assessor)
```

Key point: each `TableHandle` in `handles` is expressed **in normalized (vertical) coordinates**,
paired with the `view` that produces those coordinates from the raw sheet. Downstream reads
through `view`, so it only ever sees vertical tables.

### 4.3 Normalizing view seam (the transpose answer)

The existing `WorkbookSource` Protocol (`sheet_names / read_region / read_cell /
read_formula_region`, `source.py:11`) is the seam. Add a **`SourceView`** decorator: a
`WorkbookSource` that wraps another source and applies a coordinate transform.

- `IdentityView` — pass-through (the default for vertical analyzers; effectively `None`).
- `TransposedView` — swaps row/column axes in `read_region` / `read_cell` /
  `read_formula_region`. A transpose-aware analyzer detects the matrix block (reusing
  `transpose-suspected`, `coverage.py:133`) and attaches a `TransposedView` so the region is
  presented as rows=records, columns=fields.

Downstream (`plan_bands`, `StaticSubagent`, `build_index`, `quality_gate`) is handed the
candidate's `view` as its source and **stays byte-for-byte unchanged** — it reads a normal
vertical table. This is why the band layer's vertical assumption survives untouched.

**Diagrams / non-table content:** handled by *exclusion*, not normalization. An analyzer simply
does not emit handles over a chart/legend region and records an `excluded-region` finding.
Downstream never sees it.

### 4.4 Analyzer registry + config

- A registry maps `name → SheetAnalyzer factory`. Built-ins register at import.
- `SwarmConfig` (frozen dataclass, `config.py:11`) gains
  `analyzers: tuple[str, ...] = ("vertical",)` — the active lens set. Default is
  `("vertical",)` so **existing behavior is bit-for-bit preserved** until the user opts in to
  more lenses.
- `analyzers=("vertical", "multitable", "transpose")` runs all three in parallel per sheet.
- Consistent with the existing "config knows nothing about providers" rule — it names lenses by
  string id only; construction is via the registry/factories (like `build_subagent`).

### 4.5 The assessor (two-stage) — the hard part

`assess(candidates, sheet, runner, config) -> LayoutCandidate`.

**Stage 0 — dedup.** Candidates that describe the same regions (within tolerance) collapse; the
higher-confidence method wins the label. Prevents three lenses that agree from triggering an
agent call.

**Stage 1 — deterministic score + prune.** Extend the existing `score_handles` /
`coverage_score` (`structural.py:67`, `coverage.py:35`) into a richer, documented scoring
function over a *whole candidate*:
- coverage of non-empty cells (higher better),
- projected gate errors (lower better — dedup/duplicate-header/empty-corner signals),
- region-gap penalty (interior blank rows/cols = fusion smell, reuse `_region_gaps`),
- header-quality score (string-purity of header span, year-awareness — reuse existing),
- orientation-consistency (does a content-type check agree with the claimed orientation?).

Rank all candidates. **Short-circuit** (no agent) when either: only one candidate exists, or the
top candidate dominates the runner-up by a margin `M`. Otherwise keep the **top-K** (K=2–3).

**Stage 2 — agentic arbiter (only on genuine disagreement, only if `runner` injected).** The
arbiter receives the top-K candidates + evidence (region maps, sample cells, per-candidate
scores, findings) via a read-only toolset (extend `build_sheet_toolset`,
`structural_tools.py`). It **chooses among the K** — it does not invent a new layout (bounds the
blast radius, same discipline as the ReAct header-recovery gate). Output: chosen `method` +
rationale.

**Stage 3 — verify-before-accept floor.** The chosen candidate is scored/validated exactly as
Layer 2 does today: it replaces the baseline (`vertical`) only when **provably not worse**
(≥ baseline coverage, ≤ baseline errors). If the agent picks something worse, the baseline
stands. **So the ensemble can never score below today's splitter on any sheet.**

**Stage 4 — live re-validation.** Reuse the run_swarm re-validation already built for Layer-2
boundary alteration (`runner.py:57-94`): the accepted candidate's handles are re-read live
before commitment, so a proposal that looks good on the snapshot but fails live is rejected.

No runner injected → Stages 2 is skipped; the deterministic top-ranked candidate wins (this is
the graceful degradation, and it can still beat today's single-strategy splitter because it runs
multiple deterministic lenses).

### 4.6 Integration into `run_swarm` / splitting

- `split_workbook()` (`splitter.py:262`) is rewritten to: for each sheet, run the active
  analyzers (parallel), assess, and return the winning candidate's normalized handles. It now
  returns **a flat list of `(TableHandle, view)` pairs** — possibly several per sheet.
- `run_swarm` (`runner.py:15`) iterates those pairs into `orchestrate_table`, passing each
  handle's `view` as the source. The existing per-table loop already supports N tables; this is
  the "multi-table is a data-shape change, not control-flow" property.
- Today's Layer-2 `StructuralReviewer` (`structural.py:128`) is **subsumed** by this assessor
  — its "propose re-cut → score → accept if better → live re-validate" logic is the seed of
  Stages 1/3/4. It is refactored into the assessor rather than kept as a parallel mechanism.

## 5. Failure behavior (unchanged contract)

- Analyzers never raise; a lens failure = `[]` + finding.
- If **all** analyzers return nothing for a sheet, fall back to today's behavior: a `TableHandle`
  stub with `errors` populated (`orchestrator.py:23`). One bad sheet never fails the file.
- verify-before-accept + live re-validation guarantee no regression vs. the current baseline.

## 6. Scope & phasing

This is larger than one plan. Decompose into three phases, each its own plan. Ship Phase A
first — it is a pure, provably-neutral refactor.

**Phase A — Foundational refactor (safe, no behavior change).**
- Add `SheetAnalyzer` protocol, `LayoutCandidate`, the registry, and `SwarmConfig.analyzers`.
- Refactor `detect_table`/`split_workbook` internals into `VerticalSplitAnalyzer` (the baseline
  lens) with **zero behavior change** when `analyzers=("vertical",)`.
- Deterministic assessor Stage 0/1 only (single candidate → passthrough).
- Exit criterion: full existing test suite green, extraction output identical on the eval corpus.

**Phase B — Assessor depth + normalization seam.**
- `SourceView` / `IdentityView` / `TransposedView` + downstream wiring through `view`.
- Assessor Stages 2–4 (agentic arbiter, verify-before-accept, live re-validation); subsume the
  Layer-2 `StructuralReviewer`.
- Exit criterion: with a runner injected, no eval regression; arbiter demonstrably picks the
  baseline when lenses agree.

**Phase C — New lenses.**
- `MultiTableAnalyzer` (geometry/blob, reuse `_components`) — proves the ensemble beats baseline
  on the real dataset's multi-table sheets.
- `TransposeAnalyzer` (content-type, reuse `transpose-suspected`) — exercises `TransposedView`
  end-to-end.
- `SemanticAnalyzer` (agentic lens for diagram exclusion / ambiguous headers) — optional, gated
  on runner.
- Exit criterion: measurable pass-rate gain on the previously-excluded corpus workbooks
  (`multi_region_sales`, `quarterly_pnl`, `segment_report`, `store_ops`, `cashflow_signs`).

## 7. Testing strategy

- **Contract tests** for `SheetAnalyzer`: every registered analyzer returns valid candidates,
  never raises, emits `[]` cleanly on irrelevant sheets.
- **Golden neutrality test (Phase A gate):** `analyzers=("vertical",)` produces byte-identical
  `WorkbookExtraction` to `main` across the eval corpus.
- **`TransposedView` unit tests:** round-trip a known matrix; assert downstream `build_index`
  resolves the correct axis with no band-layer changes.
- **Assessor tests:** (a) single candidate → passthrough; (b) dominant candidate →
  short-circuit, no agent call; (c) genuine disagreement → arbiter invoked; (d) agent picks a
  worse candidate → verify-before-accept keeps baseline; (e) live re-validation rejects a
  snapshot-only winner.
- **Regression:** the existing structural/boundary suites (`test_structural_*`,
  `test_runner_structural`, `test_structural_repair_e2e`) must stay green as Layer-2 is subsumed.

## 8. Risks & open questions

- **Dedup tolerance & prune margin `M`** need tuning; wrong values either over-call the agent or
  suppress a better candidate. Mitigation: start conservative (agent only on clear disagreement),
  measure on corpus.
- **Assessor is the load-bearing piece.** A weak deterministic scorer forces too many agent calls
  (cost) or picks wrong when no runner is present. The richer score (§4.5) is the mitigation;
  it must be unit-tested against labeled corpus sheets.
- **Multi-table `row_key` selection** — each emitted handle still needs a clean key column
  (A4). Handles that can't get one degrade to the existing error-stub path; not a new failure
  mode, but multi-table surfaces it more often.
- **Formula extraction across multiple tables per sheet** — intra-table resolution (`resolve/`)
  is per-table and unaffected; cross-table refs remain out of scope (A7).
- **Open:** should the agentic arbiter be allowed to *merge* regions from different candidates
  (e.g. table split from lens A + orientation from lens B), or only pick one whole candidate?
  This design says **pick-one** (bounded blast radius); merging is a deliberate future extension.
