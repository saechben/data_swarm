"""Subagent package: analyze one band into a SegmentReport.

The swarm depends only on the `Subagent` port (`analyze(task) -> SegmentReport`) and is
unaware whether deterministic static analysis, a one-shot LLM verify, or a full ReAct
agent ran behind it. `build_subagent` selects the implementation from config at the
composition root; `analyze_band` is a back-compat shim for legacy callers/tests.
"""
from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

from mcg_swarm.schemas import SegmentReport
from mcg_swarm.subagent.task import BandTask
from mcg_swarm.subagent.static import HeaderVerification, StaticSubagent

_log = logging.getLogger(__name__)
_react_warned = False


@runtime_checkable
class Subagent(Protocol):
    def analyze(self, task: BandTask) -> SegmentReport: ...


def _warn_once(msg: str, *args) -> None:
    global _react_warned
    if not _react_warned:
        _log.warning(msg, *args)
        _react_warned = True


def build_subagent(llm=None) -> "Subagent":
    """Construct the configured subagent (`MCG_SUBAGENT`, default `static`).

    `react` enables the escalating ReAct verifier — but only when `ANTHROPIC_API_KEY` is
    set and the Claude Agent SDK is importable. Otherwise it logs once and falls back to
    `StaticSubagent`, so the default path never needs the SDK installed.
    """
    mode = os.environ.get("MCG_SUBAGENT", "static").strip().lower()
    if mode != "react":
        return StaticSubagent(llm)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        _warn_once("MCG_SUBAGENT=react but ANTHROPIC_API_KEY is unset; using static.")
        return StaticSubagent(llm)

    try:
        from mcg_swarm.subagent.escalating import EscalatingSubagent
        from mcg_swarm.subagent.sdk_runner import ClaudeSDKAgentRunner
        from mcg_swarm.subagent.verifier import ReActVerifier
        runner = ClaudeSDKAgentRunner()
        return EscalatingSubagent(StaticSubagent(llm), ReActVerifier(runner))
    except Exception as e:  # SDK missing / construction failure → degrade to static
        _warn_once("MCG_SUBAGENT=react unavailable (%s); using static.", e)
        return StaticSubagent(llm)


def analyze_band(path, band, header, llm=None) -> SegmentReport:
    """Back-compat shim: build a minimal BandTask (no handle signals) and run static analysis."""
    task = BandTask(path=path, band=band, header=list(header))
    return StaticSubagent(llm).analyze(task)


__all__ = [
    "Subagent", "BandTask", "StaticSubagent", "HeaderVerification",
    "build_subagent", "analyze_band",
]
