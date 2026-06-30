"""Finding record + derived errors/provisional_notes views."""
from mcg_swarm.schemas import (
    Finding, CanonicalTable, WorkbookExtraction, ExtractionRef,
    finding_from_gate_failure,
)


def _table(**kw):
    base = dict(table_id="t", sheet="S", region="A1:B2", header_row=1,
                extraction=ExtractionRef(script_name="idx_t"))
    base.update(kw)
    return CanonicalTable(**base)


def test_findings_derive_errors_and_notes():
    t = _table(findings=[
        Finding(category="dtype-mismatch", severity="error", scope="column",
                message="bad dtype", source="gate"),
        Finding(category="anomaly", severity="info", scope="table",
                message="heads up", source="static"),
    ])
    assert t.errors == ["bad dtype"]
    assert t.provisional_notes == ["heads up"]


def test_legacy_errors_still_work_without_findings():
    t = _table(errors=["legacy error"], provisional_notes=["legacy note"])
    assert t.errors == ["legacy error"]
    assert t.provisional_notes == ["legacy note"]
    assert t.findings == []


def test_workbook_findings_derive_errors():
    wb = WorkbookExtraction(
        workbook="w", generator_version="v",
        findings=[Finding(category="uncovered-data", severity="error",
                          scope="sheet", message="dropped table on S", source="static")],
    )
    assert wb.errors == ["dropped table on S"]


def test_finding_from_gate_failure_categorizes():
    f = finding_from_gate_failure("dtype-mismatch: column 'X' declared number ...")
    assert f.category == "dtype-mismatch"
    assert f.severity == "error" and f.source == "gate"
    f2 = finding_from_gate_failure("computed mismatch Sum@1: live=None calc=13")
    assert f2.category == "computed"
