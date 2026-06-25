# MCG Swarm — Usage Guide

**Status: current as of 2026-06-24.** This document explains what the MCG swarm is,
how to connect to it (both as a downstream consumer and as an intelligence backend),
the input it expects and the assumptions it makes, the output it produces, and how to
run it. For the authoritative list of data assumptions and unsupported cases, see
[`DATA-REQUIREMENTS.md`](DATA-REQUIREMENTS.md).

---

## 1. What it is

The MCG (Model-Card Generation) swarm ingests an Excel workbook and emits a
`WorkbookExtraction` — a set of **independent canonical tables**, each exposing a
deterministic `query(row, column)` extraction layer that reads the **live file**.

The whole point is abstraction: a downstream **pricing agent** answers questions by
calling `query(...)` through the produced index and **never loads spreadsheet data into
its own context**. The swarm turns "a messy workbook" into "a small, typed, queryable
structure" so the consumer reasons over coordinates and values, not raw cells.

```
Excel workbook ──▶ MCG swarm ──▶ WorkbookExtraction (structure only)
                                      │
                                      ▼
                         ExtractionIndex.query(row, col) ──▶ live cell value
                                      ▲
                            pricing agent calls this; never sees the raw grid
```

### Pipeline (tiers)

1. **Tier-0 splitter** (`splitter.py`) — deterministically locates one table per sheet:
   header row, A1 region, columns, 1- or 2-row composite headers, title banners,
   left-offset and gap-column trimming.
2. **Tier-1 orchestrator** (`orchestrator.py`) — per table: plan bands → dispatch
   subagents → merge → build index → run the in-loop test gate → return. **Never
   raises**; failures land in `CanonicalTable.errors`.
3. **Tier-2 subagents** (`subagent.py`) — deterministic-first column/type analysis,
   with an optional LLM pass to verify headers / fill units & roles.
4. **Extraction index** (`extraction.py`) — O(1) name→cell resolution, reopen-per-read
   for live values.
5. **Quality gate** (`testing.py`) — coverage / round-trip / column & row integrity /
   computed checks. A table that fails is returned **with errors and no index**.

Everything works **with no LLM** (deterministic). An LLM is optional and only used for
messy-tab fallback and header verification.

---

## 2. Requirements & setup

- **Python 3.11**, **pydantic v2**, **openpyxl 3.1.5**, pytest — all pinned in the
  project virtualenv at `.venv/`.
- **Always use `.venv/bin/python`** (the base/anaconda interpreter has pydantic v1 and
  will crash the harness).

```bash
# from the repo root
.venv/bin/python -m pytest -q          # 186 passed, 2 skipped
```

No network or API key is required for the deterministic path.

---

## 3. Connecting to it

There are two distinct "connections": **(A)** a downstream consumer connecting to the
swarm's *output*, and **(B)** the swarm connecting to an *intelligence backend*.

### A. Consume the output — `run_swarm` → `build_indices` → `query`

```python
from mcg_swarm.runner import run_swarm, build_indices

path = "path/to/workbook.xlsx"

# 1. Run the swarm (no LLM = fully deterministic).
extraction = run_swarm({"main": path}, llm=None)

# 2. Build the query indices (one per successfully-extracted table).
#    Returns {table_id: ExtractionIndex}. Failed tables are skipped.
indices = build_indices(path, extraction)

# 3. Query by logical row key + column name. Reads the LIVE cell each call.
idx = indices["Sheet1__0"]              # table_id is f"{sheet}__{index}"
cell = idx.query(row="EMEA", column="Revenue")
print(cell.value, cell.cell_ref, cell.dtype, cell.unit, cell.is_computed)
# 1234.5  'C4'  'number'  'USD'  False
```

`ExtractionIndex` API:

| Method | Returns | Notes |
|---|---|---|
| `query(row, column)` | `ExtractedValue` | Raises `KeyError` on unknown row/column. Reopens the file → reflects edits with no rebuild. |
| `query_cell(a1)` | `ExtractedValue` | Read a raw A1 cell. |
| `query_range(a1)` | `list[ExtractedValue]` | Read an A1 range. |
| `read_all(max_rows=None)` | `list[(row_key, col_name, value, cell_ref)]` | One workbook open for the whole table (batch). |
| `row_keys()` | `list` | All row identifiers (key-column values, or 1-based positions if no key). |
| `column_names()` | `list[str]` | All column names in physical order (key column first). |

**Live-read guarantee:** `query()` opens a fresh read-only handle each call, so editing a
cell in Excel/LibreOffice changes the result with no re-run. (Formula cells need the
workbook to have been saved by a spreadsheet app at least once so cached results exist —
see assumption A6.)

### B. Connect an intelligence backend — the `LLMClient` interface

The swarm's "intelligence component" is a single injected interface. The abstraction is
**"call something → get the filled-out output schema"**:

```python
class LLMClient(Protocol):
    def complete(self, system: str, user: str, *, schema: Optional[Type[BaseModel]] = None) -> dict: ...
```

Pass any implementation via `llm=` to `run_swarm`. The swarm never branches on which
backend it is, so a **one-shot LLM**, a stubbed fake, or a future **multi-step / ReAct
agent** are all interchangeable — as long as `complete()` returns the requested schema.

```python
from mcg_swarm.llm.client import AnthropicClient
import os

# One-shot LLM backend (used for messy-tab fallback + header verification).
llm = AnthropicClient(model="claude-haiku-4-5-20251001") if os.environ.get("ANTHROPIC_API_KEY") else None
extraction = run_swarm({"main": path}, llm=llm)
```

**Schema enforcement (boundary contract).** When a call passes a Pydantic-model
`schema`, the response is **validated at the client boundary** before it returns; a
malformed response raises `LLMSchemaError`. All swarm callers wrap `complete()` in
try/except, so a bad response degrades to the deterministic path rather than crashing
downstream. This is centralized in `_SchemaEnforcedClient.complete()`, so every backend
(one-shot, fake, agentic) inherits enforcement for free — implement only `_raw_complete`.

Built-in implementations (`mcg_swarm/llm/client.py`):
- `AnthropicClient(model=..., api_key=...)` — real API, lazy-imports `anthropic`.
- `FakeLLMClient(responses)` — scripted dicts or a callable; for tests, no network.

### C. Opt-in ReAct subagent — `MCG_SUBAGENT=react`

How each table *band* is analyzed sits behind the `Subagent` port
(`mcg_swarm/subagent/`, `analyze(task) -> SegmentReport`); the swarm is unaware which
strategy runs. `run_swarm` selects it from the `MCG_SUBAGENT` env var:

- `static` (default) — deterministic column inference + the one-shot header-verify above.
  No extra dependency; behavior unchanged.
- `react` — an SDK-backed ReAct agent that checks static at **two points**, using
  read-only probe tools (`peek_rows`, `tail_rows`, `column_values`, `header_candidates`,
  `peek_region`) and returning column corrections. Any agent failure falls back to the
  static result — it never breaks the pipeline.
  - **band level** (`subagent/escalating.py`) — during slice analysis; corrects column
    dtype/role/unit. It cannot restructure, but any whole-table problem it *notices* (e.g.
    a header mis-detection) is recorded as an anomaly and forwarded to the table level.
  - **table level** (`subagent/table_check.py`) — over the fully-assembled table, so it
    sees whole-table failures (messy headers, merge conflicts, quality-gate failures). It
    can **recover a mis-detected header**: re-pick the header row/span and rename columns,
    then rebuild the index.

  At both points two triggers apply, additively:
  - **failure fallback — always on:** if static looks problematic / the table came back
    with errors, the agent runs. Not configurable.
  - **validation — configurable:** the agent also double-checks otherwise-clean tables.
    On by default; set `MCG_REACT_VALIDATE=off` to run the agent only on failures.

  The table level is **verify-before-accept**: every proposal (metadata fix *or*
  structural rebuild) is materialised into a candidate, re-indexed, and re-run through the
  quality gate. A candidate replaces the original only when it is provably better — fewer
  gate errors, or (on a tie) a higher year-aware header *label score*. So the agent can
  never regress a good table; a gate-blind header over-detection is still recovered
  because real labels beat data-as-header, and the gate's column-name check stops the
  agent inventing names. Header-span arithmetic from the agent is treated as a hint only
  (several spans are tried; the best-verifying one wins).

  The ≤ `REACT_MAX_TABLE_ROWS` (40) size guard applies throughout: large data tables are
  never sent to the agent (static is reliable there, and it would be slow/costly).

```bash
pip install claude-agent-sdk          # optional; only needed for react mode
MCG_SUBAGENT=react .venv/bin/python eval/run_benchmark.py --adapter swarm
```

`react` authenticates either via `ANTHROPIC_API_KEY` or — with no key set — via a
logged-in `claude` CLI (subscription auth). If the SDK is absent and neither auth path is
available, `react` logs once and degrades to `static`, so enabling it is always safe. The
probe tools are framework-agnostic (`mcg_swarm/subagent/tools.py`); only `sdk_runner.py`
imports the Claude Agent SDK.

---

## 4. Expected input & data assumptions

**Input:** one `.xlsx` workbook path, passed as `run_swarm({"main": path})`.

The swarm assumes (summarized — full detail and corpus evidence in
[`DATA-REQUIREMENTS.md`](DATA-REQUIREMENTS.md)):

| # | Assumption |
|---|---|
| A1 | **One table per worksheet tab.** Only the topmost table on a tab is captured. |
| A2 | **A detectable header row** (a title/units banner directly above is tolerated). |
| A3 | **Vertical orientation** — rows = records, columns = fields. |
| A4 | **A key column** — the first non-empty column holds the row identifiers. |
| A5 | **Unique column names** within a table (the gate fails loud on duplicates). |
| A6 | **Values readable from the live file** (cached formula results present). |
| A7 | **Tables are independent** — no cross-table/cross-sheet references modeled. |
| A8 | **Header is at most 2 rows** (single row, or group-row + leaf-row composite). |

**Supported deterministically:** clean vertical tables; title/units banners; left-offset
tables; trailing stray cells beyond a gap column; 100k+ row tables (band fan-out);
two-row composite headers.

**Not supported / out of scope:** multiple tables per tab; transposed/matrix orientation;
3+ row hierarchical headers (degraded); pivot tables; cross-table/cross-sheet formulas.
These either get captured partially or are returned as an error stub — see §6.

---

## 5. Output

`run_swarm` returns a `WorkbookExtraction` (all models are pydantic v2, in
`mcg_swarm/schemas.py`):

```
WorkbookExtraction
├─ workbook: str                  # file basename
├─ sheets: list[str]
├─ generator_version: str         # "mcg-swarm-v2.0.0"
├─ errors: list[str]              # file-level errors (e.g. unreadable workbook)
└─ tables: list[CanonicalTable]
       ├─ table_id: str           # f"{sheet}__{index}"
       ├─ sheet, region, header_row, header_span
       ├─ orientation: "vertical" | "transposed"
       ├─ columns: list[ColumnSpec]      # name, dtype, unit, role(key|value|computed)
       ├─ formulas: list[TableFormula]
       ├─ description: str
       ├─ extraction: ExtractionRef      # script_name, row_key: list[str]
       └─ errors: list[str]              # [] iff the table passed the gate
```

`query()` / `read_all()` return `ExtractedValue`:

```
ExtractedValue
├─ value: Any            # the live cell value
├─ dtype: str            # number | string | boolean | date
├─ unit: str | None
├─ sheet: str
├─ cell_ref: str         # e.g. "C4"
└─ is_computed: bool     # True for computed columns
```

**Structure, not data:** a `CanonicalTable` never copies cell values — it describes
*where* the data is. Values come only from `query()` reading the live file.

---

## 6. Failure behavior

- The swarm **never raises** out of orchestration. An unresolvable tab is returned as a
  `CanonicalTable` **stub with `errors` populated**; other tabs in the file still process.
- A table that fails the in-loop test gate is returned **with `errors` and no index** —
  `build_indices` skips it, so it simply won't appear in the `{table_id: index}` map.
- Check `table.errors == []` to know a table is queryable. Check
  `extraction.errors` for file-level problems.

```python
for t in extraction.tables:
    if t.errors:
        print(f"[skip] {t.table_id}: {t.errors}")
    else:
        print(f"[ok]   {t.table_id}: {t.region} cols={[c.name for c in t.columns]}")
```

---

## 7. Semantic / NL & formula resolution (eval adapter)

`run_swarm` gives you structure + `query(exact_row, exact_col)`. Mapping a **natural-
language query** ("What is the AvgSalary for Finance?") or a **formula operand**
(`cost_per_unit_emea`) to a coordinate is handled by a deterministic token-matching
resolver, `mcg_swarm/resolve.py::deterministic_resolve(phrase, catalog)`, wired into the
eval adapter (`eval/adapters/swarm_adapter.py`). It matches verbatim → all-tokens/
separator-insensitive → prefix tiers, reading only table *structure* (column/row names),
never answer values. When an `ANTHROPIC_API_KEY` is present the adapter tries the LLM
resolver first and falls back to the deterministic one.

```python
from mcg_swarm.resolve import deterministic_resolve

catalog = [{"table_id": "headcount",
            "columns": ["Headcount", "AvgSalary", "OpenReqs"],   # value columns
            "row_keys": ["Engineering", "Finance", "Ops"]}]
deterministic_resolve("What is the AvgSalary for Finance?", catalog)
# ('headcount', 'Finance', 'AvgSalary')
```

---

## 8. How to run

### Programmatically
See §3A — `run_swarm({"main": path})` then `build_indices(path, extraction)`.

### The benchmark (eval harness)
The eval harness scores the swarm against synthetic workbooks with known ground truth.

```bash
# reference adapter — should be ~100%
.venv/bin/python eval/run_benchmark.py --adapter oracle

# the swarm (deterministic; force no-LLM by exporting an empty key so the .env
# key is not picked up)
ANTHROPIC_API_KEY="" .venv/bin/python eval/run_benchmark.py --adapter swarm

# limit to specific workbooks
.venv/bin/python eval/run_benchmark.py --adapter swarm --workbooks sales_regional.xlsx headcount_dept.xlsx
```

Outputs land in `eval/results/` (`scorecard_swarm.json`, `report_swarm.html`).

### Tests
```bash
.venv/bin/python -m pytest -q
```

---

## 9. Current measured state

Deterministic (no LLM), 14 in-scope workbooks:

| Capability | Score |
|---|---|
| Table boundaries | **100%** (22/22) |
| Value extraction | **100%** (199/199) |
| Semantic extraction | **99.2%** (127/128) |
| Formula (intra-table) | **81.8%** (18/22) |
| **Overall** | **98.65%** (366/371) |

Oracle adapter: 100%. Test suite: 148 passed, 1 skipped.

The remaining gaps are genuinely the LLM's domain or out of scope: initialism operands
(`gp`→GrossProfit), single-letter row refs (`net_a`→"Product A"), cross-sheet formulas,
and a column-name/table-title collision — all documented in `DATA-REQUIREMENTS.md`.

---

## 10. Key files

| Path | Role |
|---|---|
| `mcg_swarm/runner.py` | `run_swarm`, `build_indices` — top-level entry points. |
| `mcg_swarm/splitter.py` | Tier-0 deterministic table detection. |
| `mcg_swarm/orchestrator.py` | Tier-1 per-table orchestration (never raises). |
| `mcg_swarm/subagent.py` | Tier-2 column/type analysis + optional LLM header verify. |
| `mcg_swarm/extraction.py` | `ExtractionIndex` — live-read `query()` layer. |
| `mcg_swarm/quality_gate.py` | In-loop quality gate. |
| `mcg_swarm/resolve.py` | Deterministic NL/operand → coordinate resolver. |
| `mcg_swarm/llm/client.py` | `LLMClient` interface + schema enforcement + clients. |
| `mcg_swarm/schemas.py` | Output models (`WorkbookExtraction`, `CanonicalTable`, …). |
| `eval/adapters/swarm_adapter.py` | Wires the swarm to the eval harness. |
| `DATA-REQUIREMENTS.md` | Authoritative assumptions, supported/unsupported cases, limits. |
