"""Serialized ground-truth label schema for the eval pipeline.

Every workbook the generator produces gets one ``WorkbookLabel`` JSON sidecar.
These objects are the *ground truth* the harness scores against. They are derived
from the same in-memory data used to write the xlsx, and re-verified against the
physical file by ``verify.py``.

Nothing in here knows about the swarm. The swarm's outputs are obtained through an
``EvalAdapter`` (see ``adapters/``) and compared to these labels by ``harness/``.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

ValueType = Literal["number", "string", "boolean", "date"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# Table structure labels
# --------------------------------------------------------------------------- #
class ColumnLabel(_Base):
    label: str = Field(description="Canonical column header label, units stripped.")
    col_index: int = Field(description="1-based absolute column index in the sheet.")
    col_letter: str = Field(description="Excel column letter, e.g. 'C'.")
    dtype: ValueType
    unit: Optional[str] = Field(default=None, description="e.g. 'USD', '%', 'count'.")


class RowKeyLabel(_Base):
    label: str = Field(description="Row key (entity) label, e.g. 'EMEA'.")
    row_index: int = Field(description="1-based absolute row index in the sheet.")


class CellFact(_Base):
    """A single value cell with both its ground-truth value and its literal text."""

    row_label: str
    col_label: str
    cell_ref: str = Field(description="Excel A1 reference, e.g. 'C5'.")
    value: Any = Field(description="Ground-truth typed value (footnotes/parens cleaned).")
    raw: Any = Field(description="What literally sits in the cell, e.g. '1,234 (a)' or '=A1*B1'.")
    is_formula: bool = Field(
        default=False,
        description="True if the cell holds an Excel formula; `raw` is the formula "
        "text and `value` is its recalculated result (cached in the file).",
    )


class TableLabel(_Base):
    table_id: str
    name: str = Field(description="Canonical logical table name.")
    sheet: str
    region: str = Field(description="Full bounding box incl title+header, e.g. 'B3:F14'.")
    header_region: str = Field(description="Header row(s) region, e.g. 'B4:F4'.")
    data_region: str = Field(description="Body region (incl totals rows), e.g. 'B5:F14'.")
    orientation: Literal["vertical", "transposed", "matrix"] = "vertical"
    columns: list[ColumnLabel]
    row_keys: list[RowKeyLabel]
    cells: list[CellFact]
    traps: list[str] = Field(default_factory=list)
    is_duplicate_of: Optional[str] = Field(
        default=None, description="table_id this is a duplicate of (canonicalisation test)."
    )


class MeasureLabel(_Base):
    """A semantically named value relevant to the workbook's business logic.

    This is what the per-table orchestrator is expected to surface as a
    canonical measure/column mapped to a (table, row, column) location.
    """

    semantic_name: str
    aliases: list[str] = Field(default_factory=list)
    table_id: str
    row_label: str
    col_label: str
    unit: Optional[str] = None
    value: Any


# --------------------------------------------------------------------------- #
# Validation samples (discriminated union on `type`)
# --------------------------------------------------------------------------- #
class ExtractionSample(_Base):
    """Structured extraction matching the script contract query(row, column)."""

    id: str
    type: Literal["extraction"] = "extraction"
    sheet: str
    table_id: str
    table: str
    row_label: str
    col_label: str
    expected_value: Any
    expected_cell_ref: str
    dtype: ValueType
    tolerance: float = 1e-9


class SemanticSample(_Base):
    """Natural-language query -> value + which measure/location should answer it."""

    id: str
    type: Literal["semantic"] = "semantic"
    query: str
    expected_value: Any
    expected_table_id: str
    expected_row_label: str
    expected_col_label: str
    dtype: ValueType
    tolerance: float = 1e-9


class BoundarySample(_Base):
    """Table-boundary assertion: expected full bounding box for a table."""

    id: str
    type: Literal["boundary"] = "boundary"
    sheet: str
    table_id: str
    table_name: str
    expected_region: str
    min_iou: float = Field(default=0.999, description="Min cell-IoU to count as correct.")


class FormulaSample(_Base):
    """Intra-table formula compute check over named measures."""

    id: str
    type: Literal["formula"] = "formula"
    description: str
    business_logic: str
    expression: str = Field(description="e.g. 'Revenue - Units * CostPerUnit'.")
    operands: dict[str, str] = Field(description="operand symbol -> measure semantic_name.")
    inputs: dict[str, Any] = Field(description="operand symbol -> ground-truth value.")
    expected_value: Any
    tolerance: float = 1e-6


Sample = Annotated[
    Union[ExtractionSample, SemanticSample, BoundarySample, FormulaSample],
    Field(discriminator="type"),
]


class WorkbookLabel(_Base):
    workbook: str = Field(description="Filename, e.g. 'sales_regional.xlsx'.")
    rel_path: str = Field(description="Path relative to eval/data/, e.g. 'workbooks/...'.")
    difficulty: Literal["easy", "medium", "hard"]
    domain: str
    traps: list[str]
    sheets: list[str]
    business_logic: str
    tables: list[TableLabel]
    measures: list[MeasureLabel]
    samples: list[Sample]

    # convenience -------------------------------------------------------------
    def table(self, table_id: str) -> TableLabel:
        for t in self.tables:
            if t.table_id == table_id:
                return t
        raise KeyError(table_id)

    def measure(self, semantic_name: str) -> MeasureLabel:
        for m in self.measures:
            if m.semantic_name == semantic_name:
                return m
        raise KeyError(semantic_name)
