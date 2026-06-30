# Boundary Detection & Repair — Design

**Date:** 2026-06-30
**Status:** Approved (brainstorming) — pending spec review
**Topic:** Eliminate silent corruption of cases the static splitter gets wrong. Detect every boundary/structure problem deterministically (the guarantee); let the ReAct layer optionally repair them behind verify-before-accept (the stretch).

## Problem

A stress battery of 18 adversarial workbooks (static-only and full live-agent runs, 2026-06-29/30) showed the swarm **silently corrupts or drops data** on cases the deterministic splitter mis-handles, and that the live agent does **not** save them:

| Case | Pathology | Static | Live agent | Failure class |
|---|---|---|---|---|
| A | two stacked tables | only 1st; 2nd dropped, `errors=[]` | identical — silent | invisible to agent (off-region) |
| B | side-by-side tables | only left; right dropped, `errors=[]` | identical — silent | invisible to agent (off-region) |
| L | preamble rows as header | real table lost, `errors=[]` | identical — silent | invisible to agent (off-region) |
| I | currency text → false `span=2` | header eaten, `errors=[]` | **agent diagnosed it correctly** ("actual headers are Item/Price/Margin/Delta") but only as a `provisional_note`; columns still wrong, `errors=[]` | detected, not surfaced, not committable |
| C, D | transposed / 3-level header, empty `A1` | opaque `orchestration error: 'A'` | unchanged | ungraceful + wrong |
| M, J, K | subtotals / mixed dates / dup headers | mostly silent (K errors) | caught as notes | detected, not surfaced |

Two findings drive the design:
1. **The agent cannot breach the splitter ceiling.** In A/B/L the lost data is *outside the region handed to the agent*; it has no way to know more data exists. Only a component that scans the *whole sheet* independent of the (possibly wrong) region can catch these.
2. **When the agent does detect (I, M, J, K), the detection dies in a soft channel.** It lands in `provisional_notes`, so `errors=[]` and a consumer sees "clean." Signalling, not capability.

## Goal (north star)

**Any case not correctly handled by static analysis MUST be detected and surfaced as a first-class signal — never silently corrupted.** Solving the case is a best-effort stretch; detection is the contract. Restated operationally: for every workbook, either the extraction is correct, or there is an `error`-severity `Finding` describing what is wrong.

## Decisions (locked)

1. **Plan A** — detection guaranteed (deterministic, model-free) + agent alteration best-effort (runner-gated). Detection is the contract; a rejected/again-impossible repair degrades to "detected, unresolved," which is acceptable.
2. **Two layers, separated by risk.** Layer 1 (detection) is deterministic and always on; Layer 2 (alteration) is the agent and only runs when a flag fires and a runner is present.
3. **The agent thinks freely but commits only through the gate.** A structural change is accepted only if it *provably* improves the result (coverage + gate errors); a hallucinated re-cut is a **no-op** (keep deterministic handles, keep the flag), never a corruption.
4. **The gate gains a coverage dimension.** Today it scores errors *within* a region; it cannot see dropped data. Layer 2's acceptance metric = gate errors **plus** coverage of the sheet's non-empty cells. The same coverage scan is Layer 1's detector.
5. **Structured `Finding` record is the source of truth** for signalling, carrying the error *and* the agent's provenance (`source`, `agent_action`, `resolution`). `errors: list[str]` is kept as a **derived view** (messages of `severity=="error"` findings) for backward compatibility, so existing error-checkers keep firing loudly. `provisional_notes` is likewise derived (or folded into findings).

## Architecture

```
split_workbook → deterministic TableHandle(s) per sheet
        │
        ▼
LAYER 1 — RESIDUE / AMBIGUITY SCAN  (deterministic, model-free, ALWAYS)
   over the full sheet grid:
     • uncovered-data:      non-empty region outside every handle that itself
                            contains a header-candidate-with-data-below
     • empty-header-corner: blank top-left header cell (also fixes the opaque
                            `orchestration error: 'A'`)
     • boundary-ambiguous / false-header-span: first "data" row looks like a
                            header, or true multi-row header under-detected
     • transpose-hint:      left column is labels, top row is period-like
   → emits Finding(severity=error|warning, source="static")   ← THE GUARANTEE
        │
        ▼  (only if a structural flag fired AND runner present)
LAYER 2 — STRUCTURAL ALTERATION  (agent, verify-before-accept)
   structural agent sees the full sheet grid + current handles + findings
   → proposes candidate handle set(s): split into N tables / re-anchor header /
     fix span / mark transposed
   → score_handles(candidate, grid) → (coverage, gate_errors)
   → accept ONLY if strictly better than the deterministic baseline
        (more covered non-empty cells AND no new errors); else keep baseline
   → record outcome on the Finding: agent_action + resolution(fixed|rejected|open)
        │
        ▼
per-table orchestration (unchanged) → CanonicalTable(s) with findings[]
```

With **no runner**, Layer 1 still runs in full: every silent case becomes a visible `Finding`. That is the contract, met without an LLM.

## Components

### 1. `Finding` record (new — `mcg_swarm/schemas.py`)

```
Finding:
  category: Literal["uncovered-data","boundary-ambiguous","false-header-span",
                    "empty-header-corner","transpose-suspected","dtype-mismatch",
                    "duplicate-column","computed-mismatch","coverage-gap",
                    "column-name","column-integrity","row-integrity","round-trip"]
  severity: Literal["error","warning","info"]
  scope:    Literal["workbook","sheet","table","column","cell"]
  ref:      str | None          # e.g. "Data!E1:G3", "Data", column name, "Amount@37"
  message:  str
  source:   Literal["static","gate","agent"]
  agent_action: str | None      # "re-typed Amount number→string" / "proposed re-split — rejected by gate"
  resolution:   Literal["fixed","open","rejected"] = "open"
```

- `CanonicalTable.findings: list[Finding]` (table/column/cell scope).
- `WorkbookExtraction.findings: list[Finding]` (workbook/sheet scope — where dropped-table signals live, since a dropped second table is not the first table's fault).
- **Backward compatibility (kept loud):**
  - `CanonicalTable.errors: list[str]` becomes a **derived property** = `[f.message for f in findings if f.severity=="error"]`.
  - `WorkbookExtraction.errors` likewise includes workbook/sheet error-findings.
  - `provisional_notes` = messages of `severity in {warning,info}` findings (or retained as a derived alias).
  - `repair_log` categorization keys off `Finding.category` (supersedes string-prefix matching, but prefixes still parse).
- Existing emitters (`quality_gate.py`, `static.py`, splitter messy-tab errors) are migrated to construct `Finding`s; the gate's current prefixes become categories.

### 2. Coverage / residue scan (new — e.g. `mcg_swarm/coverage.py`)

- `sheet_nonempty_cells(grid) -> set[(r,c)]`.
- `covered(handles) -> set[(r,c)]` (union of handle regions).
- `uncovered_blocks(grid, handles) -> list[Region]` — contiguous uncovered non-empty regions.
- A block is reported as `uncovered-data` **only if it contains a header-candidate row with data below** (reuse the splitter's existing `_is_header_candidate`). This keeps single-cell title banners and footnotes from false-positiving (they are legitimately uncovered).
- `coverage_score(handles, grid) -> int` = `|covered ∩ nonempty|`. Used both for detection (uncovered > 0 ⇒ flag) and Layer-2 acceptance.

### 3. Empty-header-corner fix (`splitter.py` / orchestrator)

- Blank top-left header cell currently leaks `KeyError 'A'` as `orchestration error: 'A'`. Handle gracefully: name the column by its letter (or `col0`) deterministically, and emit `empty-header-corner` (severity=warning) + `transpose-suspected` when the rest of the row/column shape matches transpose. No opaque internal error.

### 4. Layer-2 structural agent (`mcg_swarm/subagent/structural.py` — new)

- Reuses the injected `AgentRunner` (the DI seam shipped on main). Likely a **stronger model** than the band-level Haiku — configurable via the runner the app injects; the app may inject a separate, stronger runner for structural review (tiering is an app decision, no swarm change).
- New tool(s): read arbitrary sheet regions (whole-grid visibility, beyond the band probes).
- New structural patch schema: a proposed list of handles `{region, header_row, header_span, orientation}` for the sheet.
- `StructuralReviewer.review(sheet_grid, handles, findings) -> list[TableHandle]`:
  1. ask the agent for candidate handle set(s);
  2. for each candidate, run the existing per-table orchestration deterministically and compute `(coverage_score, n_error_findings)`;
  3. accept the candidate **iff** `coverage > baseline.coverage AND error_findings <= baseline.error_findings` (strictly-better-or-keep);
  4. on accept, mark the originating `Finding.resolution="fixed"` + `agent_action`; on reject, `resolution="rejected"` + record what was tried; never raise (any failure → baseline + findings unchanged).

### 5. In-scope alterations (Layer 2)
multi-table split (A, B), header re-anchor / preamble skip (L), false-span correction (I, D), and transpose (C). All get a **Layer-1 detector** regardless. Transpose *alteration* is the riskiest (it changes extraction orientation/semantics); it is included behind the same verify-before-accept gate, and if it can't be scored as strictly better it stays detection-only — consistent with Plan A.

## Data flow (no-runner vs runner)

- **No runner:** split → Layer 1 → findings → orchestrate deterministic handles. Every silent case is now a visible `Finding`. (Contract met.)
- **Runner present:** split → Layer 1 → if structural flags, Layer 2 proposes/scores/accepts → orchestrate final handles → findings carry `agent_action`/`resolution`.

## Error handling & determinism

- Never-raise preserved end-to-end: Layer 1 is pure; Layer 2 catches all agent/SDK failures and falls back to deterministic handles with findings intact.
- Verify-before-accept makes every agent structural change provably non-regressing; a hallucinated re-cut is a no-op, not a corruption.
- Layer 1 detection is fully deterministic and reproducible.

## Testing

- **Promote the stress battery to a committed regression fixture set** (`tests/fixtures/nasty/` + a generator), with explicit assertions:
  - **Detection (must, runner=None):** A/B/L emit `uncovered-data` error-findings; C/D emit `empty-header-corner` (no opaque `orchestration error`); I/D emit `false-header-span`; C emits `transpose-suspected`. No case is silently `errors=[]` when corrupted.
  - **Finding schema:** `errors` derived view equals `severity=="error"` messages; round-trips for a consumer that only reads `errors`.
  - **Coverage:** `uncovered_blocks` ignores single-cell banners (E title), flags A/B/L blocks.
  - **Layer 2 (with `FakeAgentRunner`):** a canned correct re-split for A is accepted (coverage↑); a canned bad re-split is rejected (no regression); resolution/agent_action recorded. No network in tests.
  - **Gate coverage metric** unit tests.
  - Existing suite stays green (current baseline 251 passed / 1 skipped on main with the SDK installed; 247/2 without).
- A separate **live** smoke (opt-in, SDK present) re-runs the battery to confirm real repair on A/B/L/I and records latency.

## Backward compatibility & migration

- `errors`/`provisional_notes` become derived from `findings`; all current consumers (eval adapter reading `errors`, `repair_log`, gate) keep working. One migration pass converts emitters to build `Finding`s.
- `CanonicalTable`/`WorkbookExtraction` gain `findings`; no field is removed.

## Out of scope

- *Guaranteeing* a correct repair for every case (Plan A is detect-always, solve-best-effort).
- Deep multi-row-header *semantics* beyond detection + best-effort re-anchor.
- Changes to Phase-1 formula translation, fan-out thresholds, or the `live=None` computed-mismatch behavior (tracked separately; noted by the battery but not this spec's target).

## Forward (not committed here)
- Tiered runners (cheap band model + strong structural model) as an app-composition pattern.
- Treating `live=None` formula cells as "uncomputed, skip" in gate Phase 4 (separate fix; surfaced by case Q).
