"""EscalatingSubagent — static-first, ReAct-verify on trouble.

Runs the deterministic static pass, then escalates to the ReAct verifier only when it is
worth it (small table) AND something looks off (the splitter was unsure, static produced
anomalies, or the splitter and static disagree on a column's role). Otherwise the static
result stands. This is where "jump in if there's an error" + "verify the static output"
live; the orchestrator stays unaware of any of it.
"""
from __future__ import annotations

from dataclasses import dataclass

from mcg_swarm.schemas import SegmentReport
from mcg_swarm.subagent.task import BandTask, role_disagreements

# Large data tables carry no labeled measures and are where static analysis is already
# reliable; running an agent there is slow and costly. Mirrors swarm_adapter's cap.
REACT_MAX_TABLE_ROWS = 40


def _static_failing(task: BandTask, static_report: SegmentReport) -> bool:
    """True when the band's static analysis looks problematic (the fallback trigger)."""
    return (
        task.ambiguous
        or bool(static_report.anomalies)
        or bool(role_disagreements(static_report.columns, task.handle_columns))
    )


@dataclass
class EscalationPolicy:
    """Decides whether to run the ReAct verifier for a band.

    Two additive triggers:
      - **fallback (always on):** run when static looks problematic (ambiguous, anomalies,
        or splitter/static role disagreement). Not configurable.
      - **validation (configurable):** when ``validate`` is True, also run on otherwise-
        clean bands to double-check static.

    The size guard always applies: large data tables (> ``max_table_rows``) are never
    sent to the agent — static is reliable there and an agent would be slow and costly.
    """
    max_table_rows: int = REACT_MAX_TABLE_ROWS
    validate: bool = False

    def should_escalate(self, task: BandTask, static_report: SegmentReport) -> bool:
        n_data_rows = task.band.row_end - task.band.row_start + 1
        if n_data_rows > self.max_table_rows:
            return False
        return self.validate or _static_failing(task, static_report)


class EscalatingSubagent:
    """Implements the Subagent port: static analysis, with ReAct verification on trouble."""

    def __init__(self, static, verifier, policy: EscalationPolicy | None = None) -> None:
        self._static = static
        self._verifier = verifier
        self._policy = policy or EscalationPolicy()

    def analyze(self, task: BandTask) -> SegmentReport:
        report = self._static.analyze(task)
        if self._policy.should_escalate(task, report):
            return self._verifier.verify(task, report)
        return report
