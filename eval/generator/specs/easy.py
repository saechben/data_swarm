"""Tier 1 — easy: single clean table, header row 1, anchor A1, no traps."""
from __future__ import annotations

from eval.generator.tables import ColSpec, TableSpec
from eval.generator.specs._model import FormulaDef, MeasureDef, WorkbookSpec, _grid


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
