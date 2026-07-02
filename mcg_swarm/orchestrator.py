"""
Tier-1 orchestrator: split → plan_bands → analyze_band → merge → build_index → test → return.

Contract:
- NEVER raises. All failures are captured as CanonicalTable.findings (errors is a derived view).
- NEVER marks a failing table as passing (errors == [] iff run_table_tests passed).
- Ambiguous handles return an error stub immediately.
"""
from __future__ import annotations
import dataclasses

from mcg_swarm.schemas import CanonicalTable, ExtractionRef, Finding, finding_from_gate_failure
from mcg_swarm.size_estimate import plan_bands
from mcg_swarm.subagent import BandTask, StaticSubagent
from mcg_swarm.merge import merge_reports
from mcg_swarm.extraction import build_index
from mcg_swarm.formula_extract import extract_formulas
from mcg_swarm.quality_gate import run_table_tests
from mcg_swarm.header_llm import resolve_messy_tab
from eval.util import range_box


def _stub(handle, table_id: str, findings: list, orientation: str = "vertical") -> CanonicalTable:
    """Return a minimal CanonicalTable stub carrying the given findings."""
    return CanonicalTable(
        table_id=table_id,
        sheet=handle.sheet,
        region=handle.region,
        header_row=handle.header_row,
        columns=list(handle.columns),
        description="",
        extraction=ExtractionRef(script_name=f"idx_{table_id}", row_key=[]),
        findings=list(findings),
        orientation=orientation,
    )


def _orchestrate_core(
    source,
    handle,
    table_id: str,
    llm=None,
    subagent=None,
    max_repairs: int = 2,
    detect_findings: list | None = None,
    orientation: str = "vertical",
) -> CanonicalTable:
    """
    Orchestrate full analysis of a single table handle.

    Parameters
    ----------
    source:      WorkbookSource (or path str for back-compat) for the workbook.
    handle:      TableHandle from splitter.split_workbook().
    table_id:    Unique identifier string for this table.
    llm:         Optional LLMClient; used for the §0 messy-tab header fallback and,
                 when no subagent is injected, for the default StaticSubagent.
    subagent:    Optional Subagent (analyze(task) -> SegmentReport). Defaults to
                 StaticSubagent(llm); the orchestrator treats it opaquely.
    max_repairs: Reserved for future bounded repair loop (not yet active).

    Returns
    -------
    CanonicalTable — always. Never raises.
    """
    detect_findings = list(detect_findings or [])

    # §0  LLM header fallback — attempt resolution before fail-loud
    if handle.ambiguous and llm is not None:
        handle = resolve_messy_tab(source, handle, llm)  # never raises

    # §1  Ambiguous handle — fail-loud stub immediately
    if handle.ambiguous:
        return _stub(handle, table_id, detect_findings + [Finding(
            category="messy-tab", severity="error", scope="table", source="static",
            message=f"messy tab: {handle.reason or 'ambiguous header'}")],
            orientation=orientation)

    if subagent is None:
        subagent = StaticSubagent(llm)

    try:
        # §2  Plan bands and dispatch subagents
        axis, _k, bands = plan_bands(handle)
        # Fix 1: pass each band only its own column slice so col-axis merge_reports
        # doesn't concatenate duplicated full headers. Forward the splitter's
        # structural signals (column roles, header span, ambiguity) into the BandTask
        # so the subagent can drive escalation without re-deriving them.
        _min_col = range_box(handle.region)[1]
        _hspan = getattr(handle, "header_span", 1)
        def _band_task(band):
            slice_ = handle.columns[
                (band.col_start - _min_col) : (band.col_end - _min_col + 1)
            ]
            return BandTask(
                path=getattr(source, "path", None),
                band=band,
                header=[c.name for c in slice_],
                handle_columns=list(slice_),
                header_span=_hspan,
                ambiguous=getattr(handle, "ambiguous", False),
                reason=getattr(handle, "reason", None),
                table_region=handle.region,
                source=source,
            )
        reports = [subagent.analyze(_band_task(b)) for b in bands]

        # §3  Merge; surface conflicts as errors (repair hook — minimal, deferred)
        merged = merge_reports(reports, axis=axis)
        if merged.conflicts:
            return _stub(handle, table_id, detect_findings + [Finding(
                category="merge-conflict", severity="error", scope="table",
                source="static", message=f"merge conflict: {c}") for c in merged.conflicts],
                orientation=orientation)

        # §4  Choose row_key and build ExtractionIndex
        key_cols = [c.name for c in merged.columns if c.role == "key"]
        row_key = key_cols[:1]  # first key column, or [] if none
        # Fix 2: build index from LLM-refined merged columns so dtype/unit/role
        # changes from subagent LLM pass reach query() output.
        merged_handle = dataclasses.replace(handle, columns=merged.columns)
        index = build_index(source, merged_handle, row_key=row_key)

        # §4.5  Extract in-cell formulas: translate same-row arithmetic, upgrade
        # role='computed' on translated targets (in place on merged.columns, which
        # `index` shares), capture the rest as provisional notes. Never raises.
        extracted_formulas, formula_notes = extract_formulas(
            source, index, merged.columns)
        all_formulas = list(merged.formulas) + extracted_formulas
        all_notes = list(merged.anomalies) + formula_notes

        # §5  Build intermediate CanonicalTable for testing
        table = CanonicalTable(
            table_id=table_id,
            sheet=handle.sheet,
            region=handle.region,
            header_row=handle.header_row,
            header_span=getattr(handle, "header_span", 1),
            columns=merged.columns,
            formulas=all_formulas,
            description=merged.description,
            findings=[Finding(category="anomaly", severity="info", scope="table",
                              source="static", message=n) for n in all_notes],
            extraction=ExtractionRef(script_name=f"idx_{table_id}", row_key=row_key),
            orientation=orientation,
        )

        # §6  Run quality gate
        report = run_table_tests(source, table, index)
        gate_findings = [] if report.passed else [
            finding_from_gate_failure(str(f)) for f in report.failures]

        # §7  Return fully-populated CanonicalTable (findings = detect + anomalies + gate)
        anomaly_findings = [Finding(category="anomaly", severity="info", scope="table",
                                    source="static", message=n) for n in all_notes]
        return CanonicalTable(
            table_id=table_id,
            sheet=handle.sheet,
            region=handle.region,
            header_row=handle.header_row,
            header_span=getattr(handle, "header_span", 1),
            columns=merged.columns,
            formulas=all_formulas,
            description=merged.description,
            extraction=ExtractionRef(script_name=f"idx_{table_id}", row_key=row_key),
            findings=detect_findings + anomaly_findings + gate_findings,
            orientation=orientation,
        )

    except Exception as exc:  # never let a subagent failure escape
        return _stub(handle, table_id, detect_findings + [Finding(
            category="orchestration-error", severity="error", scope="table",
            source="static", message=f"orchestration error: {exc}")],
            orientation=orientation)


def orchestrate_table(
    source,
    handle,
    table_id: str,
    llm=None,
    subagent=None,
    table_validator=None,
    max_repairs: int = 2,
    detect_findings: list | None = None,
    orientation: str = "vertical",
) -> CanonicalTable:
    """Static orchestration, then an optional table-level agent validation/recovery pass.

    The static pipeline (`_orchestrate_core`) runs unchanged and produces the
    CanonicalTable. When a `table_validator` is supplied it gets the final say: it runs
    the agent over the whole table (always on errors; configurably on clean tables) and
    returns a possibly-corrected table. The orchestrator stays unaware of its internals.

    Parameters
    ----------
    source: WorkbookSource (or path str for back-compat) for the workbook.
    """
    table = _orchestrate_core(
        source, handle, table_id, llm=llm, subagent=subagent, max_repairs=max_repairs,
        detect_findings=detect_findings, orientation=orientation)
    if table_validator is not None:
        table = table_validator.review(source, handle, table)
    return table
