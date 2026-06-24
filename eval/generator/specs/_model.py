"""Shared spec dataclasses and the grid helper used by every workbook builder."""
from __future__ import annotations

from dataclasses import dataclass, field


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
    tables: list  # list[TableSpec]
    measures: list[MeasureDef]
    formulas: list[FormulaDef]


def _grid(rows, col_values):
    """col_values: {col_label: [v aligned with rows]} -> {(row,col): v}."""
    data = {}
    for i, r in enumerate(rows):
        for c, vals in col_values.items():
            data[(r, c)] = vals[i]
    return data
