from __future__ import annotations
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")

class ColumnSpec(_Base):
    name: str
    dtype: Literal["number", "string", "boolean", "date"]
    unit: Optional[str] = None
    role: Literal["key", "value", "computed"] = "value"

class OperandBinding(_Base):
    name: str
    source: Literal["column", "cell", "range", "param"]
    ref: str

class TableFormula(_Base):
    target: str
    expression: str
    operands: list[OperandBinding] = Field(default_factory=list)
    ast: Optional[dict] = None

class ExtractionRef(_Base):
    script_name: str
    row_key: list[str] = Field(default_factory=list)
    notes: Optional[str] = None

class CanonicalTable(_Base):
    table_id: str
    sheet: str
    region: str
    header_row: int
    orientation: Literal["vertical", "transposed"] = "vertical"
    columns: list[ColumnSpec] = Field(default_factory=list)
    formulas: list[TableFormula] = Field(default_factory=list)
    description: str = ""
    extraction: ExtractionRef
    provisional_notes: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

class WorkbookExtraction(_Base):
    workbook: str
    sheets: list[str] = Field(default_factory=list)
    tables: list[CanonicalTable] = Field(default_factory=list)
    generator_version: str
    errors: list[str] = Field(default_factory=list)

class SegmentReport(_Base):
    band: str
    columns: list[ColumnSpec] = Field(default_factory=list)
    formulas: list[TableFormula] = Field(default_factory=list)
    description: str = ""
    anomalies: list[str] = Field(default_factory=list)

class ExtractedValue(_Base):
    value: Any
    dtype: str
    unit: Optional[str] = None
    sheet: str
    cell_ref: str
    is_computed: bool = False
