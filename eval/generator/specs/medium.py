"""Tier 2 — medium: titles, offsets, gaps, units, totals, multi-table sheets."""
from __future__ import annotations

import random

from eval.generator.tables import ColSpec, TableSpec
from eval.generator.specs._model import FormulaDef, MeasureDef, WorkbookSpec, _grid


def wb_quarterly_pnl() -> WorkbookSpec:
    rows = ["Revenue", "COGS", "OpEx"]
    vals = {
        "Revenue": [5000, 5400, 5800, 6200],
        "COGS": [2000, 2100, 2200, 2300],
        "OpEx": [1500, 1550, 1600, 1650],
    }
    data = {}
    for r in rows:
        for i, q in enumerate(["Q1", "Q2", "Q3", "Q4"]):
            data[(r, q)] = vals[r][i]
    t1 = TableSpec(
        "income_statement", "Income Statement", "P&L", (1, 1), "Line Item",
        [ColSpec(q, "USD") for q in ["Q1", "Q2", "Q3", "Q4"]],
        rows, data,
        title="Income Statement (USD thousands)",
        units_in_header=True, totals_col="FY",
    )
    arows = ["TaxRate", "DiscountRate"]
    adata = {("TaxRate", "Value"): 21, ("DiscountRate", "Value"): 8}
    t2 = TableSpec(
        "assumptions", "Assumptions", "P&L", (9, 1), "Assumption",
        [ColSpec("Value", "%")], arows, adata,
        title="Assumptions",
    )
    measures = [
        MeasureDef("revenue_q1", "income_statement", "Revenue", "Q1"),
        MeasureDef("cogs_q1", "income_statement", "COGS", "Q1"),
        MeasureDef("opex_q1", "income_statement", "OpEx", "Q1"),
        MeasureDef("revenue_fy", "income_statement", "Revenue", "FY"),
    ]
    formulas = [
        FormulaDef("Net income Q1", "R - C - O",
                   {"R": "revenue_q1", "C": "cogs_q1", "O": "opex_q1"}),
    ]
    return WorkbookSpec(
        "quarterly_pnl.xlsx", "medium", "finance",
        ["title_banner", "units_in_header", "totals_column", "multi_table_sheet"],
        "Net income = revenue minus COGS minus operating expense.",
        [t1, t2], measures, formulas,
    )


def wb_multi_region_sales() -> WorkbookSpec:
    na_rows = ["US", "Canada", "Mexico"]
    na = _grid(na_rows, {"Revenue": [9000, 1800, 1200], "Units": [450, 90, 60]})
    eu_rows = ["UK", "Germany", "France"]
    eu = _grid(eu_rows, {"Revenue": [3000, 4200, 2600], "Units": [150, 210, 130]})
    t1 = TableSpec(
        "na_sales", "North America Sales", "Sales", (4, 3), "Country",
        [ColSpec("Revenue", "USD"), ColSpec("Units", "count")],
        na_rows, na,
        traps=["offset_anchor", "stacked_tables"],
    )
    t2 = TableSpec(
        "eu_sales", "Europe Sales", "Sales", (10, 3), "Country",
        [ColSpec("Revenue", "USD"), ColSpec("Units", "count")],
        eu_rows, eu,
        traps=["offset_anchor", "stacked_tables"],
    )
    measures = [
        MeasureDef("revenue_us", "na_sales", "US", "Revenue"),
        MeasureDef("units_us", "na_sales", "US", "Units"),
        MeasureDef("revenue_germany", "eu_sales", "Germany", "Revenue"),
        MeasureDef("units_germany", "eu_sales", "Germany", "Units"),
    ]
    formulas = [
        FormulaDef("US average selling price", "R / U",
                   {"R": "revenue_us", "U": "units_us"}),
        FormulaDef("Germany average selling price", "R / U",
                   {"R": "revenue_germany", "U": "units_germany"}),
    ]
    return WorkbookSpec(
        "multi_region_sales.xlsx", "medium", "sales",
        ["offset_anchor", "stacked_tables", "blank_row_gap"],
        "Average selling price = revenue divided by units sold.",
        [t1, t2], measures, formulas,
    )


def wb_store_ops() -> WorkbookSpec:
    days = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    tables, measures, formulas = [], [], []
    rng = random.Random(8)
    for s in [1, 2]:
        sheet = f"Store {s}"
        sales = _grid(days, {
            "Transactions": [rng.randint(80, 200) for _ in days],
            "Revenue": [rng.randint(2000, 6000) for _ in days],
        })
        labor = _grid(days, {
            "Hours": [rng.randint(30, 60) for _ in days],
            "LaborCost": [rng.randint(600, 1400) for _ in days],
        })
        tables.append(TableSpec(
            f"sales_store{s}", f"Store {s} Sales", sheet, (1, 1), "Day",
            [ColSpec("Transactions", "count"), ColSpec("Revenue", "USD")],
            days, sales, units_in_header=True, traps=["units_in_header"]))
        tables.append(TableSpec(
            f"labor_store{s}", f"Store {s} Labor", sheet, (9, 1), "Day",
            [ColSpec("Hours", "count"), ColSpec("LaborCost", "USD")],
            days, labor, units_in_header=True,
            traps=["units_in_header", "multi_table_sheet"]))
        measures += [
            MeasureDef(f"revenue_mon_store{s}", f"sales_store{s}", "Mon", "Revenue"),
            MeasureDef(f"laborcost_mon_store{s}", f"labor_store{s}", "Mon", "LaborCost"),
        ]
        formulas.append(FormulaDef(
            f"Store {s} Monday labor pct", "L / R * 100",
            {"L": f"laborcost_mon_store{s}", "R": f"revenue_mon_store{s}"}))
    return WorkbookSpec(
        "store_ops.xlsx", "medium", "ops",
        ["multi_sheet", "multi_table_sheet", "units_in_header"],
        "Labor percentage = labor cost divided by revenue, as a percent.",
        tables, measures, formulas,
    )


def wb_vendor_spend() -> WorkbookSpec:
    rows = ["Acme", "Globex", "Initech", "Umbrella", "Stark"]
    cols = {
        "Q1": [12000, 8000, 5000, 22000, 14000],
        "Q2": [13000, 8500, 5200, 21000, 15000],
        "Q3": [11000, 9000, 4800, 23000, 16000],
        "Q4": [14000, 9500, 5100, 20000, 17000],
    }
    t = TableSpec(
        "vendor_spend", "Vendor Spend", "Vendor Spend", (1, 1), "Vendor",
        [ColSpec(q, "USD") for q in ["Q1", "Q2", "Q3", "Q4"]],
        rows, _grid(rows, cols),
        title="Vendor Spend by Quarter (USD)",
        totals_col="Annual", totals_row="Total",
        traps=["title_banner", "totals_row", "totals_column"],
    )
    measures = [
        MeasureDef("acme_annual", "vendor_spend", "Acme", "Annual"),
        MeasureDef("umbrella_annual", "vendor_spend", "Umbrella", "Annual"),
        MeasureDef("total_q1", "vendor_spend", "Total", "Q1"),
    ]
    formulas = [
        FormulaDef("Acme vs Umbrella annual delta", "U - A",
                   {"U": "umbrella_annual", "A": "acme_annual"}),
    ]
    return WorkbookSpec(
        "vendor_spend.xlsx", "medium", "finance",
        ["title_banner", "totals_row", "totals_column"],
        "Annual spend per vendor = sum of the four quarterly spends.",
        [t], measures, formulas,
    )


def wb_capex() -> WorkbookSpec:
    rows = ["Datacenter", "Fleet", "Office Reno", "Tooling"]
    cols = {
        "Y2024": [400, 120, 80, 60],
        "Y2025": [350, 140, 0, 90],
        "Y2026": [200, 160, 50, 110],
    }
    data = _grid(rows, cols)
    notes = {"Datacenter": "Phase 2", "Fleet": "EV transition",
             "Office Reno": "HQ only", "Tooling": "CI/CD"}
    for r in rows:
        data[(r, "Notes")] = notes[r]
    t = TableSpec(
        "capex", "Capex Plan", "Capex", (2, 2), "Project",
        [ColSpec("Y2024", "USD"), ColSpec("Y2025", "USD"), ColSpec("Y2026", "USD"),
         ColSpec("Notes", None, "string")],
        rows, data,
        title="Capital Expenditure Plan (USD thousands)",
        totals_col="Total", totals_row="Total",
        traps=["title_banner", "text_column", "totals_row", "totals_column",
               "offset_anchor"],
    )
    measures = [
        MeasureDef("datacenter_total", "capex", "Datacenter", "Total"),
        MeasureDef("fleet_total", "capex", "Fleet", "Total"),
        MeasureDef("total_y2024", "capex", "Total", "Y2024"),
    ]
    formulas = [
        FormulaDef("Datacenter + Fleet total capex", "D + F",
                   {"D": "datacenter_total", "F": "fleet_total"}),
    ]
    return WorkbookSpec(
        "capex_plan.xlsx", "medium", "finance",
        ["title_banner", "text_column", "totals_row", "totals_column", "offset_anchor"],
        "Project total capex = sum of yearly capex; ignore the text Notes column.",
        [t], measures, formulas,
    )
