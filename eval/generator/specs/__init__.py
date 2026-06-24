"""Declarative specs for the 15 benchmark workbooks.

Graded easy -> hard, themed as realistic sales/finance/ops/HR spreadsheets.
Each WorkbookSpec yields TableSpec(s) (rendered to xlsx + ground-truth labels),
measure definitions (canonical measures the swarm should surface), and formula
definitions (intra-table arithmetic over measures the extraction layer should compute).

All data is deterministic (seeded), integer/simple-decimal, so ground truth is exact.

Builders are grouped by difficulty tier across easy/medium/hard modules; this package
re-exports the shared dataclasses and ``all_specs()`` so existing import paths
(``from eval.generator.specs import WorkbookSpec, all_specs``) keep working.
"""
from __future__ import annotations

from eval.generator.specs._model import FormulaDef, MeasureDef, WorkbookSpec
from eval.generator.specs.easy import (
    wb_expenses, wb_headcount, wb_inventory, wb_pricing, wb_sales_regional,
)
from eval.generator.specs.medium import (
    wb_capex, wb_multi_region_sales, wb_quarterly_pnl, wb_store_ops, wb_vendor_spend,
)
from eval.generator.specs.hard import (
    wb_cashflow_signs, wb_consolidated_pnl, wb_dup_tables, wb_large_ledger,
    wb_segment_report,
)

__all__ = [
    "FormulaDef", "MeasureDef", "WorkbookSpec", "ALL_BUILDERS", "all_specs",
]

ALL_BUILDERS = [
    wb_sales_regional, wb_headcount, wb_inventory, wb_expenses, wb_pricing,
    wb_quarterly_pnl, wb_multi_region_sales, wb_store_ops, wb_vendor_spend, wb_capex,
    wb_consolidated_pnl, wb_cashflow_signs, wb_segment_report, wb_dup_tables,
    wb_large_ledger,
]


def all_specs() -> list[WorkbookSpec]:
    return [b() for b in ALL_BUILDERS]
