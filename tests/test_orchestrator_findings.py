"""Orchestrator routes errors + injected detection findings through findings[]."""
from mcg_swarm.orchestrator import orchestrate_table
from mcg_swarm.splitter import split_workbook
from mcg_swarm.schemas import Finding
from mcg_swarm.source import as_source


def _wb(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Data"
    ws.append(["Region", "Rev"]); ws.append(["NA", 1]); ws.append(["EU", 2])
    p = tmp_path / "ok.xlsx"; wb.save(p)
    return str(p)


def test_detect_findings_merged_into_table(tmp_path):
    path = _wb(tmp_path)
    src = as_source(path)
    handle = split_workbook(src)[0]
    extra = [Finding(category="empty-header-corner", severity="error", scope="table",
                     message="corner empty", source="static")]
    t = orchestrate_table(src, handle, table_id="Data__0", detect_findings=extra)
    assert any(f.category == "empty-header-corner" for f in t.findings)
    assert "corner empty" in t.errors   # derived view includes injected error


def test_clean_table_has_no_error_findings(tmp_path):
    path = _wb(tmp_path)
    src = as_source(path)
    handle = split_workbook(src)[0]
    t = orchestrate_table(src, handle, table_id="Data__0")
    assert [f for f in t.findings if f.severity == "error"] == []
    assert t.errors == []
