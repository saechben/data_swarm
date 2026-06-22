import openpyxl
from dataclasses import replace
from mcg_swarm.header_llm import resolve_messy_tab
from mcg_swarm.splitter import split_workbook
from mcg_swarm.llm.client import FakeLLMClient


def _wb(tmp_path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    for r in rows:
        ws.append(r)
    p = tmp_path / "t.xlsx"
    wb.save(p)
    return str(p)


def test_llm_resolves_offset_banner(tmp_path):
    p = _wb(tmp_path, [["Q3 Report"], [], ["Region", "Revenue"], ["EMEA", 100]])
    h = split_workbook(p)[0]
    if not h.ambiguous:   # if the deterministic detector already nailed it, skip
        return
    fake = FakeLLMClient(responses=[{
        "confident": True,
        "header_row": 3,
        "region": "A3:B4",
        "columns": [
            {"name": "Region", "dtype": "string"},
            {"name": "Revenue", "dtype": "number"},
        ],
    }])
    out = resolve_messy_tab(p, h, fake)
    assert not out.ambiguous and out.header_row == 3 and out.region == "A3:B4"


def test_llm_low_confidence_stays_ambiguous(tmp_path):
    p = _wb(tmp_path, [["junk"], ["more junk"]])
    h = split_workbook(p)[0]
    fake = FakeLLMClient(responses=[{"confident": False}])
    out = resolve_messy_tab(p, h, fake)
    assert out.ambiguous


def test_llm_exception_stays_ambiguous(tmp_path):
    """LLM error must never propagate — handle stays ambiguous."""
    p = _wb(tmp_path, [["junk"], ["more junk"]])
    h = split_workbook(p)[0]

    class BrokenLLM:
        def complete(self, system, user, *, schema=None):
            raise RuntimeError("network failure")

    out = resolve_messy_tab(p, h, BrokenLLM())
    assert out.ambiguous
    assert "llm header fallback error" in out.reason


def test_confident_but_malformed_stays_ambiguous(tmp_path):
    """confident=True but missing region/header_row/columns must not raise."""
    p = _wb(tmp_path, [["A", "B"], [1, 2]])
    h = split_workbook(p)[0]
    fake = FakeLLMClient(responses=[{"confident": True}])  # missing required fields
    out = resolve_messy_tab(p, h, fake)
    assert out.ambiguous
    assert "error" in out.reason


def test_bad_sheet_name_stays_ambiguous(tmp_path):
    """A handle whose .sheet does not exist in the workbook must not raise."""
    p = _wb(tmp_path, [["A", "B"], [1, 2]])
    h = split_workbook(p)[0]
    bad_handle = replace(h, sheet="NoSuchSheet")
    fake = FakeLLMClient(responses=[{"confident": True, "header_row": 1, "region": "A1:B2",
                                     "columns": [{"name": "A"}, {"name": "B"}]}])
    out = resolve_messy_tab(p, bad_handle, fake)
    assert out.ambiguous
