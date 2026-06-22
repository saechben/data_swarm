# tests/test_merge.py
from mcg_swarm.merge import merge_reports
from mcg_swarm.schemas import SegmentReport, ColumnSpec, TableFormula

def _rep(band, cols, formulas=()):
    return SegmentReport(band=band,
        columns=[ColumnSpec(name=n, dtype=d) for n, d in cols],
        formulas=list(formulas), description=f"d{band}")

def test_row_merge_agreement():
    r1 = _rep("A2:B100", [("Region", "string"), ("Rev", "number")])
    r2 = _rep("A101:B200", [("Region", "string"), ("Rev", "number")])
    m = merge_reports([r1, r2], axis="row")
    assert [c.name for c in m.columns] == ["Region", "Rev"] and not m.conflicts

def test_row_merge_disagreement_flags_conflict():
    r1 = _rep("A2:B100", [("Region", "string"), ("Rev", "number")])
    r2 = _rep("A101:B200", [("Region", "string"), ("Rev", "string")])
    m = merge_reports([r1, r2], axis="row")
    assert m.conflicts  # dtype mismatch on Rev

def test_col_merge_concatenates():
    r1 = _rep("A1:A10", [("Region", "string")])
    r2 = _rep("B1:C10", [("Rev", "number"), ("Units", "number")])
    m = merge_reports([r1, r2], axis="col")
    assert [c.name for c in m.columns] == ["Region", "Rev", "Units"]

def test_empty_reports_returns_empty_result():
    for ax in ("row", "col"):
        m = merge_reports([], axis=ax)
        assert m.columns == []
        assert m.formulas == []
        assert m.description == ""
        assert m.conflicts == []

def test_formula_union_dedupe():
    f = TableFormula(target="Net", expression="Gross - Disc")
    m = merge_reports([_rep("A2:B5", [("Gross", "number")], [f]),
                       _rep("A6:B9", [("Gross", "number")], [f])], axis="row")
    assert len(m.formulas) == 1
