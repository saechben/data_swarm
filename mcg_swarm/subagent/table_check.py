"""Table-level validation / recovery check (verify-before-accept).

The band subagent runs deep in the pipeline (on one slice, before the table is merged
and quality-checked), so it cannot see whether the WHOLE table came out broken. This
check runs the ReAct agent over the fully-assembled `CanonicalTable`:

- **fallback (always on):** if the static pipeline returned a table WITH errors, run the
  agent to attempt recovery. Not configurable.
- **validation (configurable):** if the table is clean, also double-check it when
  ``validate`` is set.

The agent may return either corrected column metadata (dtype/unit/role, name-matched) or
— when the header itself was mis-detected (e.g. a data row folded into the header span so
column names look like data values such as ``'49'``) — a STRUCTURAL rebuild: a corrected
``header_row``/``header_span`` plus the full ordered column list.

**Verify-before-accept** is the safety primitive: every proposal is materialised into a
candidate table, re-indexed, and re-run through the quality gate. A candidate replaces the
original only when it is provably better — strictly fewer gate errors, or (on a tie) a
higher year-aware header *label score*, which lets a gate-blind header-span over-detection
be recovered without ever letting an unverifiable change regress a good table. The quality
gate's column-name check independently guarantees any restructured name matches a real
header cell, so the agent can only re-pick the header row, never fabricate names. The
review never raises — any failure returns the original table unchanged.
"""
from __future__ import annotations

import dataclasses
import json
from typing import Optional

from pydantic import BaseModel

from eval.util import range_box
from mcg_swarm.extraction import build_index
from mcg_swarm.quality_gate import run_table_tests
from mcg_swarm.repair_log import log_repair_pass
from mcg_swarm.schemas import CanonicalTable, ColumnSpec, finding_from_gate_failure
from mcg_swarm.source import as_source
from mcg_swarm.splitter import TableHandle
from mcg_swarm.subagent.tools import BandView, build_band_toolset
from mcg_swarm.subagent.verifier import _VALID_DTYPES, _VALID_ROLES, apply_column_patch
from mcg_swarm.size_estimate import Band


# --- agent output schema ---------------------------------------------------

class _MetaPatch(BaseModel):
    """A name-matched, partial column-metadata correction."""
    name: str
    dtype: Optional[str] = None
    unit: Optional[str] = None
    role: Optional[str] = None


class _StructCol(BaseModel):
    """One column in a full positional rebuild (header re-detection)."""
    name: str
    dtype: str = "string"
    role: str = "value"
    unit: Optional[str] = None


class TableRecoveryPatch(BaseModel):
    """What the table agent's `finalize` returns.

    Two non-exclusive ways to correct the table:
      * ``column_patches`` — partial, name-matched dtype/unit/role fixes (no restructure).
      * ``header_row`` / ``header_span`` + ``columns`` — a STRUCTURAL rebuild used only
        when the header was mis-detected; ``columns`` is the full ordered column list for
        the corrected header.
    """
    column_patches: list[_MetaPatch] = []
    header_row: Optional[int] = None
    header_span: Optional[int] = None
    columns: list[_StructCol] = []
    anomalies: list[str] = []


# --- policy ----------------------------------------------------------------

@dataclasses.dataclass
class TableCheckPolicy:
    """When to run the table-level agent check.

    Size gate removed: cost is bounded by sampling in the extraction layer.
    max_passes controls how many agent repair iterations are attempted.
    """
    validate: bool = False
    max_passes: int = 3

    def should_check(self, table: CanonicalTable, n_data_rows: int) -> bool:
        return bool(table.errors) or self.validate


# --- header label score (year-aware) ---------------------------------------

def _is_label(name) -> bool:
    """A good column name is a label: non-numeric, or a plausible year (e.g. 2023).

    Distinguishes a real header (``'Product'``, ``'Price'``, ``'2024'``) from a data row
    mis-read as a header (``'Widget'``, ``'49'``, ``'1200'``) — the latter carries bare
    non-year numbers, so it scores lower and loses the verify-before-accept tie-break.
    """
    try:
        f = float(str(name).replace(",", "").strip())
    except (TypeError, ValueError):
        return True  # non-numeric → a label
    return 1900.0 <= f <= 2100.0 and f.is_integer()  # year-like numbers are still labels


def _label_score(columns) -> int:
    return sum(1 for c in columns if _is_label(c.name))


def _ranks_higher(cand, cand_errs, best, best_errs) -> bool:
    """Order two accepted candidates: fewer gate errors first, then richer header labels."""
    if len(cand_errs) != len(best_errs):
        return len(cand_errs) < len(best_errs)
    return _label_score(cand.columns) > _label_score(best.columns)


# --- reindex + gate (verify) -----------------------------------------------

def _reindex_and_check(source, table: CanonicalTable) -> list[str]:
    """Rebuild the index for *table* and return the quality-gate failures it produces."""
    src = as_source(source)
    handle = TableHandle(
        sheet=table.sheet, region=table.region, header_row=table.header_row,
        columns=list(table.columns), header_span=table.header_span)
    index = build_index(src, handle, row_key=list(table.extraction.row_key))
    return list(run_table_tests(src, table, index).failures)


def _first_key(columns) -> list[str]:
    for c in columns:
        if c.role == "key":
            return [c.name]
    return []


def _structural_candidates(table: CanonicalTable,
                           patch: TableRecoveryPatch) -> list[CanonicalTable]:
    """Build candidate rebuilds from a structural patch, trying several header spans.

    The agent reliably identifies the correct column NAMES (and the gate guards them) but
    is unreliable at the header_span ARITHMETIC (it has been seen to return span 3 for a
    single-row header). So we don't trust its span: we try its span, a single-row header
    (the common over-detection fix), and the original span, then let verify-before-accept
    pick whichever actually re-indexes best. Spans that would leave no data rows are
    dropped.
    """
    if not patch.columns:
        return []  # a restructure needs the new column list
    new_cols = [
        ColumnSpec(
            name=c.name,
            dtype=c.dtype if c.dtype in _VALID_DTYPES else "string",
            unit=c.unit,
            role=c.role if c.role in _VALID_ROLES else "value")
        for c in patch.columns]
    header_row = patch.header_row or table.header_row
    _r0, _c0, max_r, _c1 = range_box(table.region)
    extraction = table.extraction.model_copy(update={"row_key": _first_key(new_cols)})

    out: list[CanonicalTable] = []
    seen: set[int] = set()
    for span in (patch.header_span, 1, table.header_span):
        if span is None or span < 1 or span in seen:
            continue
        if header_row + span > max_r:
            continue  # no data rows left under this header
        seen.add(span)
        out.append(table.model_copy(update={
            "header_row": header_row, "header_span": span,
            "columns": new_cols, "extraction": extraction}))
    return out


def _metadata_candidate(table: CanonicalTable,
                        patch: TableRecoveryPatch) -> Optional[CanonicalTable]:
    """Apply name-matched dtype/unit/role fixes; None if nothing changes."""
    if not patch.column_patches:
        return None
    meta = {"columns": [p.model_dump(exclude_none=True) for p in patch.column_patches]}
    new_cols = apply_column_patch(table.columns, meta)
    if [(c.name, c.dtype, c.unit, c.role) for c in new_cols] == \
       [(c.name, c.dtype, c.unit, c.role) for c in table.columns]:
        return None  # no-op
    return table.model_copy(update={"columns": new_cols})


def _candidates(table: CanonicalTable, patch: TableRecoveryPatch) -> list[CanonicalTable]:
    """All candidate tables the patch proposes (structural rebuilds or a metadata fix)."""
    if patch.header_row is not None or patch.header_span is not None:
        return _structural_candidates(table, patch)
    meta = _metadata_candidate(table, patch)
    return [meta] if meta is not None else []


def _table_seed(table: CanonicalTable) -> str:
    cols = [{"name": c.name, "dtype": c.dtype, "role": c.role, "unit": c.unit}
            for c in table.columns]
    lines = [
        "You are validating ONE fully-extracted spreadsheet table. A fast deterministic "
        "pass produced the column metadata below. Inspect the real cells with the "
        "read-only tools, then call `finalize`.",
        "",
        "If the columns are right but some dtype/unit/role is wrong, return only those "
        "fixes in `column_patches` (name-matched; include just the columns you change).",
        "",
        "If the HEADER itself was mis-detected — e.g. a data row was folded into a "
        "multi-row header so column names look like data values ('49', '1200'), or the "
        "header span is wrong — return a structural rebuild: the corrected `header_row` "
        "and/or `header_span`, plus `columns` as the FULL ordered list of real column "
        "names (which must come from actual header cells) with their dtype/role. Use "
        "`header_candidates` to see the rows around the header.",
        "",
        "Never invent names or cell values. If the extraction looks correct, return empty.",
        "",
        f"Region: {table.region}  header_row: {table.header_row}  header_span: {table.header_span}",
        "Columns: " + json.dumps(cols),
    ]
    if table.provisional_notes:
        lines.append("The per-band pass flagged these notes (a strong hint about what to "
                     "check — e.g. a header mis-detection it could not itself fix): "
                     + json.dumps(list(table.provisional_notes)))
    if table.errors:
        lines.append("Static extraction reported ERRORS to investigate: "
                     + json.dumps(list(table.errors)))
    return "\n".join(lines)


def _patch_summary(patch: "TableRecoveryPatch") -> str:
    """One-line human-readable description of what a patch proposed."""
    parts = []
    if patch.column_patches:
        parts.append(f"meta:{len(patch.column_patches)}")
    if patch.header_row is not None or patch.header_span is not None:
        parts.append(
            f"struct(hr={patch.header_row},hs={patch.header_span},"
            f"cols={len(patch.columns)})")
    return ",".join(parts) if parts else "empty"


class TableValidator:
    """Runs the ReAct agent over an assembled CanonicalTable, verify-before-accept."""

    def __init__(self, runner, policy: TableCheckPolicy | None = None) -> None:
        self._runner = runner
        self._policy = policy or TableCheckPolicy()

    def review(self, source, handle, table: CanonicalTable) -> CanonicalTable:
        try:
            src = as_source(source)
            _r0, _c0, max_r, _c1 = range_box(handle.region)
            n_data_rows = max_r - handle.header_row
            if not self._policy.should_check(table, n_data_rows):
                return table
            workbook = getattr(src, "path", "workbook")
            current, attempts = table, []
            for pass_no in range(self._policy.max_passes):
                errs_before = list(current.errors)
                patch = self._run_agent(src, handle, current, attempts)
                best, best_errs = None, None
                for cand in _candidates(current, patch):
                    try:
                        errs = _reindex_and_check(src, cand)
                    except Exception:
                        continue  # malformed candidate must not sink the others
                    if not self._accepts(current, cand, errs):
                        continue
                    if best is None or _ranks_higher(cand, errs, best, best_errs):
                        best, best_errs = cand, errs
                accepted = best is not None
                log_repair_pass(
                    workbook, current.table_id, pass_no,
                    errs_before,
                    best_errs if accepted else errs_before,
                    accepted,
                    _patch_summary(patch),
                    0.0,
                )
                if accepted:
                    # Rebuild findings to stay consistent with best_errs:
                    # keep non-error findings (detection/anomaly/warning/info),
                    # replace error-severity findings with freshly-mapped gate failures.
                    non_error_findings = [
                        f for f in best.findings if f.severity != "error"
                    ]
                    gate_findings = [finding_from_gate_failure(e) for e in best_errs]
                    _rebuilt = non_error_findings + gate_findings
                    current = best.model_copy(
                        update={"errors": best_errs, "findings": _rebuilt}
                    )
                    attempts.append(_patch_summary(patch))
                    if not best_errs:
                        break  # errors cleared — stop early
                else:
                    break  # no improvement — stop rather than burning passes
            return current
        except Exception:
            return table  # never raise; pipeline must not break

    def _run_agent(self, source, handle, table, attempts) -> TableRecoveryPatch:
        min_r, min_c, max_r, max_c = range_box(handle.region)
        band = Band(
            sheet=handle.sheet, header_row=handle.header_row, region=handle.region,
            col_start=min_c, col_end=max_c,
            row_start=handle.header_row + 1, row_end=max_r)
        tools = build_band_toolset(BandView(source, band))
        seed = _table_seed(table)
        if attempts:
            seed += ("\nPrevious passes already tried (do not repeat these): "
                     + json.dumps(attempts))
        raw = self._runner.run(seed, tools, schema=TableRecoveryPatch)
        return TableRecoveryPatch.model_validate(raw)

    @staticmethod
    def _accepts(original: CanonicalTable, candidate: CanonicalTable,
                 cand_errs: list[str]) -> bool:
        """Accept only a provably-better candidate (verify-before-accept).

        Strictly fewer gate errors wins outright. On a tie, a higher year-aware header
        label score wins — this recovers a gate-blind header-span over-detection (real
        labels beat data-as-header) while refusing to land an unverifiable lateral change.
        """
        orig_errs = list(original.errors)
        if len(cand_errs) < len(orig_errs):
            return True
        if len(cand_errs) == len(orig_errs):
            return _label_score(candidate.columns) > _label_score(original.columns)
        return False
