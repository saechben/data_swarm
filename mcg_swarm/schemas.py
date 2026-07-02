from __future__ import annotations
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")

class ColumnSpec(_Base):
    name: str
    dtype: Literal["number", "string", "boolean", "date"]
    unit: Optional[str] = None
    role: Literal["key", "value", "computed"] = "value"

_GATE_PREFIXES = [
    ("coverage gap", "coverage-gap"),
    ("column-name", "column-name"),
    ("column-integrity", "column-integrity"),
    ("column-coverage", "column-coverage"),
    ("row-integrity", "row-integrity"),
    ("row-key collision", "row-key-collision"),
    ("blank row key", "blank-row-key"),
    ("empty index", "empty-index"),
    ("round-trip", "round-trip"),
    ("dtype-mismatch", "dtype-mismatch"),
    ("computed", "computed"),
]


class Finding(_Base):
    category: str
    severity: Literal["error", "warning", "info"]
    scope: Literal["workbook", "sheet", "table", "column", "cell"]
    message: str
    source: Literal["static", "gate", "agent"]
    ref: Optional[str] = None
    agent_action: Optional[str] = None
    resolution: Literal["fixed", "open", "rejected"] = "open"


def finding_from_gate_failure(msg: str) -> "Finding":
    """Map a legacy gate failure string to a Finding (category by prefix)."""
    category = "other"
    for prefix, cat in _GATE_PREFIXES:
        if msg.startswith(prefix):
            category = cat
            break
    return Finding(category=category, severity="error", scope="table",
                   message=msg, source="gate")

class OperandBinding(_Base):
    name: str
    source: Literal["column", "cell", "range", "param"]
    ref: str

class TableFormula(_Base):
    target: str
    expression: str
    operands: list[OperandBinding] = Field(default_factory=list)
    ast: Optional[dict] = None
    context: Optional[str] = None

class ExtractionRef(_Base):
    script_name: str
    row_key: list[str] = Field(default_factory=list)
    notes: Optional[str] = None

class CanonicalTable(_Base):
    table_id: str
    sheet: str
    region: str
    header_row: int
    header_span: int = 1
    orientation: Literal["vertical", "transposed"] = "vertical"
    columns: list[ColumnSpec] = Field(default_factory=list)
    formulas: list[TableFormula] = Field(default_factory=list)
    description: str = ""
    extraction: ExtractionRef
    provisional_notes: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)

    @model_validator(mode="after")
    def _derive_views(self):
        if self.findings:
            self.errors = [f.message for f in self.findings if f.severity == "error"]
            self.provisional_notes = [
                f.message for f in self.findings if f.severity in ("warning", "info")
            ]
        return self

class WorkbookExtraction(_Base):
    workbook: str
    sheets: list[str] = Field(default_factory=list)
    tables: list[CanonicalTable] = Field(default_factory=list)
    generator_version: str
    errors: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)

    @model_validator(mode="after")
    def _derive_errors(self):
        if self.findings:
            self.errors = [f.message for f in self.findings if f.severity == "error"]
        return self

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
