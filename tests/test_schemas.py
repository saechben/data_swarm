import pytest
from mcg_swarm.schemas import (
    ColumnSpec, OperandBinding, TableFormula, ExtractionRef,
    CanonicalTable, WorkbookExtraction, SegmentReport, ExtractedValue,
)

def test_column_spec_defaults():
    c = ColumnSpec(name="Revenue", dtype="number")
    assert c.role == "value" and c.unit is None

def test_table_formula_holds_operands_and_ast():
    f = TableFormula(
        target="Net", expression="Gross - Discount",
        operands=[OperandBinding(name="Gross", source="column", ref="Gross"),
                  OperandBinding(name="Discount", source="column", ref="Discount")],
        ast={"op": "-", "args": [{"var": "Gross"}, {"var": "Discount"}]},
    )
    assert f.operands[0].source == "column"

def test_canonical_table_round_trips_and_defaults_empty_errors():
    t = CanonicalTable(
        table_id="t1", sheet="Sheet1", region="A1:E5", header_row=1,
        columns=[ColumnSpec(name="Region", dtype="string", role="key")],
        description="d", extraction=ExtractionRef(script_name="idx_t1", row_key=["Region"]),
    )
    assert t.orientation == "vertical" and t.errors == [] and t.formulas == []
    assert CanonicalTable.model_validate_json(t.model_dump_json()).table_id == "t1"

def test_extracted_value_provenance():
    v = ExtractedValue(value=42, dtype="number", sheet="Sheet1", cell_ref="C5")
    assert v.is_computed is False and v.unit is None

def test_extra_fields_forbidden():
    with pytest.raises(Exception):
        ColumnSpec(name="x", dtype="number", bogus=1)

def test_table_formula_context_field():
    from mcg_swarm.schemas import TableFormula
    f = TableFormula(target="Total", expression="A+B", context="Total is A plus B")
    assert f.context == "Total is A plus B"
    # backward-compatible: context is optional and defaults to None
    g = TableFormula(target="Total", expression="A+B")
    assert g.context is None
