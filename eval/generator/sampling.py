"""Shared label assembly: resolve measures, generate mixed samples, prune cells.

Used by both the spec-driven generator (build.py) and the bespoke extreme
stress-test workbooks (hard_workbooks.py), so sampling/scoring stays identical.
"""
from __future__ import annotations

import random
from typing import Iterable

from eval.schemas import (
    BoundarySample,
    ExtractionSample,
    FormulaSample,
    MeasureLabel,
    SemanticSample,
    TableLabel,
)
from eval.util import safe_eval

CELL_CAP = 500


def value_index(tables: list[TableLabel]) -> dict[tuple[str, str, str], object]:
    idx = {}
    for t in tables:
        for c in t.cells:
            idx[(t.table_id, c.row_label, c.col_label)] = c
    return idx


def resolve_measures(tables: list[TableLabel], measure_defs) -> list[MeasureLabel]:
    idx = value_index(tables)
    out = []
    for m in measure_defs:
        cell = idx[(m.table_id, m.row_label, m.col_label)]
        unit = None
        tbl = next(t for t in tables if t.table_id == m.table_id)
        for col in tbl.columns:
            if col.label == m.col_label:
                unit = col.unit
        out.append(MeasureLabel(
            semantic_name=m.semantic_name, aliases=m.aliases, table_id=m.table_id,
            row_label=m.row_label, col_label=m.col_label, unit=unit, value=cell.value,
        ))
    return out


def _phrase(rng, table, row, col, unit, disambiguate):
    name = table.name
    if disambiguate:
        # (row, col) isn't unique across this workbook -> must name the table/sheet
        templates = [
            f"What is the {col} for {row} in {name}?",
            f"Find the {col} of {row} in the {name} table.",
            f"In {name} ({table.sheet}), what is {row}'s {col}?",
        ]
    else:
        templates = [
            f"What is the {col} for {row} in {name}?",
            f"Give me {row}'s {col}.",
            f"{row} {col}",
            f"How much {col} did {row} have?",
            f"Find the {col} of {row} in the {name} table.",
        ]
        if unit == "%":
            templates.append(f"What is the {col} percentage for {row}?")
        if unit == "USD":
            templates.append(f"What is {row}'s {col} in dollars?")
    return rng.choice(templates)


def make_samples(filename, tables, measures, formula_defs, business_logic,
                 prioritize_formulas=False, seed=None):
    rng = random.Random(seed if seed is not None else (hash(filename) & 0xFFFFFFFF))
    measure_by_name = {m.semantic_name: m for m in measures}
    samples: list = []
    referenced: dict[str, set] = {t.table_id: set() for t in tables}

    for t in tables:
        samples.append(BoundarySample(
            id=f"{filename}:bnd:{t.table_id}", sheet=t.sheet, table_id=t.table_id,
            table_name=t.name, expected_region=t.region))

    for i, f in enumerate(formula_defs):
        names = {sym: float(measure_by_name[mn].value) for sym, mn in f.operands.items()}
        expected = safe_eval(f.expression, names)
        samples.append(FormulaSample(
            id=f"{filename}:fml:{i}", description=f.description,
            business_logic=business_logic, expression=f.expression,
            operands=dict(f.operands), inputs=names, expected_value=expected))
        for mn in f.operands.values():
            m = measure_by_name[mn]
            referenced[m.table_id].add((m.row_label, m.col_label))

    numeric_pool = [
        (t, c) for t in tables for c in t.cells
        if isinstance(c.value, (int, float)) and not isinstance(c.value, bool)
    ]
    string_pool = [
        (t, c) for t in tables for c in t.cells if isinstance(c.value, str)
    ]
    rng.shuffle(numeric_pool)
    rng.shuffle(string_pool)
    if prioritize_formulas:  # surface reference/formula cells in extraction first
        numeric_pool.sort(key=lambda tc: not tc[1].is_formula)

    extraction_n = min(14, len(numeric_pool))
    chosen = numeric_pool[:extraction_n] + string_pool[:min(2, len(string_pool))]
    for j, (t, c) in enumerate(chosen):
        dtype = "string" if isinstance(c.value, str) else "number"
        samples.append(ExtractionSample(
            id=f"{filename}:ext:{j}", sheet=t.sheet, table_id=t.table_id, table=t.name,
            row_label=c.row_label, col_label=c.col_label, expected_value=c.value,
            expected_cell_ref=c.cell_ref, dtype=dtype))
        referenced[t.table_id].add((c.row_label, c.col_label))

    # (row_label, col_label) pairs that appear in more than one table need the
    # table named in the query, else the natural-language question is ambiguous.
    pair_tables: dict[tuple[str, str], set[str]] = {}
    for t in tables:
        for c in t.cells:
            pair_tables.setdefault((c.row_label, c.col_label), set()).add(t.table_id)

    need_semantic = max(8, 26 - len(samples))
    used_queries: dict[str, tuple[str, str, str]] = {}
    for j in range(need_semantic):
        t, c = numeric_pool[(j * 3 + 1) % len(numeric_pool)]
        unit = next((col.unit for col in t.columns if col.label == c.col_label), None)
        ambiguous = len(pair_tables[(c.row_label, c.col_label)]) > 1
        query = _phrase(rng, t, c.row_label, c.col_label, unit, ambiguous)
        target = (t.table_id, c.row_label, c.col_label)
        # guarantee unique query text per workbook (safety net)
        if used_queries.get(query, target) != target:
            query = f"{query} [{t.sheet}/{t.table_id}]"
        used_queries[query] = target
        samples.append(SemanticSample(
            id=f"{filename}:sem:{j}", query=query,
            expected_value=c.value, expected_table_id=t.table_id,
            expected_row_label=c.row_label, expected_col_label=c.col_label, dtype="number"))
        referenced[t.table_id].add((c.row_label, c.col_label))

    return samples, referenced


def prune_cells(tables, referenced, rng=None, cap=CELL_CAP):
    rng = rng or random.Random(0)
    for t in tables:
        if len(t.cells) <= cap:
            continue
        keep = set(referenced.get(t.table_id, set()))
        kept = [c for c in t.cells if (c.row_label, c.col_label) in keep]
        rest = [c for c in t.cells if (c.row_label, c.col_label) not in keep]
        rng.shuffle(rest)
        t.cells = kept + rest[: max(0, cap - len(kept))]
