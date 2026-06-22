# MCG Swarm Eval Pipeline (v2 — Independent Canonical Tables)

## Goal
A reproducible benchmark for the MCG swarm. It generates synthetic Excel workbooks
whose ground truth is fully known (because we author the data), then scores the swarm's
outputs — table-boundary detection, value extraction, measure detection/mapping, and
intra-table formula computation — against those known-correct labels, end to end. The
unit under test is a `WorkbookExtraction` of independent `CanonicalTable`s; there is no
cross-table dependency graph.

## Audience / who this is for
The engineer(s) building the swarm (deterministic splitter + per-table orchestrators +
size-driven fan-out + merge/test/repair). They need a hard signal for "did the system
read the spreadsheet correctly and produce canonical tables whose extraction scripts
return the right number," plus a view of *where* it breaks as workbook complexity rises.

## Success looks like
`python eval/run_benchmark.py --adapter oracle` runs all synthetic workbooks through the
harness and produces a JSON scorecard, a console summary, and an HTML dashboard. The
reference oracle scores ~100% (proving labels are internally correct and the scorer
works); a real swarm adapter, plugged into the same interface, gets a per-capability,
per-file, per-tier breakdown.

## Scope
- In scope: synthetic workbook generator (15 graded, realistic-messy files); ground-truth
  label sidecars; 20-30 mixed validation samples per workbook; an adapter interface the
  swarm plugs into; a reference oracle adapter; scoring for 4 capabilities; JSON + console
  + HTML reporting; a label self-verifier.
- Out of scope: implementing the swarm itself (we ship a stub adapter with wiring notes);
  the LLM runtime; cost/latency benchmarking.
- Maybe later: golden real-world workbooks; regression tracking across runs; CI wiring.

## Key decisions made during this interview
- **Integration boundary = adapter interface.** Harness talks to a thin `EvalAdapter`
  protocol; the swarm plugs in behind it. A reference oracle adapter ships so the pipeline
  is testable today, before the swarm is wired.
- **Benchmark all four capabilities:** table boundaries, measure & value extraction,
  measure detection & mapping, intra-table formula correctness.
- **Mixed sample bundle** per workbook: structured extraction, natural-language semantic,
  table-boundary, and formula/aggregate samples.
- **Graded tiers (easy/medium/hard), themed as realistic** sales/finance/ops/HR workbooks
  so the mess resembles real spreadsheets, not toy data.
- **Output = JSON + console + HTML**, all under `eval/`, driven by a `run_benchmark` CLI.

## Correctness guarantee
Every label is derived from the same in-memory data used to write the xlsx, then a
verifier re-opens each xlsx and asserts that every labeled cell, region, measure, and
sample matches what is physically in the file. Formula samples are recomputed from labeled
operands. The expected results are therefore correct by construction and double-checked.

## v2 migration status
The framing is v2 (independent canonical tables, no cross-table dep-graph). The
generator, labels, scorer and oracle still run on the v1 label schema (a single
`business_logic` string + one formula per workbook) so the oracle harness stays green.
Pending code adjustments (per `../SWARM-v2-canonical-tables.md`): scope formula scoring
to intra-table only, drop `business_logic` as a driver, and add a whole-table
coverage-invariant check.

## Open questions
- Real swarm adapter wiring (depends on the orchestrator's final API) — left as a
  documented stub in `eval/adapters/swarm_adapter.py`.
- Whether to add fuzzy string matching for semantic-query variable mapping (currently
  alias-based exact match) — revisit once the orchestrator's column naming is observed.

## Suggested next steps
1. Run `python eval/run_benchmark.py --adapter oracle` and open the HTML dashboard.
2. Inspect a couple of generated workbooks + their label JSON to confirm the mess matches
   real files you care about.
3. Implement `SwarmAdapter` against the orchestrator and re-run.
