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


@dataclass
class EscalationPolicy:
    """Decides whether to run the ReAct verifier for a band.

    mode:
      "always"   — validate every eligible band (double-check static), subject only to
                   the size guard. This is the validation default when react is enabled.
      "on_error" — validate only when static flagged a problem (anomalies, the splitter
                   was ambiguous, or splitter/static role disagreement). Conservative.

    The size guard always applies: large data tables (> max_table_rows) are never
    validated — static is reliable there and an agent would be slow and costly.
    """
    max_table_rows: int = REACT_MAX_TABLE_ROWS
    mode: str = "on_error"

    def should_escalate(self, task: BandTask, static_report: SegmentReport) -> bool:
        n_data_rows = task.band.row_end - task.band.row_start + 1
        if n_data_rows > self.max_table_rows:
            return False
        if self.mode == "always":
            return True
        return (
            task.ambiguous
            or bool(static_report.anomalies)
            or bool(role_disagreements(static_report.columns, task.handle_columns))
        )


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
