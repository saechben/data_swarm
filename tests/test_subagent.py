import openpyxl
from mcg_swarm.subagent import analyze_band
from mcg_swarm.size_estimate import plan_bands
from mcg_swarm.splitter import split_workbook
from mcg_swarm.llm.client import FakeLLMClient

def _wb(tmp_path, rows):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Data"
    for r in rows: ws.append(r)
    p = tmp_path / "t.xlsx"; wb.save(p); return str(p)

def test_deterministic_report_without_llm(tmp_path):
    p = _wb(tmp_path, [["Region", "Revenue"], ["EMEA", 100], ["APAC", 200]])
    h = split_workbook(p)[0]
    _, _, bands = plan_bands(h)
    rep = analyze_band(p, bands[0], header=[c.name for c in h.columns], llm=None)
    assert [c.name for c in rep.columns] == ["Region", "Revenue"]
    assert rep.columns[1].dtype == "number"

def test_llm_gap_fill_applies_units(tmp_path):
    p = _wb(tmp_path, [["Region", "Revenue"], ["EMEA", 100]])
    h = split_workbook(p)[0]
    _, _, bands = plan_bands(h)
    fake = FakeLLMClient(responses=[{"columns": [
        {"name": "Region", "unit": None, "role": "key"},
        {"name": "Revenue", "unit": "USD", "role": "value"}]}])
    rep = analyze_band(p, bands[0], header=["Region", "Revenue"], llm=fake)
    assert rep.columns[1].unit == "USD"
    assert len(fake.calls) == 1

def test_llm_error_falls_back(tmp_path):
    p = _wb(tmp_path, [["Region", "Revenue"], ["EMEA", 100]])
    h = split_workbook(p)[0]
    _, _, bands = plan_bands(h)
    boom = FakeLLMClient(responses=lambda **k: (_ for _ in ()).throw(RuntimeError("x")))
    rep = analyze_band(p, bands[0], header=["Region", "Revenue"], llm=boom)
    assert [c.name for c in rep.columns] == ["Region", "Revenue"]  # deterministic survives
    assert any("llm" in a.lower() for a in rep.anomalies)
