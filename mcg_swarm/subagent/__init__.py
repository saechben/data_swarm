"""Subagent package: analyze one band into a SegmentReport.

The swarm depends only on the `Subagent` port (`analyze(task) -> SegmentReport`) and is
unaware whether deterministic static analysis or an injected ReAct runner ran behind it.
`build_subagent` / `build_table_validator` take an injected `runner` (an `AgentRunner`)
built by the application; `runner is None` selects the static-only path. The swarm names
no provider and reads no env.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from mcg_swarm.config import SwarmConfig
from mcg_swarm.schemas import SegmentReport
from mcg_swarm.subagent.task import BandTask
from mcg_swarm.subagent.static import HeaderVerification, StaticSubagent


@runtime_checkable
class Subagent(Protocol):
    def analyze(self, task: BandTask) -> SegmentReport: ...


def build_subagent(llm=None, runner=None, config: SwarmConfig = SwarmConfig()) -> "Subagent":
    """Construct the band-level subagent.

    `runner is None` → deterministic `StaticSubagent`. Otherwise the escalating subagent:
    static-first, with the injected ReAct `runner` verifying on trouble (and on clean
    bands too when `config.validate`).
    """
    if runner is None:
        return StaticSubagent(llm)
    from mcg_swarm.subagent.escalating import EscalatingSubagent, EscalationPolicy
    from mcg_swarm.subagent.verifier import ReActVerifier
    return EscalatingSubagent(
        StaticSubagent(llm),
        ReActVerifier(runner),
        EscalationPolicy(validate=config.validate),
    )


def build_table_validator(runner=None, config: SwarmConfig = SwarmConfig()):
    """Construct the table-level validator, or `None` when no runner is injected."""
    if runner is None:
        return None
    from mcg_swarm.subagent.table_check import TableCheckPolicy, TableValidator
    return TableValidator(
        runner,
        TableCheckPolicy(validate=config.validate, max_passes=config.repair_max_passes),
    )


def build_structural_reviewer(runner=None, config: SwarmConfig = SwarmConfig()):
    """Construct the sheet-level structural reviewer, or `None`.

    None when no runner is injected or `config.alter_boundaries` is False (detection-only).
    """
    if runner is None or not config.alter_boundaries:
        return None
    from mcg_swarm.subagent.structural import StructuralReviewer
    return StructuralReviewer(runner)


def analyze_band(path, band, header, llm=None) -> SegmentReport:
    """Back-compat shim: build a minimal BandTask (no handle signals) and run static analysis."""
    task = BandTask(path=path, band=band, header=list(header))
    return StaticSubagent(llm).analyze(task)


__all__ = [
    "Subagent", "BandTask", "StaticSubagent", "HeaderVerification",
    "build_subagent", "build_table_validator", "build_structural_reviewer", "analyze_band",
]
