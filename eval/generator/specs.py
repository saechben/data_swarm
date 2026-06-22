"""Declarative specs for the 15 benchmark workbooks.

Graded easy -> hard, themed as realistic sales/finance/ops/HR spreadsheets.
Each WorkbookSpec yields TableSpec(s) (rendered to xlsx + ground-truth labels),
measure definitions (canonical measures the swarm should surface), and formula
definitions (intra-table arithmetic over measures the extraction layer should compute).

All data is deterministic (seeded), integer/simple-decimal, so ground truth is exact.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from eval.generator.tables import ColSpec, TableSpec


@dataclass
class MeasureDef:
    semantic_name: str
    table_id: str
    row_label: str
    col_label: str
    aliases: list[str] = field(default_factory=list)


@dataclass
class FormulaDef:
    description: str
    expression: str  # pure arithmetic in the operand symbols below
    operands: dict[str, str]  # symbol -> measure semantic_name


@dataclass
class WorkbookSpec:
    filename: str
    difficulty: str
    domain: str
    traps: list[str]
    business_logic: str
    tables: list[TableSpec]
    measures: list[MeasureDef]
    formulas: list[FormulaDef]


def _grid(rows, col_values):
    """col_values: {col_label: [v aligned with rows]} -> {(row,col): v}."""
    data = {}
    for i, r in enumerate(rows):
        for c, vals in col_values.items():
            data[(r, c)] = vals[i]
    return data


# --------------------------------------------------------------------------- #
# TIER 1 — easy: single clean table, header row 1, anchor A1, no traps
# --------------------------------------------------------------------------- #
def wb_sales_regional() -> WorkbookSpec:
    rows = ["EMEA", "APAC", "NorthAm", "LatAm"]
    cols = {
        "Revenue": [1000, 800, 1500, 600],
        "Units": [50, 40, 75, 30],
        "CostPerUnit": [12, 14, 11, 13],
        "Discount": [5, 8, 3, 10],
    }
    t = TableSpec(
        table_id="regional_sales",
        name="Regional Sales",
        sheet="Regional Sales",
        anchor=(1, 1),
        key_header="Region",
        columns=[
            ColSpec("Revenue", "USD"),
            ColSpec("Units", "count"),
            ColSpec("CostPerUnit", "USD"),
            ColSpec("Discount", "%"),
        ],
        rows=rows,
        data=_grid(rows, cols),
    )
    measures = []
    for reg in ["EMEA", "APAC"]:
        measures += [
            MeasureDef(f"revenue_{reg.lower()}", "regional_sales", reg, "Revenue",
                       [f"{reg} revenue"]),
            MeasureDef(f"units_{reg.lower()}", "regional_sales", reg, "Units",
                       [f"{reg} units"]),
            MeasureDef(f"cost_per_unit_{reg.lower()}", "regional_sales", reg,
                       "CostPerUnit", [f"{reg} unit cost"]),
        ]
    formulas = [
        FormulaDef(
            f"Gross margin for {reg}", "R - U * C",
            {"R": f"revenue_{reg.lower()}", "U": f"units_{reg.lower()}",
             "C": f"cost_per_unit_{reg.lower()}"},
        )
        for reg in ["EMEA", "APAC"]
    ]
    return WorkbookSpec(
        "sales_regional.xlsx", "easy", "sales", [],
        "Gross margin = revenue minus units sold times unit cost.",
        [t], measures, formulas,
    )


def wb_headcount() -> WorkbookSpec:
    rows = ["Engineering", "Sales", "Marketing", "Ops", "Finance"]
    cols = {
        "Headcount": [120, 80, 35, 50, 25],
        "AvgSalary": [145000, 110000, 95000, 85000, 130000],
        "OpenReqs": [12, 6, 3, 4, 2],
    }
    t = TableSpec(
        "headcount", "Headcount by Department", "Headcount", (1, 1), "Department",
        [ColSpec("Headcount", "count"), ColSpec("AvgSalary", "USD"),
         ColSpec("OpenReqs", "count")],
        rows, _grid(rows, cols),
    )
    measures = [
        MeasureDef("headcount_eng", "headcount", "Engineering", "Headcount"),
        MeasureDef("avg_salary_eng", "headcount", "Engineering", "AvgSalary"),
        MeasureDef("headcount_sales", "headcount", "Sales", "Headcount"),
        MeasureDef("avg_salary_sales", "headcount", "Sales", "AvgSalary"),
    ]
    formulas = [
        FormulaDef("Engineering annual payroll", "H * S",
                   {"H": "headcount_eng", "S": "avg_salary_eng"}),
        FormulaDef("Sales annual payroll", "H * S",
                   {"H": "headcount_sales", "S": "avg_salary_sales"}),
    ]
    return WorkbookSpec(
        "headcount_dept.xlsx", "easy", "hr", [],
        "Annual payroll = headcount times average salary.",
        [t], measures, formulas,
    )


def wb_inventory() -> WorkbookSpec:
    rows = ["SKU-100", "SKU-101", "SKU-102", "SKU-103", "SKU-104"]
    cols = {
        "OnHand": [340, 120, 0, 75, 410],
        "ReorderPoint": [100, 150, 50, 80, 200],
        "UnitCost": [4, 9, 22, 15, 3],
        "LeadTimeDays": [14, 30, 7, 21, 10],
    }
    t = TableSpec(
        "inventory", "Inventory Snapshot", "Inventory", (1, 1), "SKU",
        [ColSpec("OnHand", "count"), ColSpec("ReorderPoint", "count"),
         ColSpec("UnitCost", "USD"), ColSpec("LeadTimeDays", "days")],
        rows, _grid(rows, cols),
    )
    measures = [
        MeasureDef("onhand_sku100", "inventory", "SKU-100", "OnHand"),
        MeasureDef("unitcost_sku100", "inventory", "SKU-100", "UnitCost"),
        MeasureDef("onhand_sku104", "inventory", "SKU-104", "OnHand"),
        MeasureDef("unitcost_sku104", "inventory", "SKU-104", "UnitCost"),
    ]
    formulas = [
        FormulaDef("Inventory value SKU-100", "Q * C",
                   {"Q": "onhand_sku100", "C": "unitcost_sku100"}),
        FormulaDef("Inventory value SKU-104", "Q * C",
                   {"Q": "onhand_sku104", "C": "unitcost_sku104"}),
    ]
    return WorkbookSpec(
        "inventory_snapshot.xlsx", "easy", "ops", [],
        "Inventory value = on-hand quantity times unit cost.",
        [t], measures, formulas,
    )


def wb_expenses() -> WorkbookSpec:
    rows = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]
    cols = {
        "Rent": [12000] * 6,
        "Payroll": [88000, 88000, 91000, 91000, 95000, 95000],
        "Marketing": [15000, 22000, 18000, 30000, 12000, 25000],
        "Utilities": [3200, 3100, 2900, 2700, 2500, 2400],
    }
    t = TableSpec(
        "expenses", "Monthly Expenses", "Expenses", (1, 1), "Month",
        [ColSpec("Rent", "USD"), ColSpec("Payroll", "USD"),
         ColSpec("Marketing", "USD"), ColSpec("Utilities", "USD")],
        rows, _grid(rows, cols),
    )
    measures = [
        MeasureDef(f"{c.lower()}_jan", "expenses", "Jan", c)
        for c in ["Rent", "Payroll", "Marketing", "Utilities"]
    ]
    formulas = [
        FormulaDef("Total opex January", "R + P + M + U",
                   {"R": "rent_jan", "P": "payroll_jan", "M": "marketing_jan",
                    "U": "utilities_jan"}),
    ]
    return WorkbookSpec(
        "monthly_expenses.xlsx", "easy", "finance", [],
        "Total operating expense = rent + payroll + marketing + utilities.",
        [t], measures, formulas,
    )


def wb_pricing() -> WorkbookSpec:
    rows = ["Widget", "Gadget", "Gizmo", "Sprocket"]
    cols = {
        "ListPrice": [49, 99, 149, 29],
        "UnitCost": [18, 40, 60, 11],
        "Qty": [1200, 800, 300, 2500],
    }
    t = TableSpec(
        "pricing", "Product Price List", "Price List", (1, 1), "Product",
        [ColSpec("ListPrice", "USD"), ColSpec("UnitCost", "USD"),
         ColSpec("Qty", "count")],
        rows, _grid(rows, cols),
    )
    measures = []
    for p in ["Widget", "Gadget"]:
        measures += [
            MeasureDef(f"list_price_{p.lower()}", "pricing", p, "ListPrice"),
            MeasureDef(f"unit_cost_{p.lower()}", "pricing", p, "UnitCost"),
            MeasureDef(f"qty_{p.lower()}", "pricing", p, "Qty"),
        ]
    formulas = [
        FormulaDef(f"Gross profit {p}", "(L - C) * Q",
                   {"L": f"list_price_{p.lower()}", "C": f"unit_cost_{p.lower()}",
                    "Q": f"qty_{p.lower()}"})
        for p in ["Widget", "Gadget"]
    ]
    return WorkbookSpec(
        "product_pricing.xlsx", "easy", "pricing", [],
        "Gross profit = (list price minus unit cost) times quantity sold.",
        [t], measures, formulas,
    )


# --------------------------------------------------------------------------- #
# TIER 2 — medium: titles, offsets, gaps, units, totals, multi-table sheets
# --------------------------------------------------------------------------- #
def wb_quarterly_pnl() -> WorkbookSpec:
    rows = ["Revenue", "COGS", "OpEx"]
    cols = {q: None for q in ["Q1", "Q2", "Q3", "Q4"]}
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


# --------------------------------------------------------------------------- #
# TIER 3 — hard: multi-level headers, sign traps, footnotes/text numbers,
#          duplicate tables, large scale
# --------------------------------------------------------------------------- #
def wb_consolidated_pnl() -> WorkbookSpec:
    rows = ["Revenue", "COGS", "OpEx"]
    leaf = [
        ColSpec("Actual", "USD", group="EMEA"),
        ColSpec("Budget", "USD", group="EMEA"),
        ColSpec("Actual", "USD", group="APAC"),
        ColSpec("Budget", "USD", group="APAC"),
    ]
    # data keyed by (row, col_label) — but Actual/Budget repeat across groups,
    # so we must disambiguate. Use distinct logical col labels then relabel.
    # Simpler: give leaf columns unique labels but keep group merged header.
    leaf = [
        ColSpec("EMEA Actual", "USD", group="EMEA"),
        ColSpec("EMEA Budget", "USD", group="EMEA"),
        ColSpec("APAC Actual", "USD", group="APAC"),
        ColSpec("APAC Budget", "USD", group="APAC"),
    ]
    vals = {
        "Revenue": [4200, 4000, 3100, 3300],
        "COGS": [1800, 1750, 1400, 1350],
        "OpEx": [900, 950, 700, 720],
    }
    data = {}
    labels = ["EMEA Actual", "EMEA Budget", "APAC Actual", "APAC Budget"]
    for r in rows:
        for i, c in enumerate(labels):
            data[(r, c)] = vals[r][i]
    t = TableSpec(
        "consolidated", "Consolidated P&L", "Consolidated", (1, 1), "Line Item",
        leaf, rows, data,
        title="Consolidated P&L — Actual vs Budget (USD thousands)",
        traps=["two_level_header", "merged_cells", "title_banner"],
    )
    measures = [
        MeasureDef("revenue_emea_actual", "consolidated", "Revenue", "EMEA Actual"),
        MeasureDef("revenue_emea_budget", "consolidated", "Revenue", "EMEA Budget"),
        MeasureDef("revenue_apac_actual", "consolidated", "Revenue", "APAC Actual"),
        MeasureDef("cogs_emea_actual", "consolidated", "COGS", "EMEA Actual"),
    ]
    formulas = [
        FormulaDef("EMEA revenue variance (actual - budget)", "A - B",
                   {"A": "revenue_emea_actual", "B": "revenue_emea_budget"}),
    ]
    return WorkbookSpec(
        "consolidated_pnl_multiheader.xlsx", "hard", "finance",
        ["two_level_header", "merged_cells", "title_banner"],
        "Variance = actual minus budget, per region and line item.",
        [t], measures, formulas,
    )


def wb_cashflow_signs() -> WorkbookSpec:
    rows = ["Operating", "Investing", "Financing"]
    cols = {
        "Jan": [5000, -3000, -1000],
        "Feb": [5200, -800, -1000],
        "Mar": [4800, -6000, 2000],
    }
    t1 = TableSpec(
        "cashflow", "Cash Flow Statement", "Cash Flow", (1, 1), "Activity",
        [ColSpec("Jan", "USD"), ColSpec("Feb", "USD"), ColSpec("Mar", "USD")],
        rows, _grid(rows, cols),
        title="Cash Flow (outflows in parentheses, USD)",
        paren_negatives=True, totals_row="Net Change",
        traps=["paren_negatives", "sign_convention", "totals_row"],
    )
    # transposed summary on a second sheet: activities across columns
    srows = ["Q1 Total"]
    sdata = {("Q1 Total", "Operating"): 15000, ("Q1 Total", "Investing"): -9800,
             ("Q1 Total", "Financing"): 0}
    t2 = TableSpec(
        "cashflow_summary", "Cash Flow Summary", "Summary", (1, 1), "Period",
        [ColSpec("Operating", "USD"), ColSpec("Investing", "USD"),
         ColSpec("Financing", "USD")],
        srows, sdata,
        orientation="transposed", paren_negatives=True,
        traps=["transposed", "paren_negatives"],
    )
    measures = [
        MeasureDef("operating_jan", "cashflow", "Operating", "Jan"),
        MeasureDef("investing_jan", "cashflow", "Investing", "Jan"),
        MeasureDef("financing_jan", "cashflow", "Financing", "Jan"),
        MeasureDef("net_change_jan", "cashflow", "Net Change", "Jan"),
    ]
    formulas = [
        FormulaDef("Net cash flow January", "O + I + F",
                   {"O": "operating_jan", "I": "investing_jan", "F": "financing_jan"}),
    ]
    return WorkbookSpec(
        "cashflow_signs.xlsx", "hard", "finance",
        ["paren_negatives", "sign_convention", "transposed", "totals_row"],
        "Net cash flow = operating + investing + financing (outflows are negative).",
        [t1, t2], measures, formulas,
    )


def wb_segment_report() -> WorkbookSpec:
    rows = ["Cloud", "Hardware", "Services"]
    cols = {
        "Revenue": [124500, 88200, 45300],
        "OperatingIncome": [31200, 9800, 12100],
        "Assets": [210000, 156000, 64000],
    }
    data = _grid(rows, cols)
    foot = {("Cloud", "Revenue"), ("Hardware", "OperatingIncome")}
    t = TableSpec(
        "segments", "Segment Report", "Segments", (1, 1), "Segment",
        [ColSpec("Revenue", "USD"), ColSpec("OperatingIncome", "USD"),
         ColSpec("Assets", "USD")],
        rows, data,
        title="Segment Report (USD thousands)",
        thousands_text=True, footnote_cells=foot,
        traps=["thousands_text", "footnote_markers", "numbers_as_text"],
    )
    # footnote definitions mini-table below
    frows = ["(a)"]
    fdata = {("(a)", "Note"): "Restated for FX"}
    t2 = TableSpec(
        "footnotes", "Footnotes", "Segments", (8, 1), "Marker",
        [ColSpec("Note", None, "string")], frows, fdata,
        traps=["footnote_table"],
    )
    measures = [
        MeasureDef("revenue_cloud", "segments", "Cloud", "Revenue"),
        MeasureDef("opinc_cloud", "segments", "Cloud", "OperatingIncome"),
        MeasureDef("revenue_hardware", "segments", "Hardware", "Revenue"),
        MeasureDef("opinc_hardware", "segments", "Hardware", "OperatingIncome"),
    ]
    formulas = [
        FormulaDef("Cloud operating margin pct", "I / R * 100",
                   {"I": "opinc_cloud", "R": "revenue_cloud"}),
    ]
    return WorkbookSpec(
        "segment_report.xlsx", "hard", "finance",
        ["thousands_text", "footnote_markers", "numbers_as_text", "footnote_table"],
        "Operating margin = operating income divided by revenue, as a percent.",
        [t, t2], measures, formulas,
    )


def wb_dup_tables() -> WorkbookSpec:
    rows = ["EMEA", "APAC", "NorthAm"]
    cols = {"Revenue": [1000, 800, 1500], "Units": [50, 40, 75]}
    data = _grid(rows, cols)
    summary = TableSpec(
        "regional_sales_summary", "Regional Sales", "Summary", (1, 1), "Region",
        [ColSpec("Revenue", "USD"), ColSpec("Units", "count")],
        rows, data,
        traps=["duplicate_table"],
    )
    # detail: same data + an extra column => same logical table, different layout
    ddata = dict(data)
    for r in rows:
        ddata[(r, "CostPerUnit")] = {"EMEA": 12, "APAC": 14, "NorthAm": 11}[r]
    detail = TableSpec(
        "regional_sales_detail", "Regional Sales", "Detail", (1, 1), "Region",
        [ColSpec("Revenue", "USD"), ColSpec("Units", "count"),
         ColSpec("CostPerUnit", "USD")],
        rows, ddata,
        is_duplicate_of="regional_sales_summary",
        traps=["duplicate_table", "canonicalisation"],
    )
    measures = [
        MeasureDef("revenue_emea", "regional_sales_summary", "EMEA", "Revenue",
                   ["EMEA revenue"]),
        MeasureDef("units_emea", "regional_sales_summary", "EMEA", "Units"),
        MeasureDef("cost_per_unit_emea", "regional_sales_detail", "EMEA",
                   "CostPerUnit"),
    ]
    formulas = [
        FormulaDef("EMEA gross margin", "R - U * C",
                   {"R": "revenue_emea", "U": "units_emea", "C": "cost_per_unit_emea"}),
    ]
    return WorkbookSpec(
        "dup_tables.xlsx", "hard", "sales",
        ["duplicate_table", "canonicalisation", "multi_sheet"],
        "Gross margin = revenue minus units times unit cost; the same Regional "
        "Sales table appears on two sheets and must be canonicalised.",
        [summary, detail], measures, formulas,
    )


def wb_large_ledger() -> WorkbookSpec:
    rng = random.Random(1234)
    regions = ["EMEA", "APAC", "NorthAm", "LatAm"]
    products = ["Widget", "Gadget", "Gizmo", "Sprocket"]
    n = 12000
    led_rows = [f"T{100000 + i}" for i in range(n)]
    region_of, product_of, amount_of, qty_of, date_of = {}, {}, {}, {}, {}
    totals_amt = {r: 0 for r in regions}
    totals_qty = {r: 0 for r in regions}
    for i, tid in enumerate(led_rows):
        reg = regions[rng.randrange(len(regions))]
        prod = products[rng.randrange(len(products))]
        amt = rng.randint(10, 500)
        qty = rng.randint(1, 20)
        d = f"2024-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}"
        region_of[tid], product_of[tid] = reg, prod
        amount_of[tid], qty_of[tid], date_of[tid] = amt, qty, d
        totals_amt[reg] += amt
        totals_qty[reg] += qty
    led_data = {}
    for tid in led_rows:
        led_data[(tid, "Date")] = date_of[tid]
        led_data[(tid, "Region")] = region_of[tid]
        led_data[(tid, "Product")] = product_of[tid]
        led_data[(tid, "Amount")] = amount_of[tid]
        led_data[(tid, "Qty")] = qty_of[tid]
    ledger = TableSpec(
        "ledger", "Transaction Ledger", "Transactions", (1, 1), "TxnID",
        [ColSpec("Date", None, "date"), ColSpec("Region", None, "string"),
         ColSpec("Product", None, "string"), ColSpec("Amount", "USD"),
         ColSpec("Qty", "count")],
        led_rows, led_data,
        traps=["large_scale", "12k_rows"],
    )
    sdata = {}
    for r in regions:
        sdata[(r, "TotalAmount")] = totals_amt[r]
        sdata[(r, "TotalQty")] = totals_qty[r]
    summary = TableSpec(
        "ledger_summary", "Regional Totals", "Summary", (1, 1), "Region",
        [ColSpec("TotalAmount", "USD"), ColSpec("TotalQty", "count")],
        regions, sdata,
        traps=["aggregate_table"],
    )
    measures = [
        MeasureDef("total_amount_emea", "ledger_summary", "EMEA", "TotalAmount"),
        MeasureDef("total_amount_apac", "ledger_summary", "APAC", "TotalAmount"),
        MeasureDef("total_amount_northam", "ledger_summary", "NorthAm", "TotalAmount"),
        MeasureDef("total_amount_latam", "ledger_summary", "LatAm", "TotalAmount"),
    ]
    formulas = [
        FormulaDef("Company-wide total amount", "E + A + N + L",
                   {"E": "total_amount_emea", "A": "total_amount_apac",
                    "N": "total_amount_northam", "L": "total_amount_latam"}),
    ]
    return WorkbookSpec(
        "large_ledger.xlsx", "hard", "sales",
        ["large_scale", "12k_rows", "aggregate_table", "multi_sheet"],
        "Company total = sum of regional totals; regional totals aggregate the "
        "transaction ledger by region.",
        [ledger, summary], measures, formulas,
    )


ALL_BUILDERS = [
    wb_sales_regional, wb_headcount, wb_inventory, wb_expenses, wb_pricing,
    wb_quarterly_pnl, wb_multi_region_sales, wb_store_ops, wb_vendor_spend, wb_capex,
    wb_consolidated_pnl, wb_cashflow_signs, wb_segment_report, wb_dup_tables,
    wb_large_ledger,
]


def all_specs() -> list[WorkbookSpec]:
    return [b() for b in ALL_BUILDERS]
