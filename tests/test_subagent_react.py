"""Tests for ReActVerifier + FakeAgentRunner + the digest (fully offline)."""
import openpyxl

from mcg_swarm.size_estimate import Band
from mcg_swarm.schemas import ColumnSpec, SegmentReport
from mcg_swarm.subagent.task import BandTask, build_digest, role_disagreements
from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from mcg_swarm.subagent.verifier import ReActVerifier, SegmentReportPatch


def _wb(tmp_path):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Data"
    ws.append(["Quarterly Sales", None, None])
    ws.append(["Region", "Revenue", "Units"])
    ws.append(["EMEA", 100, 5])
    ws.append(["APAC", 200, 8])
    ws.append(["Total", 300, 13])
    p = tmp_path / "t.xlsx"; wb.save(p); return str(p)


def _band():
    return Band(sheet="Data", header_row=2, region="A2:C5",
                col_start=1, col_end=3, row_start=3, row_end=5)


def _cols(revenue_role="value"):
    return [ColumnSpec(name="Region", dtype="string", role="key"),
            ColumnSpec(name="Revenue", dtype="number", role=revenue_role),
            ColumnSpec(name="Units", dtype="number", role="value")]


def _task(p, **kw):
    return BandTask(path=p, band=_band(), header=["Region", "Revenue", "Units"],
                    handle_columns=_cols(), **kw)


def _static():
    return SegmentReport(band="A2:C5", columns=_cols(), formulas=[],
                         description="d", anomalies=[])


def test_verifier_applies_patch_after_probing(tmp_path):
    p = _wb(tmp_path)
    runner = FakeAgentRunner(
        actions=[{"tool": "tail_rows", "args": {"count": 1}}],
        final={"columns": [{"name": "Revenue", "unit": "USD"}],
               "anomalies": ["totals row present"]})
    rep = ReActVerifier(runner).verify(_task(p), _static())
    assert rep.columns[1].unit == "USD"
    assert "totals row present" in rep.anomalies
    # the scripted probe really executed against the band snapshot
    assert runner.observations[0]["rows"][-1]["cells"][0] == "Total"


def test_verifier_falls_back_on_runner_error(tmp_path):
    p = _wb(tmp_path)

    class Boom:
        def run(self, seed, tools, *, schema):
            raise RuntimeError("agent exploded")

    rep = ReActVerifier(Boom()).verify(_task(p), _static())
    assert [c.name for c in rep.columns] == ["Region", "Revenue", "Units"]
    assert rep.columns[1].unit is None  # unchanged


def test_role_disagreements_and_digest():
    static = _static()  # Revenue inferred 'value'
    task = BandTask(path="x", band=_band(), header=["Region", "Revenue", "Units"],
                    handle_columns=_cols(revenue_role="computed"),  # splitter says 'computed'
                    ambiguous=True, reason="messy headers")
    dis = role_disagreements(static.columns, task.handle_columns)
    assert any(d["name"] == "Revenue" for d in dis)
    txt = build_digest(task, static).to_prompt()
    assert "Revenue" in txt and "messy headers" in txt


def test_patch_schema_validates():
    SegmentReportPatch.model_validate({"columns": [{"name": "X", "role": "value"}]})
