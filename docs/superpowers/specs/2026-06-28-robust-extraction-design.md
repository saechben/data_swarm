# Robust Extraction — Design Spec

_Date: 2026-06-28 · Branch: `feat/robust-extraction` · Status: approved design, pre-implementation_

## 1. Goal

Make the swarm's output the reliable, self-describing structure the project exists
to produce: a structure an agent can navigate by **metadata alone** — knowing which
tables/columns to access without reading the underlying data and without guessing.

This spec covers the **reliability foundation (north-star "A")**: *zero silent errors
+ maximal auto-recovery*. Every emitted table is either provably-correct (passes every
gate) or carries an explicit failure flag — never silently wrong — and the agent layer
becomes a real **bounded multi-pass repair loop** that drives the error count toward
zero instead of taking a single shot.

It also introduces a **data-source interface** so input is no longer hardcoded to a file
path, and an **adaptive sampling strategy** so validation is both thorough on small
tables and bounded on large ones.

## 2. Non-goals (explicitly deferred)

- **B (per-table/column confidence for routing)** and **C (a self-describing catalog as
  the primary artifact)** — future branches; this spec is the prerequisite for both.
- **Hardcoded deterministic repair strategies.** Decided against pre-building a catalog of
  fixes for errors we have not yet observed. The ReAct agent is the flexible repair engine;
  we **log** which gate failures recur and only later automate the frequent ones. (See §10.)
- **Performance optimization of large-table scans.** Accepted as "compute now, optimize
  later"; logged and tracked in `OPTIMIZATIONS.md` (#1–#4).
- **Removing the band-level ReAct verifier.** Left unchanged; out of scope.

## 3. Design overview

```
run_swarm(source) ─ per sheet ─► _orchestrate_core ─► CanonicalTable (may have errors)
                                                          │
                                                          ▼
                                          TableValidator.review  (robustness layer)
                                          ┌─────────────────────────────────────┐
                                          │ ALWAYS runs when agent available     │
                                          │ size gate REMOVED (uses sampling)    │
                                          │ loop ≤ max_passes:                   │
                                          │   gate(sample) → errors?             │
                                          │     none & validated → done          │
                                          │   agent(failures + prior attempts)   │
                                          │   → candidates → verify-before-accept │
                                          │   → keep best → repeat               │
                                          │ log every pass (categorized)         │
                                          └─────────────────────────────────────┘
                                                          ▼
                              CanonicalTable: errors=[] (clean) OR remaining errors (flag)
```

## 4. Components

### 4.1 Data-source interface (`mcg_swarm/source.py`, new)

A port that abstracts *where the cells come from*, so the swarm depends on an interface
rather than on `openpyxl.load_workbook(path)`. The read surface is exactly what the swarm
needs today (derived from current openpyxl usage in `splitter.py`, `extraction.py`,
`quality_gate.py`, `static.py`, `tools.py`):

```python
class WorkbookSource(Protocol):
    def sheet_names(self) -> list[str]: ...
    def dimensions(self, sheet: str) -> tuple[int, int, int, int]:
        """(min_row, min_col, max_row, max_col) of the used range, 1-based."""
    def read_region(self, sheet: str, min_row: int, min_col: int,
                    max_row: int, max_col: int) -> Iterator[tuple]:
        """Yield rows of values (values_only), top-to-bottom."""
    def read_cell(self, sheet: str, row: int, col: int) -> Any: ...
```

- **Concrete implementation shipped now:** `OpenpyxlFileSource(path)` — preserves current
  behavior exactly (read-only, `data_only=True`, per-call open to keep live-read semantics
  where that matters). This is the only implementation in this branch.
- **`run_swarm` accepts either** a `WorkbookSource` **or** the existing `{"main": path}` /
  path (wrapped internally in `OpenpyxlFileSource`) — full back-compat.
- Internal read sites are migrated to take a `WorkbookSource`. Read semantics (e.g.
  `ExtractionIndex.query()`'s live-read property) are defined by the source implementation.
- **Future sources** (bytes/`BytesIO`, pandas DataFrame, Arrow/DuckDB, streaming) implement
  the same port without touching extraction/validation logic. Not built here.

### 4.2 Adaptive sampling (`mcg_swarm/sampling.py`, new)

One shared strategy used by both the gate and the agent's evidence:

```python
def select_sample(row_keys: list, *, full_threshold: int, sample_size: int) -> list:
    """≤ full_threshold rows -> return all. Else a representative high-N sample
    drawn from HEAD + strided-middle + TAIL so late-row anomalies are caught."""
```

- Small tables (≤ `full_threshold`, default **300**) → inspect **everything**.
- Large tables → **~300** rows spread across the table (head/middle/tail stripes), **not**
  the first-N. This directly fixes the blind spot that let a late-row dtype error pass both
  static and the gate (the `ResolvedDays` case).
- Both thresholds env-tunable (`MCG_SAMPLE_FULL_THRESHOLD`, `MCG_SAMPLE_SIZE`).

### 4.3 Quality-gate upgrade (`mcg_swarm/quality_gate.py`)

- Replace contiguous `sample_keys = keys[:sample_size]` (`quality_gate.py:66`) with
  `select_sample(keys, ...)`.
- Keep the single-batched-open design (`quality_gate.py:116-138`); the bounding box now
  spans the spread sample. **Accepted cost:** on huge tables the box approaches the full
  height → ~O(rows) read per pass (logged; see §9).
- Net effect: the gate **detects more real errors**, giving the repair loop genuine targets.

### 4.4 Bounded multi-pass repair loop (`mcg_swarm/subagent/table_check.py`)

Replace the single-shot `TableValidator.review` with a loop (activates the dormant
`max_repairs`, `orchestrator.py:56`):

```
errors = table.errors
for pass in range(max_passes):            # default 3
    if not errors and validated_once:     # clean & at least one validation pass done
        break
    patch = agent(seed = failures(errors) + summary(prior_attempts))   # live ReAct
    best = best verify-before-accept candidate from patch
    if best improves on current (fewer errors, or tie + better label score):
        table, errors = best, best.errors
        record attempt; continue
    break                                  # no improvement -> stop
return table (errors = [] if cleared, else remaining)
```

- **Always runs** when a validator exists (validate default-on); the **≤40-row size gate is
  removed** (`table_check.py:88`) — large tables are handled via sampled inspection (§4.2).
- **Agent sees, each pass:** the remaining categorized failures **and** a summary of prior
  passes' attempts (so it does not repeat a rejected fix).
- **Verify-before-accept unchanged** (`table_check.py:271-285`): never regress, never mark a
  wrong table passing. Loop never raises — returns best-so-far on any error.

### 4.5 Composition / activation (`mcg_swarm/subagent/__init__.py`)

- Activation is unchanged at the master-switch level: `MCG_SUBAGENT=react` **and** an
  available agent (`claude` CLI or `ANTHROPIC_API_KEY`). What changes is that once active,
  the validator **runs on every table, not only on errored ones** (old behavior was
  only-on-error unless `validate`). With no agent / `static` mode it stays a no-op (graceful
  degrade — errors surfaced, same as today).
- `MCG_REACT_VALIDATE` is reinterpreted as "also validate clean tables" and defaults on.
  New: `MCG_REPAIR_MAX_PASSES` (default 3).
- Note: the swarm still defaults to `static` (no agent). Turning on robustness = set
  `MCG_SUBAGENT=react`. We are **not** flipping the global default in this branch.

### 4.6 Repair logging + failure categorization (`mcg_swarm/repair_log.py`, new)

The data runway for deciding what to automate later. Per pass emit a structured record:

```
{workbook, table_id, pass, errors_before, errors_after, accepted,
 failure_categories: {coverage_gap|column_name|column_integrity|row_integrity|
                      round_trip|computed: count},
 agent_patch_summary, latency_s}
```

- A small categorizer maps the gate's failure strings (which already carry stable prefixes
  like `"coverage gap:"`, `"column-integrity:"`, `"round-trip:"`) to categories.
- Emitted via `logging` (INFO) and, when `MCG_REPAIR_LOG=<path>` is set, appended as JSONL
  for offline frequency analysis.

## 5. The reliability contract

Unchanged guarantee, stronger delivery:

- `CanonicalTable.errors == []` **iff** the quality gate passes.
- A table the loop cannot clear keeps its remaining `errors` (the explicit failure flag)
  and is excluded from `build_indices` (`runner.py:54-56`) — never silently emitted as
  usable.
- The validator and loop never raise; worst case returns the best table seen.

## 6. Configuration & defaults

| Knob | Default | Meaning |
|---|---|---|
| `MCG_SUBAGENT` | `static` | `react` enables the agent validator/repair loop |
| `MCG_REACT_VALIDATE` | `on` | also validate clean tables (not just on errors) |
| `MCG_REPAIR_MAX_PASSES` | `3` | repair loop budget |
| `MCG_SAMPLE_FULL_THRESHOLD` | `300` | ≤ this many rows → inspect all |
| `MCG_SAMPLE_SIZE` | `300` | spread-sample size for larger tables |
| `MCG_REPAIR_LOG` | unset | JSONL path for per-pass repair records |

## 7. Testing strategy (TDD)

Live agent is too slow/non-deterministic for CI → use the existing `FakeAgentRunner` with
scripted patches.

- **Loop, multi-pass to clean:** fake returns a partial fix pass 1, the rest pass 2 →
  final `errors == []`, 2 passes recorded.
- **Verify-before-accept rejects regression:** fake returns a worse patch → original kept.
- **Budget exhaustion:** errors remain after `max_passes` → retained on the table.
- **No-improvement stop:** fake returns a no-op → loop stops after one pass.
- **Sampling catches late-row anomaly:** the `ResolvedDays`-style workbook (numeric head,
  text tail) now **fails the gate** under `select_sample` where contiguous-first-N passed.
- **Sampling bounded:** `select_sample` returns ≤ cap for a 1M-row key list; includes
  head+tail.
- **Source port:** `OpenpyxlFileSource` round-trips `sheet_names/dimensions/read_region/
  read_cell` against a fixture; `run_swarm` accepts both a path and a `WorkbookSource`.
- **Categorizer:** each gate failure prefix → correct category.
- **(optional, not in CI)** live smoke: `run_swarm` on the demo fix workbook clears/validates.

## 8. Scalability notes (accepted costs)

- Spread sampling means a gate pass reads across the table (~O(rows) on huge tables), run a
  few times by the loop. Accepted for now; logged. Add to `OPTIMIZATIONS.md` as a follow-up
  (stripe-bounded reads / streaming source via the new port).
- The data-source port is the seam through which a future streaming/columnar implementation
  (OPTIMIZATIONS #4) can remove this cost without changing extraction logic.

## 9. Incremental delivery order

1. `WorkbookSource` port + `OpenpyxlFileSource`; thread through `run_swarm` (back-compat).
2. `sampling.select_sample` + unit tests.
3. Gate uses `select_sample`; late-row-anomaly test goes green.
4. Repair loop in `TableValidator` (FakeAgentRunner tests) + always-on activation.
5. `repair_log` + categorizer; wire into the loop.
6. Update `OPTIMIZATIONS.md` (#1 cross-ref) and a `docs/diagrams/error-recovery.md` diagram.

## 10. Future (out of scope, recorded)

- Promote frequently-logged failure categories into cheap deterministic pre-repairs.
- North-star B: per-table/column `verified|provisional` confidence for routing.
- North-star C: workbook-level catalog as the primary, agent-queried artifact.
