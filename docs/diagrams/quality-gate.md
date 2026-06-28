# Table Test Reports — `run_table_tests`

How the in-loop quality gate validates an extracted table and produces a
`TableTestReport`. This is the deterministic check the orchestrator runs at §6
before accepting a table.

- **Entry:** `run_table_tests(path, table, index, sample_size=25)` — `mcg_swarm/quality_gate.py:22`
- **Returns:** `TableTestReport(passed: bool, failures: list[str])` — `quality_gate.py:14-17`
- **Consumed by:** `orchestrator.py:134-135` (sets `CanonicalTable.errors`), and indirectly by the
  table-level ReAct validator's verify-before-accept (fewer errors ⇒ accept).

## Flow

```mermaid
flowchart TD
  start["run_table_tests(path, table, index, sample_size=25)<br/>quality_gate.py:22"]
  start --> setup["keys = index.row_keys()<br/>cols = index.column_names()<br/>quality_gate.py:47-48"]

  setup --> p1["<b>Phase 1 — Coverage</b><br/>resolution-only, ZERO file I/O<br/>every col in _col_to_phys?<br/>every key in _key_to_phys?<br/>quality_gate.py:53-59"]

  p1 --> sample["Build sample + cell set<br/>sample_keys = keys[:25] (contiguous)<br/>collect 'needed' (row,col) + sample_cells<br/>quality_gate.py:66-111"]

  sample --> scan["<b>ONE workbook open</b><br/>scan bounding box of needed cells → live_cache<br/>replaces N×cols separate opens (~2-3s each)<br/>quality_gate.py:116-138"]

  scan --> p2a["<b>Phase 2a — column-name gate</b><br/>build live_col_map (bottom-first across span)<br/>every table column present in live header?<br/>no duplicate column names?<br/>quality_gate.py:140-166"]
  p2a --> p2b["<b>Phase 2b — column-integrity</b><br/>index _col_to_phys col == live header col<br/>catches numeric→numeric remaps<br/>quality_gate.py:168-180"]
  p2b --> p2c["<b>Phase 2c — row-integrity</b><br/>for sampled keys: live key-col cell == row key<br/>quality_gate.py:182-193"]
  p2c --> p3["<b>Phase 3 — round-trip</b><br/>first 5 keys × cols: index.query(k,col) vs live_cache<br/>values_match w/ dtype tolerance; None/str-eq short-circuit<br/>(query() reopens per cell — bounded ≤5×cols)<br/>quality_gate.py:195-231"]
  p3 --> p4["<b>Phase 4 — computed columns</b><br/>role=='computed' & matching formula → evaluate vs live<br/>⚠ DORMANT: table.formulas always [] (see OPTIMIZATIONS G1)<br/>quality_gate.py:233-262"]

  p4 --> verdict{"failures empty?"}
  verdict -->|yes| passed["TableTestReport(passed=True, failures=[])"]
  verdict -->|no| failed["TableTestReport(passed=False, failures=[...])"]

  %% every phase APPENDS to one shared failures[]; none short-circuit
  p1 -.append.-> F[("failures list")]
  p2a -.-> F
  p2b -.-> F
  p2c -.-> F
  p3 -.-> F
  p4 -.-> F
  F -.-> verdict

  classDef io fill:#fde,stroke:#c39;
  classDef noio fill:#dfe,stroke:#3a6;
  class scan,p3 io;
  class p1 noio;
```

## Phase reference

| Phase | Checks | File I/O | Source |
|---|---|---|---|
| 1 — Coverage | every column & row key resolves in the index maps | **none** (in-memory) | `quality_gate.py:53-59` |
| (scan) | batch-read all needed cells in one open | **1 open**, bounding box only | `quality_gate.py:116-138` |
| 2a — Column-name | live header contains each table column; no dup names | from cache | `quality_gate.py:140-166` |
| 2b — Column-integrity | index col == live header col (no silent remap) | from cache | `quality_gate.py:168-180` |
| 2c — Row-integrity | sampled key cell matches the resolved key | from cache | `quality_gate.py:182-193` |
| 3 — Round-trip | `index.query()` value == independent live read | ≤ 5×cols opens (bounded) | `quality_gate.py:195-231` |
| 4 — Computed | re-evaluate formula vs live cell | from cache | `quality_gate.py:233-262` |

## Design notes

- **Accumulate, don't short-circuit.** All phases run and append to one shared
  `failures[]`; the verdict is simply `passed = not failures` (`quality_gate.py:264`).
  A table reports *every* problem in one pass, not just the first.
- **One open for correctness checks.** The single batched scan (`quality_gate.py:116-138`)
  replaced per-cell opens that cost ~2-3s each on large files — the main perf fix that
  unblocked 100k-row tables.
- **Sampling keeps it cheap.** `sample_size=25` *contiguous first* keys keep the scan
  bounding box small; round-trip is further capped to `ROUND_TRIP_SUBSAMPLE=5`
  (`quality_gate.py:201`). Coverage (Phase 1) is exhaustive because it's free (no I/O).
- **Phase 4 is currently dormant** — `table.formulas` is always `[]` and static never
  sets `role="computed"`, so the computed-column check never fires. See
  `OPTIMIZATIONS.md` G1 (formula extraction gap).
