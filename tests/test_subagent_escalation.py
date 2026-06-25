"""Tests for EscalatingSubagent activation policy."""
from mcg_swarm.size_estimate import Band
from mcg_swarm.schemas import ColumnSpec, SegmentReport
from mcg_swarm.subagent.task import BandTask
from mcg_swarm.subagent.escalating import (
    EscalatingSubagent, EscalationPolicy, REACT_MAX_TABLE_ROWS,
)


def _band(row_start=3, row_end=5):
    return Band(sheet="Data", header_row=2, region="A2:C5",
                col_start=1, col_end=3, row_start=row_start, row_end=row_end)


def _cols(revenue_role="value"):
    return [ColumnSpec(name="Region", dtype="string", role="key"),
            ColumnSpec(name="Revenue", dtype="number", role=revenue_role)]


def _report(anomalies=None):
    return SegmentReport(band="A2:C5", columns=_cols(), formulas=[],
                         description="static", anomalies=anomalies or [])


def _task(band=None, handle_roles="value", ambiguous=False):
    return BandTask(path="x", band=band or _band(), header=["Region", "Revenue"],
                    handle_columns=_cols(handle_roles), ambiguous=ambiguous)


class FakeStatic:
    def __init__(self, report):
        self.report = report

    def analyze(self, task):
        return self.report


class SpyVerifier:
    def __init__(self):
        self.called = False

    def verify(self, task, report):
        self.called = True
        return report.model_copy(update={"description": "verified"})


def _run(task, report):
    spy = SpyVerifier()
    sub = EscalatingSubagent(FakeStatic(report), spy)
    return sub.analyze(task), spy


def test_clean_result_does_not_escalate():
    out, spy = _run(_task(), _report())
    assert not spy.called
    assert out.description == "static"


def test_anomaly_escalates_small_table():
    out, spy = _run(_task(), _report(anomalies=["llm verify skipped: x"]))
    assert spy.called
    assert out.description == "verified"


def test_ambiguous_escalates():
    out, spy = _run(_task(ambiguous=True), _report())
    assert spy.called


def test_role_disagreement_escalates():
    # splitter says Revenue 'computed', static report says 'value' -> disagree
    out, spy = _run(_task(handle_roles="computed"), _report())
    assert spy.called


def test_large_table_never_escalates():
    big = _band(row_start=2, row_end=2 + REACT_MAX_TABLE_ROWS + 10)
    out, spy = _run(_task(band=big, ambiguous=True), _report(anomalies=["x"]))
    assert not spy.called  # size guard wins even with trouble signals


# --- validation modes -------------------------------------------------------

def _run_mode(task, report, mode):
    spy = SpyVerifier()
    sub = EscalatingSubagent(FakeStatic(report), spy, EscalationPolicy(mode=mode))
    return sub.analyze(task), spy


def test_always_mode_validates_clean_small_table():
    # 'always' runs the agent even with no trouble signals (validation step).
    out, spy = _run_mode(_task(), _report(), mode="always")
    assert spy.called
    assert out.description == "verified"


def test_always_mode_respects_size_guard():
    big = _band(row_start=2, row_end=2 + REACT_MAX_TABLE_ROWS + 10)
    out, spy = _run_mode(_task(band=big), _report(), mode="always")
    assert not spy.called  # large tables skip validation even in 'always'


def test_on_error_mode_skips_clean_table():
    out, spy = _run_mode(_task(), _report(), mode="on_error")
    assert not spy.called


def test_on_error_mode_escalates_on_anomaly():
    out, spy = _run_mode(_task(), _report(anomalies=["boom"]), mode="on_error")
    assert spy.called


def test_default_mode_is_on_error():
    assert EscalationPolicy().mode == "on_error"


def test_legacy_task_without_handle_columns_no_disagreement():
    task = BandTask(path="x", band=_band(), header=["Region", "Revenue"])  # handle_columns=None
    out, spy = _run(task, _report())  # clean, no signals
    assert not spy.called
