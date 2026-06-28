# Error Recovery — `TableValidator.review`

Bounded multi-pass ReAct repair loop that runs after static extraction whenever a
table has errors (or validation is enabled), verify-before-accept at every step.

- **Entry:** `TableValidator.review(source, handle, table)` — `table_check.py:253`
- **Returns:** `CanonicalTable` (errors cleared or reduced); never raises — any
  exception returns the original table unchanged (`table_check.py:292-293`)
- **Consumed by:** `orchestrator.py:180` (`orchestrate_table` calls
  `table_validator.review`); downstream `runner.py:58` (`build_indices` skips
  tables that still carry errors)

## Flow

```mermaid
flowchart TD
  entry["orchestrate_table(source, handle, table_id, ...)<br/>orchestrator.py:157"]
  entry --> core["_orchestrate_core — static pipeline<br/>split → bands → merge → build_index<br/>orchestrator.py:36"]

  core --> gate0["§6 run_table_tests(source, table, index)<br/>quality_gate.py:22<br/>orchestrator.py:135"]
  gate0 --> stamp["CanonicalTable(errors=[...] or [])<br/>orchestrator.py:136-151"]

  stamp --> check_validator{"table_validator<br/>supplied?<br/>orchestrator.py:179"}
  check_validator -->|no| skip_tv["return table as-is<br/>(static mode — no-op)"]
  check_validator -->|yes| tv["TableValidator.review(source, handle, table)<br/>table_check.py:253"]

  tv --> auth{"MCG_SUBAGENT=react<br/>+ agent auth available?<br/>subagent/__init__.py"}
  auth -->|no| noop["build_table_validator → None<br/>review never called<br/>runner.py:36"]
  auth -->|yes| should["TableCheckPolicy.should_check(table, n_data_rows)<br/>bool(table.errors) or validate<br/>table_check.py:92-93"]

  should -->|False| ret_unchanged["return table unchanged<br/>(clean + validate=False)<br/>table_check.py:259"]
  should -->|True| loop_init["current = table; attempts = []<br/>table_check.py:261"]

  loop_init --> loop_head["for pass_no in range(max_passes)<br/>max_passes default=3, MCG_REPAIR_MAX_PASSES<br/>table_check.py:262"]

  loop_head --> run_agent["_run_agent(src, handle, current, attempts)<br/>BandView + live SDK call → TableRecoveryPatch<br/>cost: 1 blocking claude_agent_sdk.query ~50-70s<br/>table_check.py:295"]

  run_agent --> candidates["_candidates(current, patch)<br/>structural rebuild or metadata fix<br/>table_check.py:193"]

  candidates --> reindex["_reindex_and_check(src, cand)<br/>build_index + run_table_tests per candidate<br/>cost: O(sample_size) spread-sampled row reads<br/>table_check.py:125"]

  reindex --> accepts{"_accepts(current, cand, cand_errs)<br/>fewer errors than current → True<br/>tie: label score wins<br/>table_check.py:310"}

  accepts -->|rejected| log_rej["log_repair_pass(..., accepted=False)<br/>repair_log.py<br/>table_check.py:276"]
  log_rej --> break_rej["break — no improvement,<br/>stop burning passes<br/>table_check.py:290"]

  accepts -->|accepted| best["keep best candidate<br/>(_ranks_higher: fewer errs, then label score)<br/>table_check.py:273-274"]
  best --> log_acc["log_repair_pass(..., accepted=True)<br/>repair_log.py<br/>table_check.py:276"]
  log_acc --> update["current = best<br/>attempts.append(patch_summary)<br/>table_check.py:285-286"]

  update --> cleared{"best_errs == []?<br/>table_check.py:287"}
  cleared -->|yes| break_clean["break — errors cleared<br/>table_check.py:288"]
  cleared -->|no| loop_head

  break_clean --> emit["return current<br/>(errors=[] — clean)<br/>table_check.py:291"]
  break_rej --> emit2["return current<br/>(errors remaining)<br/>table_check.py:291"]
  loop_head -->|"pass_no == max_passes-1<br/>loop exhausted"| emit2

  emit --> downstream["build_indices(path, extraction)<br/>runner.py:51<br/>skips tables with errors<br/>runner.py:58"]
  emit2 --> downstream

  skip_tv -.-> downstream
  ret_unchanged -.-> downstream
  noop -.-> downstream

  classDef agent fill:#fde,stroke:#c39;
  classDef gate fill:#dfe,stroke:#3a6;
  classDef flow fill:#def,stroke:#36c;
  classDef sink fill:#ffe,stroke:#963;
  class run_agent agent;
  class gate0,reindex,accepts gate;
  class loop_head,candidates,best,update,cleared flow;
  class downstream,emit,emit2 sink;
```

## Phase reference

| Step | What it does | Cost | Source |
|---|---|---|---|
| `_orchestrate_core` | Static split→band→merge→index pipeline | O(bands × rows) openpyxl reads | `orchestrator.py:36` |
| `§6 run_table_tests` | Deterministic quality gate; stamps `errors` | 1 workbook open + spread sample | `orchestrator.py:135`, `quality_gate.py:22` |
| `TableCheckPolicy.should_check` | Gate entry: errors present OR validate=True; no size cap | O(1) | `table_check.py:92` |
| `_run_agent` | Live ReAct SDK call; builds BandView over table region; seeds prior attempts | ~50-70s blocking network | `table_check.py:295` |
| `_candidates` | Structural rebuild (header_row/span + full cols) or metadata patch; tries agent span, span=1, original span | O(1) | `table_check.py:193` |
| `_reindex_and_check` | Re-runs `build_index` + `run_table_tests` on candidate; spread-sampled gate read | O(sample_size) random row reads | `table_check.py:125` |
| `_accepts` | Strictly fewer errors → accept; tie-break: higher year-aware label score; never regresses | O(cols) | `table_check.py:310` |
| `log_repair_pass` | Structured log: pass#, errors before/after, accepted flag, patch summary, latency | O(1) | `repair_log.py` |
| `build_indices` | Rebuilds ExtractionIndex for each clean table; silently skips error tables | O(rows) per clean table | `runner.py:51`, `runner.py:58` |

## Design notes

- **Always-on when react+auth; no-op otherwise.** `build_table_validator` in
  `mcg_swarm/subagent/__init__.py` returns `None` unless `MCG_SUBAGENT=react` and
  agent auth is available (`ANTHROPIC_API_KEY` or `claude` CLI). `orchestrate_table`
  only calls `review` when `table_validator is not None` (`orchestrator.py:179`).
- **Loop is strictly bounded.** At most `max_passes` iterations (default 3,
  set via `TableCheckPolicy.max_passes`). Early-exit on either: errors cleared
  (`break` at `table_check.py:288`) or no improvement at all (`break` at
  `table_check.py:290`). The loop never wastes a pass on a no-op.
- **Verify-before-accept never regresses.** `_accepts` enforces monotone error
  reduction — a candidate with more errors than the current best is silently dropped.
  Tie-breaking uses the year-aware label score (`_label_score`/`_is_label`) to
  recover a gate-blind header-span over-detection without accepting an unverifiable
  lateral change (`table_check.py:116-120`).
- **Sampling bounds gate cost.** `_reindex_and_check` calls `run_table_tests` which
  uses `select_sample` (`sampling.py:18`) for tables above `MCG_SAMPLE_FULL_THRESHOLD`
  (default 300 rows) — reads a stride-spread ~300-row sample rather than the full
  table. Total per-table gate cost is ≤ passes × sample_size reads (max 900 today).
  The `WorkbookSource` seam (`source.py`) is where a streaming reader removes this
  without changing extraction logic (see `OPTIMIZATIONS.md` #1 cost note).
- **Structural candidates try multiple spans.** The agent is unreliable at
  header-span arithmetic; `_structural_candidates` tries its span, span=1, and the
  original span, letting `_accepts` pick the winner (`table_check.py:168`).
- **Never raises.** The outer `try/except` at `table_check.py:292` catches all
  exceptions and returns the original table — the pipeline never breaks on a
  validator failure.
