# MCG Swarm Eval Pipeline (v2 — Independent Canonical Tables)

An end-to-end benchmark for the MCG swarm. It generates synthetic Excel workbooks whose
ground truth is known by construction, then scores the swarm's outputs against those
labels across four capabilities. The unit under test is a `WorkbookExtraction` of
independent `CanonicalTable`s (see [`../SWARM-v2-canonical-tables.md`](../SWARM-v2-canonical-tables.md)) —
each tab is one table and there is no cross-table dependency graph.

| Capability | What it checks | Metric |
|---|---|---|
| **Table boundaries** | Did the splitter/orchestrator find each table and get its cell range right? | cell-range IoU ≥ 0.999 |
| **Value extraction** | Does `query(row, column)` return the right cell value? | value match (float tolerance) |
| **Semantic extraction** | Does a natural-language query resolve to the right value? | value match |
| **Formula (intra-table)** | Does an intra-table formula (computed column or total) compute the right number? No cross-table composition. | value match (rel tol 1e-6) |
| **Measure detection** | Did it surface each relevant measure mapped to the right cell? | precision / recall / F1 |

> **v2 migration status.** The framing here is v2 (independent canonical tables, no
> cross-table dep-graph). The generator, labels, scorer and oracle still run on the v1
> label schema — each workbook label carries a single `business_logic` string and one
> formula, kept so the oracle harness stays green. Pending code adjustments (per
> `../SWARM-v2-canonical-tables.md`): scope formula scoring to intra-table only, drop
> `business_logic` as a driver, and add a whole-table **coverage-invariant** check
> (every `(row_key, column)` directly addressable, no search).

## Quick start

```bash
# from the DIM repo root
python eval/run_benchmark.py --build --adapter oracle   # generate data + run reference
python eval/verify.py                                    # prove labels are correct
python eval/run_benchmark.py --adapter noisy             # demo: scorer discriminates
open eval/results/report_oracle.html                     # HTML dashboard
```

The **oracle** adapter answers from the labels, so it scores ~100% — that proves the
labels are internally consistent and the scorer is correct. The **noisy** adapter
injects controlled errors so you can see sub-100% per-capability breakdowns.

## The 15 workbooks (graded, realistic-messy)

- **Easy (5)** — single clean table per sheet: `sales_regional`, `headcount_dept`,
  `inventory_snapshot`, `monthly_expenses`, `product_pricing`.
- **Medium (5)** — titles, offset anchors, blank-row gaps, units in headers, totals
  rows/cols, multiple tables per sheet: `quarterly_pnl`, `multi_region_sales`,
  `store_ops`, `vendor_spend`, `capex_plan`.
- **Hard (5)** — two-level merged headers, parenthesised-negative sign traps,
  numbers-as-text + footnote markers, duplicate tables across sheets (canonicalisation),
  and a 12,000-row ledger with a derived aggregate table: `consolidated_pnl_multiheader`,
  `cashflow_signs`, `segment_report`, `dup_tables`, `large_ledger`.
- **Extreme (3) — logic lives *inside* Excel.** Live formulas, cross-sheet
  references, named ranges, chained dependencies, SUM/SUMIF aggregation, percentage
  formulas and display sign-formats:
    - `formula_chain_pnl` — Units→Revenue→Gross Profit→EBIT→Tax→Net Income chained
      formulas, `SUM()` FY totals, a `TaxRate` **named range**.
    - `cross_sheet_model` — `Inputs`/`Calc`/`Summary` sheets where Calc cells are
      `=Inputs!…` formulas using an `FXRate` named range and Summary sums Calc
      **across sheets**.
    - `messy_everything` — the kitchen sink: two-level merged header + cross-sheet
      formulas + `SUMIF` over a ledger + a percentage formula + parenthesised-negative
      number format + offset anchor.

  These files are **recalculated by LibreOffice headless** during `--build` so they
  carry cached results, exactly like a real saved workbook. A reader that ignores
  cached values or can't follow references fails; a correct extractor returns the
  recalculated number.
- **Extreme-scale (1) — forces orchestrator segmentation.** `enterprise_transactions` is a
  **~100,000-row × 22-column** transaction fact table on one sheet (~2.2M cells),
  plus three derived summary tables (by region / month / category) on their own
  sheets. The primary sheet is far too large for a single pass, so the
  orchestrator must split it into row bands — **≥2 subagents** for that one
  table alone, more across the summary sheets. Samples include deep "needle" lookups
  (e.g. row 99,987) and aggregate reconciliations (company net revenue = Σ regions).

Each workbook ships **20–30+ validation samples** (503 total) mixing the four sample
types. Every expected value is verified against the physical xlsx by `verify.py`
(2,951 checks) — including that each formula cell really holds a formula with the
correct cached result, and that the 100k-row summary aggregates recompute exactly
from the full ledger (streamed read-only).

> Requires `libreoffice`/`soffice` on PATH for the extreme workbooks' recalc step.

## How to benchmark *your* swarm

The harness never imports the swarm — it talks to an `EvalAdapter`
(`eval/adapters/base.py`). Implement `eval/adapters/swarm_adapter.py`:

1. `prepare(workbook_path, label)` — run your orchestrator once; cache the produced
   `WorkbookExtraction` (its independent `CanonicalTable`s) on `self` (don't read
   answers from `label`).
2. `table_region` — return the A1 range of a canonical table (from the deterministic
   splitter / per-table orchestrator).
3. `extract` — call the table's extraction script `query(row, column)`.
4. `answer_semantic` — your NL → column/row → value path.
5. `detected_measures` — one `DetectedMeasure` per canonical column/field.
6. `compute_formula` — evaluate an intra-table formula (computed column or total)
   end-to-end.

Then: `python eval/run_benchmark.py --adapter swarm`.

## Layout

```
eval/
  PLAN.md              decisions behind this pipeline
  schemas.py           ground-truth label schema (pydantic)
  util.py              safe formula eval, cell-range IoU, value compare
  generator/
    tables.py          table renderer (xlsx + labels from one source of truth)
    specs.py           the 15 spec-driven workbook definitions
    sampling.py        shared measure/sample assembly + cell pruning
    hard_workbooks.py  3 extreme workbooks (live formulas / refs / named ranges)
    scale_workbook.py  the 100k-row enterprise_transactions workbook
    build.py           writes data/workbooks/*.xlsx + data/labels/*.json
  adapters/
    base.py            EvalAdapter interface (the swarm boundary)
    oracle.py          reference + noisy reference adapters
    swarm_adapter.py   stub to wire your orchestrator + adapter registry
  harness/
    runner.py          runs an adapter, scores samples, aggregates
    report.py          console + self-contained HTML
  verify.py            re-reads every xlsx; proves labels correct
  run_benchmark.py     CLI
  data/                generated workbooks + labels (after --build)
  results/             scorecards + HTML reports
```

## Regenerating

Data generation is deterministic (seeded). `python -m eval.generator.build` rewrites
`data/`. To add a workbook, add a builder to `generator/specs.py::ALL_BUILDERS`;
samples and labels are derived automatically and checked by `verify.py`.
