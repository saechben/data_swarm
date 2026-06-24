"""Tier 3 — hard: multi-level headers, sign traps, footnotes/text numbers,
duplicate tables, large scale.
"""
from __future__ import annotations

import random

from eval.generator.tables import ColSpec, TableSpec
from eval.generator.specs._model import FormulaDef, MeasureDef, WorkbookSpec, _grid


def wb_consolidated_pnl() -> WorkbookSpec:
    rows = ["Revenue", "COGS", "OpEx"]
    # Actual/Budget repeat across groups, so give leaf columns unique labels but
    # keep the group merged header.
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
