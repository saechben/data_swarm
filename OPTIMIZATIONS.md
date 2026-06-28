# MCG Swarm — Potential Optimizations

Tracked backlog of performance/scaling improvements. Each item notes the
bottleneck, where it lives, a proposed fix, and rough risk/effort. None are
implemented yet — this is a planning document.

---

## 1. The "swarm" is sequential, not parallel  ⭐ (highest-value)

**Bottleneck.** Despite the "swarm" framing, fan-out is a *decomposition* pattern
only — execution is fully serial at both levels:

- **Per-sheet (table) loop** — `mcg_swarm/runner.py:35-39`:
  ```python
  for i, h in enumerate(handles):
      tables.append(orchestrate_table(path, h, ...))   # one sheet at a time
  ```
  A single `subagent` / `table_validator` instance is reused across every sheet,
  called sequentially.

- **Per-band loop** — `mcg_swarm/orchestrator.py:100`:
  ```python
  reports = [subagent.analyze(_band_task(b)) for b in bands]   # one band at a time
  ```

So a workbook that decomposes into N bands across M sheets runs all N×… analyses
back-to-back. This is cheap to ignore in static mode (each band is a ~20-row
openpyxl read) but **dominates wall-clock in ReAct mode**, where every escalated
band and every table validation is a blocking `claude_agent_sdk.query` call of
~50-70s (measured). Five small sheets in ReAct mode ≈ 5× that latency, all serial.

**Why it's safe to parallelize.** The units are independent:
- Sheets/handles produce independent `CanonicalTable`s — no shared mutable state.
- Bands within a table are independent reads; each static/verify pass opens its
  own workbook handle (`extraction.py` / `static.py` reopen per call).
- The result lists just need to preserve order (use indexed futures).

**Proposed fix.**
- Band level: replace the list comprehension with a `concurrent.futures` pool;
  results collected by index to keep merge order stable.
- Sheet level: run `orchestrate_table` per handle in a pool.
- Use a **thread** pool first — both hot paths are I/O/network-bound (openpyxl
  parsing releases the GIL in spots; SDK calls are pure network wait). Move to a
  process pool only if static CPU work proves GIL-bound.
- Add a concurrency cap (e.g. `MCG_MAX_WORKERS`, default ~4-8) to bound openpyxl
  memory and avoid SDK rate limits.

**Risks / prerequisites.**
- **Subagent thread-safety.** `EscalatingSubagent` / `ReActVerifier` /
  `ClaudeSDKAgentRunner` must be reentrant across threads. `ClaudeSDKAgentRunner.run`
  calls `asyncio.run` per invocation — fine from separate threads (each gets its
  own loop), but verify no shared client/loop state. Easiest: construct one runner
  per worker, or confirm statelessness.
- SDK concurrency limits / rate-limiting — the worker cap handles this.
- Determinism of output ordering — preserve via indexed collection, not append.

**Effort:** medium. **Payoff:** large in ReAct mode (near-linear latency cut up to
the worker cap); modest in static mode.

---

## 2. Band fan-out is capped at a constant (K_MAX=4) and serial

**Bottleneck.** `mcg_swarm/size_estimate.py:9` — `K_MAX = 4`. A 100k-row table and
a 5M-row table both split into ≤4 bands; the cap exists *because* each extra
workbook open costs ~2-3s and the bands run serially (see #1). So the "swarm"
gives at most a 4× conceptual split, and currently 0× actual speedup.

**Proposed fix.** Once #1 lands (parallel bands) and #4 lands (cheaper opens),
raise or remove K_MAX so band count scales with size and actually parallelizes.

**Effort:** low (after #1, #4). **Payoff:** unlocks horizontal scaling for big
single tables.

---

## 3. `ExtractionIndex` build is O(rows) in time and memory

**Bottleneck.** `mcg_swarm/extraction.py:49-81` — the constructor materializes the
**entire region** (`grid = list(ws.iter_rows(min_row..max_row, ...))`) and builds a
full `_key_to_phys` map over every data row. This happens for every table,
regardless of how many rows are later queried. It's the memory/time wall behind the
historical 100k-row timeouts and the 11MB `enterprise_transactions.xlsx`.

**Proposed fix.**
- Lazy index: for positional row keys, `_key_to_phys` is unnecessary — derive
  physical rows arithmetically.
- For key-column lookups, build the map on demand or stream a sorted index instead
  of materializing the full grid.
- Avoid holding `grid` in memory at all; stream rows for the key scan.

**Effort:** medium. **Payoff:** large memory reduction on big tables.

---

## 4. openpyxl reopen cost / per-cell reopen in `query()`

**Bottleneck.**
- Every `load_workbook` re-parses the whole sheet XML (~2-3s on large files);
  the codebase already fights this with "single open" passes.
- `ExtractionIndex.query()` (`extraction.py:85`) reopens the workbook **per cell**
  — fine for spot lookups, O(opens) for bulk access (`read_all` mitigates, but only
  if callers use it).

**Proposed fix.**
- Streaming/columnar reader (SAX-style xlsx parse, or convert to Arrow/DuckDB/
  parquet on ingest) to kill the parse floor and enable O(streamed) scans.
- Optional cached/long-lived read-only handle for bulk read patterns (trade the
  live-read property for speed where edits aren't expected).

**Effort:** high (substrate change). **Payoff:** raises the whole ceiling for
large workbooks.

---

---

# Functional Gaps (correctness, not performance)

## G1. Formula extraction into `SegmentReport`/`CanonicalTable` is NOT implemented  ⭐

**Status: dormant by omission.** The swarm never reads formulas out of the sheet.
This is a real capability gap, partially masked by the eval's 81.8% "formula"
metric (see "Why it looks done" below).

**Evidence chain.**
- `SegmentReport.formulas` (`schemas.py:54`) and `CanonicalTable.formulas`
  (`schemas.py:38`) are fully defined (`TableFormula` = target/expression/operands/ast,
  `schemas.py:19-23`).
- The producer is a no-op: `StaticSubagent._analyze_band_single_open` (`static.py:32-64`)
  opens with `data_only=True` (computed values, not formula strings) and returns
  `formulas=[]` hardcoded (`static.py:64`).
- The detector that used to do this — `_detect_formulas` — was **deleted** in commit
  `b77195b` (2026-06-23) as *"dead… no callers… superseded by
  `_analyze_band_single_open`."* The supersession claim is inaccurate: the replacement
  does column dtype inference, **not** formula detection. So removal left no producer.
- The ReAct verifier doesn't fill the gap either: `SegmentReportPatch` (`verifier.py:30-33`)
  carries only column dtype/unit/role + anomalies.
- Net: every band → `formulas=[]` → `merge_reports` dedups empties (`merge.py:31-36`)
  → `CanonicalTable.formulas == []` always.
- The only in-pipeline consumer, quality-gate Phase 4 computed-column verification
  (`quality_gate.py:237-242`), therefore **never fires** — and static never assigns
  `role="computed"` (`static.py:62-63`) so the column side wouldn't trigger it anyway.

**Why it looks done.** `DATA-REQUIREMENTS.md` / `MCG-SWARM.md` report **"intra-table
formula 81.8% (18/22)."** That number does NOT come from extracted formulas. It comes
from the **eval adapter at query time**: `swarm_adapter.compute_formula()`
(`eval/adapters/swarm_adapter.py:220`) receives the formula `expression` + `operands`
**from the eval harness**, resolves each operand phrase to a coordinate via
`deterministic_resolve` / LLM (`resolve_coord`), reads live values, and evaluates with
`eval_expr`. Nothing reads `CanonicalTable.formulas`. So "formula evaluation" works in
the eval, while "formula extraction by the swarm" does not — two different features
sharing a name.

**Spec intent (this WAS in scope).** `MCG-SWARM-SPEC.md` §8 "Formula storage &
execution" specifies capturing `TableFormula` structurally so they can be **replayed**
from the canonical output without the eval handing them over. That self-contained
replay path is what's missing.

**Implementation approach.**
1. A real detector: second pass (or single pass) reading cells with `data_only=False`
   to get formula strings (`=B2*C2`), restricted to the band region. Mind read-only
   mode `EmptyCell` hazards (the original removal cited this) — use `values_only` +
   computed offsets, or a non-read-only open.
2. Parse each formula string into `TableFormula`: target column, normalized
   `expression`, and `OperandBinding`s (column/cell/range/param) — relative refs →
   column names where possible so they replay across rows.
3. Return them from `StaticSubagent` (and optionally let the ReAct verifier confirm/repair
   operand bindings). `merge_reports` dedup already handles the multi-band case.
4. Flip `role="computed"` on target columns so quality-gate Phase 4 actually verifies
   them against live cells.

**Risk:** the second `data_only=False` open is the cost that got it dropped — pair with
optimization #4 (cheaper opens) or fold into the existing single open. **Effort:**
medium. **Payoff:** makes the canonical output self-describing (replayable formulas) and
activates the dormant computed-column quality check.

---

## Scope note

The architecture is tuned for **financial-model-shaped** workbooks: many modest
tables where the valuable output is a compact extraction script (O(schema), not
O(data)). Items #1–#2 make the existing decomposition actually pay off; #3–#4
address raw single-table row-count scaling. Prioritize **#1** — it's the highest
value-to-effort and the one most at odds with the "swarm" name today.
