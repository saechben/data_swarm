from __future__ import annotations
import os
from mcg_swarm.schemas import WorkbookExtraction
from mcg_swarm.splitter import split_workbook, TableHandle
from mcg_swarm.orchestrator import orchestrate_table
from mcg_swarm.subagent import build_subagent, build_table_validator
from mcg_swarm.config import SwarmConfig
from mcg_swarm.extraction import build_index
from mcg_swarm.source import as_source
from mcg_swarm.coverage import scan_handle
from mcg_swarm.schemas import Finding

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
        handles = split_workbook(source)
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
    tables, sheets, wb_findings = [], [], []
    for i, h in enumerate(handles):
        sheets.append(h.sheet)
        try:
            grid = source.read_region(h.sheet)
            scan = scan_handle(grid, h, h.sheet)
        except Exception:
            scan = []  # never let detection break extraction
        table_findings = [f for f in scan if f.scope != "sheet"]
        wb_findings.extend(f for f in scan if f.scope == "sheet")
        tables.append(orchestrate_table(
            source, h, table_id=f"{h.sheet}__{i}", llm=llm,
            subagent=subagent, table_validator=table_validator,
            detect_findings=table_findings))
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
