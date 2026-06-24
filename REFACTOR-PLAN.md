# Refactor Plan — Readability & Structure

Assessment date: 2026-06-24. Baseline: 140 tests passing, 1 skipped.

## Verdict

The **core `mcg_swarm/` package is already good**. Clean layering, single entry
point, small focused files, no circular deps. No big-bang restructure warranted.

Issues are localized. This plan is **tiered by value/risk** — do Tier 1, consider
Tier 2, skip Tier 3 unless it starts hurting.

Current layering (keep as-is):
```
orchestration   runner.py → orchestrator.py
domain (pure)   formulas.py · resolve.py · merge.py · size_estimate.py
domain + IO     splitter.py · extraction.py · subagent.py · header_llm.py · testing.py
infrastructure  schemas.py · llm/client.py · env.py
```

---

## Tier 1 — High value, low risk (recommended)

### 1.1 Rename `mcg_swarm/testing.py`
**Problem:** It is production code (an in-loop quality gate called by the
orchestrator), but the name collides conceptually with the `tests/` suite. Anyone
new reads "testing" and assumes test scaffolding.
**Fix:** Rename to `quality_gate.py` (exports `run_table_tests`, `TableTestReport`).
Update the one importer (`orchestrator.py`) and `tests/test_testing.py` →
`tests/test_quality_gate.py`.
**Risk:** Trivial — pure rename, caught instantly by the test suite.

### 1.2 Split `eval/generator/specs.py` (687 LOC)
**Problem:** Largest file in the repo. 14 independent `wb_*()` workbook-spec
factories + shared types (`MeasureDef`, `FormulaDef`, `WorkbookSpec`, `_grid`).
**Fix:** Convert to a `specs/` package:
```
eval/generator/specs/
  __init__.py        # re-exports all_specs() + the dataclasses (back-compat)
  _model.py          # MeasureDef, FormulaDef, WorkbookSpec, _grid
  simple.py          # sales_regional, headcount, inventory, expenses, pricing
  financial.py       # quarterly_pnl, capex, consolidated_pnl, cashflow_signs
  multi_table.py     # multi_region_sales, store_ops, vendor_spend, segment_report, dup_tables
  large_ledger.py    # large_ledger (the 70-line outlier)
```
`__init__.py` keeps `from eval.generator.specs import all_specs` working unchanged.
**Risk:** Low — these are data factories with no logic; import path preserved.

---

## Tier 2 — Worthwhile, contained risk

### 2.1 Split `mcg_swarm/resolve.py` (356 LOC) into a package
**Problem:** One public function, but three concerns in one file: tokenisation,
per-table indexing, and the resolver. Also uses **module-global mutable caches
keyed by `id(catalog)`** (`_TABLE_INDEX_CACHE`, `_BOUNDED_WORD_CACHE`) — `id()`
reuse after GC is a latent correctness smell.
**Fix:**
```
mcg_swarm/resolve/
  __init__.py    # exports deterministic_resolve (back-compat path unchanged)
  tokens.py      # _tokenise*, _name_tokens, _bounded_pattern, _squash, _match_tier
  table_index.py # _get_or_build_table_index + the cache (or a small Resolver class
                 #   holding the cache as instance state, dropping the id()-keyed global)
  resolver.py    # deterministic_resolve
```
Optional but recommended: replace the `id()`-keyed module global with a cache held
on a `Resolver` object (or `functools.lru_cache` on hashable inputs). Keeps the free
function as a thin wrapper for back-compat.
**Risk:** Medium-low — 39 resolver tests guard behavior; pure reorganization.

### 2.2 Split `eval/adapters/swarm_adapter.py` (365 LOC)
`SwarmAdapter` mixes catalog building, LLM-fallback resolution, and extraction
delegation. Extract `_build_catalog`/`_build_full_catalog`/`_resolve_via_llm` into a
`catalog.py` helper module the adapter composes. **Risk:** Low-medium (283 tests
across two adapter test files guard it).

---

## Tier 3 — Optional / probably skip

- **Split pure-vs-IO in `splitter.py` / `extraction.py`.** Flagged by analysis, but
  both are <270 LOC and cohesive; separating algorithm from openpyxl adds indirection
  for little gain. Skip unless they grow.
- **`eval/verify.py` (333) / `hard_workbooks.py` (411).** Eval-only tooling, not
  shipped. Cohesive enough. Defer.
- **Split `schemas.py` by bounded context.** Only 64 LOC. Not worth it.

---

## Execution rules

- One Tier/item per branch+commit; run full `pytest` after each (must stay 140 green).
- Pure moves only — no behavior changes mixed into a rename/split.
- Preserve public import paths via `__init__.py` re-exports (no churn in callers/tests).
