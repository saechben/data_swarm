# Formula Extraction with Downstream Context — Design

**Date:** 2026-06-29
**Status:** Approved (design), pending implementation plan
**Goal:** Populate `CanonicalTable.formulas` so downstream agents can see *how* a
computed value is derived — both an executable binding and a human-readable hint —
and re-activate the dormant quality-gate self-check for computed columns.

---

## 1. Problem & Context

The system extracts spreadsheets so downstream agents can consume them. A computed
value with no visible formula is a black box — exactly what this project exists to
avoid. Today the formula pipeline is half-built:

- **Consumer side is complete and dormant:**
  - `mcg_swarm/formulas.py` — safe AST evaluator (`+ - * / ** //`, comparisons,
    `IF`, `SUM/AVG/MIN/MAX/COUNT/ABS/ROUND`), plus `parse_ast` and `build_env`.
  - Gate Phase 4 (`quality_gate.py:294-323`) recomputes every `role="computed"`
    column from its `TableFormula` and flags mismatches via `index.query` /
    `query_cell` / `query_range` (all exist: `extraction.py:80,96,108`).
  - `merge.py` dedups formulas by `(target, expression)`.
- **Producer side is missing.** A `_detect_formulas` once existed but was deleted
  in commit `b77195b` (latent `cell.coordinate`/`EmptyCell` crash in read-only
  mode), and even it only *flagged* formulas as anomalies — it never translated
  them. The translation layer (Excel A1 → operand-binding model) has never existed.
- `_analyze_band_single_open` (`subagent/static.py:32`) hardcodes `formulas=[]`.

**Scoping pivot:** the benchmark already scores formula *computation* through
`SwarmAdapter.compute_formula(wb, expression, operands)`, where the recipe comes
from ground-truth `FormulaDef`, **not** from our extraction. So extracting
`CanonicalTable.formulas` does not change the eval score directly — its value is
transparency for downstream agents, plus re-activating the gate self-check. We do
not over-build for a score that is already covered.

### Two load-bearing constraints

1. **The evaluator runs a named-operand grammar, not Excel syntax.** `=B2*C2` must
   become `expression="Units*Price"` with `operands=[{name:Units, source:column,
   ref:Units}, {name:Price, source:column, ref:Price}]`. This requires mapping A1
   cell coords back to detected columns/row-keys.
2. **`WorkbookSource` cannot see formulas.** `read_region`/`read_cell` open with
   `data_only=True` (computed values only). Formula strings need a
   `data_only=False` read.

### De-risked mechanism

`load_workbook(data_only=False, read_only=True)` + `iter_rows(values_only=True)`
returns formula strings as plain row-tuple values; coordinates come from `enumerate`
offsets, so `cell.coordinate`/`EmptyCell` is **never** touched. Verified against
`eval/data/workbooks/formula_chain_pnl.xlsx` (`=SUM(B2:E2)`, `=B2*B3`, …). The old
crash is structurally impossible with this approach.

---

## 2. Scope

**Phase 1 (this design): intra-table arithmetic on vertical tables**, architected so
cross-sheet / named-range / SUMIF / transposed orientation is a pure extension.

- **Translated (executable, `role="computed"`, gate-verified):**
  - same-row column arithmetic (`=B2*C2` → `Units*Price`)
  - in-table `SUM` over a contiguous range mapped to known columns
- **Captured but not translated (visible, `role` stays `value`, gate skips):**
  - cross-sheet refs (`=Inputs!B2`), named ranges (`TaxRate`), `SUMIF`,
    transposed-orientation refs, any unsupported function

Untranslatable formulas are **recorded, never dropped**: emitted as a
`TableFormula` with empty operands + populated `context`, plus a `provisional_notes`
line on the table. Phase 2 extends translation against the same interfaces with no
rework.

---

## 3. Schema Change — Contextualization

Add one optional field to `TableFormula` (`mcg_swarm/schemas.py:19`):

```python
class TableFormula(_Base):
    target: str
    expression: str
    operands: list[OperandBinding] = Field(default_factory=list)
    ast: Optional[dict] = None
    context: Optional[str] = None   # NEW: human-readable hint for downstream agents
```

`context` is the downstream "hint":
e.g. *"Gross Profit is computed as Revenue − COGS (same-row columns)."*

- **Baseline:** deterministic gloss synthesized from `target` + operands + source
  kinds. Always present, zero-cost, never hallucinated.
- **Optional enrichment:** when an LLM is configured, the existing header-verify
  call in `run_static` may enrich the gloss into natural language. Deterministic
  first, LLM optional — same discipline as the rest of the codebase.

For untranslated formulas, `context` explains *why* it could not be translated
(e.g. *"References sheet 'Inputs' — cross-sheet formulas not yet executable; shown
for reference."*).

`_Base` uses `extra="forbid"`, so adding the field is required for it to be
accepted; it is backward-compatible (defaults to `None`).

---

## 4. Producer Location — Orchestrator Step, Per-Table

Formula extraction runs **once per table in the orchestrator, after merge** — not
per band.

Rationale:
- Translation needs table-level context (full column layout, key column, region,
  orientation) that a single band does not cleanly own.
- One `data_only=False` open per table instead of one per band — respects the perf
  concern that retired the old per-band code. `_analyze_band_single_open` stays
  single-open and untouched.
- It is where `merged.formulas` already flows into `CanonicalTable`
  (`orchestrator.py:128,146`).

New module `mcg_swarm/formula_extract.py`, pure function:

```python
def extract_formulas(
    source: WorkbookSource,
    sheet: str,
    region: str,            # table region, e.g. "A1:F12"
    columns: list[ColumnSpec],
    header_row: int,
    orientation: str,       # "vertical" | "transposed"
) -> tuple[list[TableFormula], list[str]]:
    """Returns (formulas, provisional_notes). Never raises."""
```

The orchestrator calls it after building the merged column set, then merges its
output into the table's `formulas` and `provisional_notes`. **`extract_formulas`
owns the `role` upgrade**: it mutates the `role` of any `ColumnSpec` in the passed
`columns` list whose formula fully translates (it is the only place that knows which
targets translated). The orchestrator just wires inputs/outputs.

---

## 5. WorkbookSource Extension

Add one method to the Protocol and both concrete impls
(`mcg_swarm/source.py`):

```python
def read_formula_region(self, sheet, min_row=None, min_col=None,
                        max_row=None, max_col=None) -> list[tuple]: ...
```

- `OpenpyxlFileSource`: `load_workbook(data_only=False, read_only=True)` +
  `iter_rows(values_only=True)`, close in `finally` (mirrors `read_region`).
- `SnapshotSource`: delegates to inner (formula strings are not cached; the
  snapshot only short-circuits computed-value `read_cell`).
- `as_source` unchanged.

---

## 6. Translation Algorithm (Phase 1)

Operating on one representative data row of the table:

1. Read that row's formula strings via `read_formula_region`.
2. For each formula cell, extract A1 tokens from the Excel string: single refs
   (`[A-Z]+[0-9]+`, optionally `$`-anchored) and ranges inside `SUM(...)`
   (`A1:A9`). Detect disqualifiers up front: `!` (cross-sheet), bare names
   (named ranges), unsupported function calls.
3. Resolve each single A1 ref to a column: its column letter must match a known
   table column position **and** its row must equal the formula cell's row
   (same-row guard → vertical intra-table). Map to
   `OperandBinding(source="column", ref=<col name>)`. A `SUM(range)` whose columns
   are all in-table maps to `source="range"`.
4. Rewrite the Excel expression into engine grammar by substituting each A1 token
   with its operand name (`=B2*C2` → `Units*Price`). Validate with `parse_ast`;
   store the resulting AST in `TableFormula.ast`.
5. If any token fails the guard (cross-sheet, named range, transposed, unsupported
   fn): **bail for that one formula** — emit a `TableFormula` with empty operands,
   `role` stays `value`, populate `context` with the reason, append a
   `provisional_notes` line. No crash, no silent drop.
6. Mark the target column `role="computed"` **only** when fully translated — so the
   gate's Phase 4 sees exactly the formulas we can verify.
7. Build `context` for every formula (deterministic gloss; optional LLM enrichment).

---

## 7. Error Handling & Invariants

- **Never raises.** Consistent with the system-wide "errors surface in lists" rule.
  Any failure → `provisional_notes` / table `errors`; deterministic columns stand.
- **Gate Phase 4 safety.** It only ever sees fully-translated formulas (role gate),
  so re-activation cannot introduce false failures on untranslatable formulas.
- **Merge unchanged.** Dedup by `(target, expression)` already handles multi-band.
- **No formula → no behavior change.** Tables without formulas get `formulas=[]`,
  identical to today.

---

## 8. Testing (TDD)

- **`formula_extract` unit tests:**
  - `=B2*C2` → `expression="Units*Price"` + two column operands + AST.
  - `=SUM(B2:E2)` → range operand.
  - cross-sheet `=Inputs!B2` → untranslated, `context` set, note appended,
    `role` stays `value`.
  - named range `=EBIT*TaxRate` → untranslated with reason.
  - transposed orientation → untranslated with reason.
- **`read_formula_region` tests:** returns formula strings; **regression test** on a
  region containing empty cells (guards the old `EmptyCell` crash).
- **End-to-end** on `formula_chain_pnl.xlsx`: `CanonicalTable.formulas` populated
  with `context`; gate stays green.
- **Gate Phase 4 reactivation:** computed column with a correct formula passes; a
  deliberately wrong expression fails — proving the self-check is live.
- Full suite stays green (currently 222 passed / 2 skipped).

---

## 9. Out of Scope (Phase 2+)

- Cross-sheet reference resolution (`=Inputs!B2` → `source="cell"` cross-sheet A1).
- Named-range table + resolution (`TaxRate` → `source="param"`/`"cell"`).
- `SUM` ranges spanning sheets; `SUMIF` and other conditional aggregates.
- Transposed-orientation translation (refs running down a column).
- LLM-driven translation of arbitrary Excel formulas.

All extend the Phase 1 interfaces (`extract_formulas`, `read_formula_region`,
`TableFormula.context`) without rework.

---

## 10. Touched Surface

| File | Change |
|------|--------|
| `mcg_swarm/schemas.py` | add `TableFormula.context` |
| `mcg_swarm/source.py` | add `read_formula_region` to Protocol + both impls |
| `mcg_swarm/formula_extract.py` | **new** — `extract_formulas` + A1 translation |
| `mcg_swarm/orchestrator.py` | call `extract_formulas` per table; merge results; upgrade roles |
| `tests/` | new unit + e2e + regression + gate-reactivation tests |
