from __future__ import annotations
import os
from mcg_swarm.schemas import WorkbookExtraction
from mcg_swarm.splitter import split_workbook, TableHandle
from mcg_swarm.orchestrator import orchestrate_table
from mcg_swarm.subagent import build_subagent, build_table_validator, build_structural_reviewer
from mcg_swarm.config import SwarmConfig
from mcg_swarm.extraction import build_index
from mcg_swarm.source import as_source
from mcg_swarm.coverage import scan_handle

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
    try:
        handles = split_workbook(source, config=config)
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
    for i, h in enumerate(handles):
        sheets.append(h.sheet)
        try:
            grid = source.read_region(h.sheet)
            scan = scan_handle(grid, h, h.sheet)
        except Exception:
            grid, scan = None, []  # never let detection break extraction

        review = None
        if (reviewer is not None and grid is not None
                and any(f.category == "uncovered-data" for f in scan)):
            try:
                review = reviewer.review(source, h, grid, scan)
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
                        source, sh, table_id=f"{h.sheet}__{i}_{j}", llm=llm,
                        subagent=subagent, table_validator=table_validator,
                        detect_findings=tf)
                    for j, (sh, tf) in enumerate(
                        zip(review.handles, review.detect_findings))]
                base_table = orchestrate_table(
                    source, h, table_id=f"{h.sheet}__{i}", llm=llm,
                    subagent=subagent, table_validator=table_validator,
                    detect_findings=[f for f in scan if f.scope != "sheet"])
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
                    source, h, table_id=f"{h.sheet}__{i}", llm=llm,
                    subagent=subagent, table_validator=table_validator,
                    detect_findings=[f for f in scan if f.scope != "sheet"]))
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
            table_id = f"{h.sheet}__{i}_{j}" if multi else f"{h.sheet}__{i}"
            tables.append(orchestrate_table(
                source, sh, table_id=table_id, llm=llm,
                subagent=subagent, table_validator=table_validator,
                detect_findings=tf))
    return WorkbookExtraction(
        workbook=name,
        sheets=sheets,
        tables=tables,
        generator_version=GENERATOR_VERSION,
        findings=wb_findings,
    )


def build_indices(path: str, extraction: WorkbookExtraction) -> dict:
    """Rebuild ExtractionIndex objects deterministically for the adapter.

    Skips tables that have errors (failed tables have no valid index).
    """
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
        out[t.table_id] = build_index(path, handle, row_key=t.extraction.row_key)
    return out
