"""Subagent package: analyze one band into a SegmentReport.

The swarm depends only on the `Subagent` port (`analyze(task) -> SegmentReport`) and is
unaware whether deterministic static analysis, a one-shot LLM verify, or a full ReAct
agent ran behind it. `build_subagent` selects the implementation from config at the
composition root; `analyze_band` is a back-compat shim for legacy callers/tests.
"""
from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from mcg_swarm.schemas import SegmentReport
from mcg_swarm.subagent.task import BandTask
from mcg_swarm.subagent.static import HeaderVerification, StaticSubagent


@runtime_checkable
class Subagent(Protocol):
    def analyze(self, task: BandTask) -> SegmentReport: ...


def build_subagent(llm=None) -> "Subagent":
    """Construct the configured subagent.

    `MCG_SUBAGENT=static` (default) → `StaticSubagent`. `react` enables the escalating
    verifier (wired in a later step); it falls back to static when unavailable.
    """
    mode = os.environ.get("MCG_SUBAGENT", "static").strip().lower()
    if mode == "react":
        # ReAct wiring lands with escalating.py / sdk_runner.py; static until then.
        return StaticSubagent(llm)
    return StaticSubagent(llm)


def analyze_band(path, band, header, llm=None) -> SegmentReport:
    """Back-compat shim: build a minimal BandTask (no handle signals) and run static analysis."""
    task = BandTask(path=path, band=band, header=list(header))
    return StaticSubagent(llm).analyze(task)


__all__ = [
    "Subagent", "BandTask", "StaticSubagent", "HeaderVerification",
    "build_subagent", "analyze_band",
]
