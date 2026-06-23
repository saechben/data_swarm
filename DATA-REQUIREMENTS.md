# MCG Swarm — Data Requirements & Assumptions

**Living document.** What the swarm assumes about input Excel workbooks, what it supports,
what it does **not** support (with evidence from the eval corpus), and the tunable limits.
Update this whenever an assumption changes or a new failure mode is found.

_Last updated: 2026-06-23 (Pattern C / 2-row headers landed). Measured against the 19-workbook eval corpus._

---

## 1. Core assumptions (the swarm relies on these)

| # | Assumption | Why | If violated |
|---|---|---|---|
| A1 | **One table per worksheet tab.** Each sheet holds exactly one logical table. | The file→table split is mechanical (one orchestrator per tab). | Only the first/topmost table on the tab is captured; others are silently missed. |
| A2 | **A single, detectable header row.** One row of column labels (a title/units banner directly above it is tolerated). | Column names + types come from this row. | Header mis-detected → wrong columns/region → extraction fails. |
| A3 | **Vertical orientation.** Rows = records, columns = fields. | Row-key→row, column-name→column resolution. | Transposed/matrix tables resolve the wrong axis → extraction fails. |
| A4 | **A key column identifies rows.** The first non-empty column holds unique row identifiers. | `query(row, column)` resolves rows by key value. | Duplicate/blank keys collide or can't resolve; positional fallback only. |
| A5 | **Header column names are unique** within the table. | Column→physical-column map is keyed by name; the in-loop gate fails loud on duplicates. | Duplicate names → gate rejects the table (returned with `errors`, no index). |
| A6 | **Values are readable from the live file** (openpyxl `data_only` — cached formula results present). | `query()` reads the live cell each call. | Workbook never opened in Excel/LibreOffice → formula cells may read `None`. |
| A7 | **Tables are independent** (no cross-table/cross-sheet references in the canonical model). | v2 emits independent canonical tables by design (spec §2, §13). | Cross-table formulas are out of scope; not modeled. |
| A8 | **Header is at most 2 rows.** A single header row, or a group-row + leaf-row pair, is supported (composite naming). | Column names derive from the header span. | Headers spanning **3+ rows** fall back to placeholder names for the unlabeled cells (degraded — see §3). |

---

## 2. Supported (works deterministically, no LLM)

- One clean vertical table per tab with a single header row. ✅
- **Title/units banner row above the header** (e.g. a merged title spanning the table width) — the region includes it; the header is still located correctly.
- **Left-offset tables** (table starts at column B/row 2, leading empty columns) — trimmed correctly.
- **Trailing stray cells** beyond a gap column (e.g. a lone `FXRate`/`TaxRate` parameter to the right) — excluded from the table.
- **Large tables** (100k+ rows) — fan-out by row bands; extraction + boundaries scale.
- **Two-row headers** (a sparse "group" row above a "leaf" row, e.g. `EMEA`/`APAC` over `Actual`/`Budget`) — composite column names via "bottom row, else nearest non-empty above". Detected deterministically; data-row misclassification is guarded (a header row must be pure string labels). `header_span` is carried through the extraction index and the in-loop gate.
- Live reads: editing a cell changes `query()` output with no re-run.

**Measured (14 in-scope workbooks, no LLM):** table boundaries **100% (22/22)**, value extraction **100% (199/199)**.

---

## 3. NOT supported / degraded (with corpus evidence)

| Case | Corpus example(s) | Behavior | Status |
|---|---|---|---|
| **Multiple tables on one tab** | `multi_region_sales` (2 on `Sales`), `quarterly_pnl` (2 on `P&L`), `segment_report` (2 on `Segments`), `store_ops` (2 each on `Store 1`/`Store 2`) | Only the first table is captured; the rest are missed. | **Excluded** from swarm scoring (violates A1). |
| **Transposed / matrix orientation** | `cashflow_signs` (`Summary` tab) | Wrong axis resolved. | **Excluded** (violates A3). |
| **3+ row hierarchical headers** | — (none in corpus) | Cells unlabeled across the whole span get placeholder letter-names. | **Degraded** (A8 supports up to 2 rows). 2-row headers (`consolidated_pnl_multiheader`, `messy_everything`) are now **fully supported** — see §2. |
| **Pivot tables** | — | Not a single clean table. | Not supported; should fail loud per spec §5. |
| **Semantic name → cell mapping** (NL queries; formula operands like `revenue_emea`) | all `semantic` + `formula` eval samples | Requires the LLM resolver. | **Built & unit-tested; blocked on a valid, funded `ANTHROPIC_API_KEY`** (currently 0% live). |
| **Cross-table / cross-sheet dependencies** | `cross_sheet_model` (cross-sheet formulas) | Not modeled; each table independent. | Out of scope (spec §13). |

### Measure detection (specific limits)
- "Measures" (metric cells) are only emitted for **summary/metric tables ≤ `MEASURE_MAX_TABLE_ROWS` (40) rows**. Large raw-data tables (transactions, ledgers) are skipped — they carry no labeled measures and would flood false positives.
- Even on summary tables, the swarm emits **all numeric value cells**; the eval labels mark only an arbitrary subset, so precision is inherently capped (~19% measured) while recall is high (~85%). Identifying *which* cells are "the measures" needs semantic understanding (LLM).

---

## 4. Failure behavior (what happens on a violation)

- A tab that can't be resolved to one clean table is returned as a `CanonicalTable` **stub with `errors` populated** and is **not marked passing** — and the other tabs in the file still process (one bad tab never fails the whole file).
- The swarm **never raises** out of orchestration; unresolvable inputs become `errors`, not crashes.
- A table that fails the in-loop test gate (coverage / round-trip / column-or-row integrity / computed) is returned **with `errors` and no extraction index** — downstream `query()` for it returns `None`.

---

## 5. Tunable constants (defaults)

| Constant | Default | Meaning |
|---|---|---|
| `ROWS_PER_AGENT` | 5,000 | Row-band size for fan-out. |
| `COLS_PER_AGENT` | 40 | Column-pressure threshold (wide-table fan-out). |
| `K_MAX` | 4 | Max subagent bands per table (was 16; lowered for LLM-call cost/latency). |
| `MEASURE_MAX_TABLE_ROWS` | 40 | Tables larger than this emit no measures. |
| `MEASURE_ROW_CAP` | 200 | Hard cap on measure rows per table. |

---

## 6. Eval corpus scope

- **19 workbooks total.** **14 in-scope** (conform to A1–A3). **5 excluded** for violating the one-table-per-tab / vertical assumptions: `multi_region_sales`, `quarterly_pnl`, `segment_report`, `store_ops`, `cashflow_signs`. (Files retained so the oracle adapter stays at 100%; excluded only from swarm scoring.)
- The downstream consumer of the extraction (the pricing agent) calls `query(row, column)` / intra-table formulas through the produced scripts and **never loads the spreadsheet data into its context** — this abstraction is the swarm's purpose.
