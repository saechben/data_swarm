"""
Tier-1 orchestrator: split → plan_bands → analyze_band → merge → build_index → test → return.

Contract:
- NEVER raises. All failures are captured in CanonicalTable.errors.
- NEVER marks a failing table as passing (errors == [] iff run_table_tests passed).
- Ambiguous handles return an error stub immediately.
"""
from __future__ import annotations

from mcg_swarm.schemas import CanonicalTable, ExtractionRef
from mcg_swarm.size_estimate import plan_bands
from mcg_swarm.subagent import analyze_band
from mcg_swarm.merge import merge_reports
from mcg_swarm.extraction import build_index
from mcg_swarm.testing import run_table_tests
from mcg_swarm.header_llm import resolve_messy_tab


def _stub(handle, table_id: str, errors: list[str]) -> CanonicalTable:
    """Return a minimal CanonicalTable stub carrying the given errors."""
    return CanonicalTable(
        table_id=table_id,
        sheet=handle.sheet,
        region=handle.region,
        header_row=handle.header_row,
        columns=list(handle.columns),
        description="",
        extraction=ExtractionRef(script_name=f"idx_{table_id}", row_key=[]),
        errors=errors,
    )


def orchestrate_table(
    path: str,
    handle,
    table_id: str,
    llm=None,
    max_repairs: int = 2,
) -> CanonicalTable:
    """
    Orchestrate full analysis of a single table handle.

    Parameters
    ----------
    path:        Path to the workbook file.
    handle:      TableHandle from splitter.split_workbook().
    table_id:    Unique identifier string for this table.
    llm:         Optional LLMClient; passed through to analyze_band.
    max_repairs: Reserved for future bounded repair loop (not yet active).

    Returns
    -------
    CanonicalTable — always. Never raises.
    """
    # §0  LLM header fallback — attempt resolution before fail-loud
    if handle.ambiguous and llm is not None:
        handle = resolve_messy_tab(path, handle, llm)  # never raises

    # §1  Ambiguous handle — fail-loud stub immediately
    if handle.ambiguous:
        return _stub(
            handle,
            table_id,
            [f"messy tab: {handle.reason or 'ambiguous header'}"],
        )

    try:
        # §2  Plan bands and dispatch subagents
        axis, _k, bands = plan_bands(handle)
        header = [c.name for c in handle.columns]
        reports = [analyze_band(path, b, header, llm=llm) for b in bands]

        # §3  Merge; surface conflicts as errors (repair hook — minimal, deferred)
        merged = merge_reports(reports, axis=axis)
        if merged.conflicts:
            return _stub(
                handle,
                table_id,
                [f"merge conflict: {c}" for c in merged.conflicts],
            )

        # §4  Choose row_key and build ExtractionIndex
        key_cols = [c.name for c in merged.columns if c.role == "key"]
        row_key = key_cols[:1]  # first key column, or [] if none
        index = build_index(path, handle, row_key=row_key)

        # §5  Build intermediate CanonicalTable for testing
        table = CanonicalTable(
            table_id=table_id,
            sheet=handle.sheet,
            region=handle.region,
            header_row=handle.header_row,
            columns=merged.columns,
            formulas=merged.formulas,
            description=merged.description,
            extraction=ExtractionRef(script_name=f"idx_{table_id}", row_key=row_key),
        )

        # §6  Run quality gate
        report = run_table_tests(path, table, index)
        errors = [] if report.passed else list(report.failures)

        # §7  Return fully-populated CanonicalTable
        return CanonicalTable(
            table_id=table_id,
            sheet=handle.sheet,
            region=handle.region,
            header_row=handle.header_row,
            columns=merged.columns,
            formulas=merged.formulas,
            description=merged.description,
            extraction=ExtractionRef(script_name=f"idx_{table_id}", row_key=row_key),
            errors=errors,
        )

    except Exception as exc:  # never let a subagent failure escape
        return _stub(handle, table_id, [f"orchestration error: {exc}"])
