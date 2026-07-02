from __future__ import annotations
import os
from mcg_swarm.schemas import WorkbookExtraction
from mcg_swarm.splitter import TableHandle
from mcg_swarm.analyzers.pipeline import analyze_workbook
from mcg_swarm.analyzers.registry import build_analyzers
from mcg_swarm.orchestrator import orchestrate_table
from mcg_swarm.subagent import build_subagent, build_table_validator, build_structural_reviewer
from mcg_swarm.config import SwarmConfig
from mcg_swarm.extraction import build_index
from mcg_swarm.source import as_source
from mcg_swarm.coverage import scan_handle
from mcg_swarm.views import TransposedView

GENERATOR_VERSION = "mcg-swarm-v2.0.0"


def run_swarm(workbooks, *, llm=None, runner=None, config: SwarmConfig = SwarmConfig()) -> WorkbookExtraction:
    """Fan-out across all tabs and return a WorkbookExtraction.

    Accepts a path string, ``{"main": path}`` dict, or any ``WorkbookSource``.
    One bad tab never fails the file — its errors land on its CanonicalTable.
    resolve_messy_tab is handled internally by orchestrate_table (Task 11).
    """
    source = as_source(workbooks)            # dict/path/source all OK
    name = getattr(source, "path", "workbook")
    name = os.path.basename(name) if isinstance(name, str) else "workbook"
    # Fail fast on a misconfigured analyzer name: this is a config/programming
    # error, not a data error, so it must raise out of run_swarm rather than
    # being swallowed by the try/except below and misreported as an
    # "unreadable workbook" data error. analyze_workbook still builds its own
    # analyzers internally; this call is purely for validation.
    build_analyzers(config.analyzers)
    try:
        sheet_analyses = analyze_workbook(source, config=config)
    except Exception as e:
        return WorkbookExtraction(
            workbook=name,
            sheets=[],
            tables=[],
            generator_version=GENERATOR_VERSION,
            errors=[f"unreadable workbook: {e}"],
        )
    # The application injects the ReAct runner (built against its provider/transport).
    # runner is None → static-only band subagent and no table validator.
    subagent = build_subagent(llm=llm, runner=runner, config=config)
    table_validator = build_table_validator(runner=runner, config=config)
    reviewer = build_structural_reviewer(runner=runner, config=config)
    tables, sheets, wb_findings = [], [], []
    for i, sa in enumerate(sheet_analyses):
        sheets.append(sa.sheet)
        wb_findings.extend(sa.findings)
        sheet_src = sa.view or source
        orient = "transposed" if isinstance(sa.view, TransposedView) else "vertical"

        if not sa.handles:
            continue  # zero-handle winner (e.g. all-diagram sheet): findings already recorded

        if len(sa.handles) > 1:
            # Multi-table interpretation from a lens: orchestrate each handle.
            # Layer-2 review presumes a single baseline handle, so it is skipped
            # here — multi-handle winners were already assessed at analyze time.
            for j, sh in enumerate(sa.handles):
                tables.append(orchestrate_table(
                    sheet_src, sh, table_id=f"{sa.sheet}__{i}_{j}", llm=llm,
                    subagent=subagent, table_validator=table_validator,
                    detect_findings=[], orientation=orient))
            continue

        h = sa.handles[0]
        try:
            grid = sheet_src.read_region(sa.sheet)
            scan = scan_handle(grid, h, sa.sheet)
        except Exception:
            grid, scan = None, []  # never let detection break extraction

        review = None
        if (reviewer is not None and grid is not None
                and any(f.category == "uncovered-data" for f in scan)):
            try:
                review = reviewer.review(sheet_src, h, grid, scan)
            except Exception:
                review = None  # never let alteration break extraction

        if review is not None and review.recut:
            # Live re-validation: the static gate proved the re-cut structurally
            # better, but the real per-table pipeline (band ReAct verifier, which
            # patches column role/dtype unconditionally, + table validator) can
            # behave differently on the smaller tables — a split can newly cross the
            # ReAct escalation threshold the monolithic baseline never hit. Never let
            # an accepted re-cut raise the live error count above the baseline.
            try:
                cand_tables = [orchestrate_table(
                        sheet_src, sh, table_id=f"{sa.sheet}__{i}_{j}", llm=llm,
                        subagent=subagent, table_validator=table_validator,
                        detect_findings=tf, orientation=orient)
                    for j, (sh, tf) in enumerate(
                        zip(review.handles, review.detect_findings))]
                base_table = orchestrate_table(
                    sheet_src, h, table_id=f"{sa.sheet}__{i}", llm=llm,
                    subagent=subagent, table_validator=table_validator,
                    detect_findings=[f for f in scan if f.scope != "sheet"],
                    orientation=orient)
                cand_err = sum(len(t.errors) for t in cand_tables)
                base_err = len(base_table.errors)
            except Exception:
                cand_tables, base_table = None, None  # never let it break extraction

            if cand_tables is not None and cand_err <= base_err:
                tables.extend(cand_tables)
                wb_findings.extend(review.sheet_findings)      # stays 'fixed'
            else:
                # live pipeline regressed (or failed) → keep deterministic baseline,
                # flip the detection annotation from fixed to rejected.
                tables.append(base_table if base_table is not None else orchestrate_table(
                    sheet_src, h, table_id=f"{sa.sheet}__{i}", llm=llm,
                    subagent=subagent, table_validator=table_validator,
                    detect_findings=[f for f in scan if f.scope != "sheet"],
                    orientation=orient))
                note = "re-cut raised live-pipeline errors; kept deterministic baseline"
                wb_findings.extend(
                    f.model_copy(update={"resolution": "rejected", "agent_action": note})
                    for f in scan if f.scope == "sheet")
            continue  # tables + findings already committed for this sheet

        if review is None:
            sheet_handles = [h]
            per_handle = [[f for f in scan if f.scope != "sheet"]]
            wb_findings.extend(f for f in scan if f.scope == "sheet")
        else:
            sheet_handles = review.handles          # baseline kept (reject/declined/open)
            per_handle = review.detect_findings
            wb_findings.extend(review.sheet_findings)

        multi = len(sheet_handles) > 1
        for j, (sh, tf) in enumerate(zip(sheet_handles, per_handle)):
            table_id = f"{sa.sheet}__{i}_{j}" if multi else f"{sa.sheet}__{i}"
            tables.append(orchestrate_table(
                sheet_src, sh, table_id=table_id, llm=llm,
                subagent=subagent, table_validator=table_validator,
                detect_findings=tf, orientation=orient))
    return WorkbookExtraction(
        workbook=name,
        sheets=sheets,
        tables=tables,
        generator_version=GENERATOR_VERSION,
        findings=wb_findings,
    )


def build_indices(path, extraction: WorkbookExtraction) -> dict:
    """Rebuild ExtractionIndex objects deterministically for the adapter.

    Skips tables that have errors (failed tables have no valid index).
    Transposed tables (extracted through a TransposedView) are rebuilt through
    the same view kind so their view-coordinate regions resolve correctly.
    """
    from mcg_swarm.source import as_source

    out = {}
    for t in extraction.tables:
        if t.errors:  # don't build an index for a failed table
            continue
        handle = TableHandle(
            sheet=t.sheet,
            region=t.region,
            header_row=t.header_row,
            columns=t.columns,
            header_span=getattr(t, "header_span", 1),
        )
        src = as_source(path)
        if t.orientation == "transposed":
            src = TransposedView(src)
        out[t.table_id] = build_index(src, handle, row_key=t.extraction.row_key)
    return out
