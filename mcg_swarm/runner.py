from __future__ import annotations
import os
from mcg_swarm.schemas import WorkbookExtraction
from mcg_swarm.splitter import split_workbook, TableHandle
from mcg_swarm.orchestrator import orchestrate_table
from mcg_swarm.subagent import build_subagent
from mcg_swarm.extraction import build_index

GENERATOR_VERSION = "mcg-swarm-v2.0.0"


def run_swarm(workbooks: dict, llm=None) -> WorkbookExtraction:
    """Fan-out across all tabs and return a WorkbookExtraction.

    One bad tab never fails the file — its errors land on its CanonicalTable.
    resolve_messy_tab is handled internally by orchestrate_table (Task 11).
    """
    path = workbooks["main"]
    name = os.path.basename(path)
    try:
        handles = split_workbook(path)
    except Exception as e:
        return WorkbookExtraction(
            workbook=name,
            sheets=[],
            tables=[],
            generator_version=GENERATOR_VERSION,
            errors=[f"unreadable workbook: {e}"],
        )
    # Construct the configured subagent once (MCG_SUBAGENT); threaded down opaquely.
    subagent = build_subagent(llm=llm)
    tables, sheets = [], []
    for i, h in enumerate(handles):
        sheets.append(h.sheet)
        tables.append(orchestrate_table(
            path, h, table_id=f"{h.sheet}__{i}", llm=llm, subagent=subagent))
    return WorkbookExtraction(
        workbook=name,
        sheets=sheets,
        tables=tables,
        generator_version=GENERATOR_VERSION,
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
