"""Subagent package: analyze one band into a SegmentReport.

The swarm depends only on the `Subagent` port (`analyze(task) -> SegmentReport`) and is
unaware whether deterministic static analysis, a one-shot LLM verify, or a full ReAct
agent ran behind it. `build_subagent` selects the implementation from config at the
composition root; `analyze_band` is a back-compat shim for legacy callers/tests.
"""
from __future__ import annotations

import logging
import os
import shutil
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


def _validate_enabled() -> bool:
    """Whether the agent also double-checks clean tables (MCG_REACT_VALIDATE, default on)."""
    return os.environ.get("MCG_REACT_VALIDATE", "on").strip().lower() != "off"


def _agent_auth_available() -> bool:
    """Whether the Claude Agent SDK has a usable auth path.

    The SDK authenticates either via ``ANTHROPIC_API_KEY`` or — when running under a
    logged-in Claude CLI (subscription auth) — via the ``claude`` binary it spawns. We
    accept either, so the react path works for CLI-only setups (verified live). If
    neither is present we stay on static rather than pay a per-band timeout that only
    fails back to static anyway.
    """
    return bool(os.environ.get("ANTHROPIC_API_KEY")) or shutil.which("claude") is not None


def build_subagent(llm=None) -> "Subagent":
    """Construct the configured subagent (`MCG_SUBAGENT`, default `static`).

    `react` enables the escalating ReAct verifier — but only when `ANTHROPIC_API_KEY` is
    set and the Claude Agent SDK is importable. Otherwise it logs once and falls back to
    `StaticSubagent`, so the default path never needs the SDK installed.
    """
    mode = os.environ.get("MCG_SUBAGENT", "static").strip().lower()
    if mode != "react":
        return StaticSubagent(llm)

    if not _agent_auth_available():
        _warn_once("MCG_SUBAGENT=react but no agent auth (ANTHROPIC_API_KEY or claude "
                   "CLI); using static.")
        return StaticSubagent(llm)

    try:
        from mcg_swarm.subagent.escalating import EscalatingSubagent, EscalationPolicy
        from mcg_swarm.subagent.sdk_runner import ClaudeSDKAgentRunner
        from mcg_swarm.subagent.verifier import ReActVerifier
        # The failure fallback is always active; MCG_REACT_VALIDATE=off disables the
        # extra "double-check clean tables" validation (default on).
        runner = ClaudeSDKAgentRunner()
        return EscalatingSubagent(
            StaticSubagent(llm), ReActVerifier(runner),
            EscalationPolicy(validate=_validate_enabled()))
    except Exception as e:  # SDK missing / construction failure → degrade to static
        _warn_once("MCG_SUBAGENT=react unavailable (%s); using static.", e)
        return StaticSubagent(llm)


def build_table_validator(llm=None):
    """Construct the table-level validator, or None when the agent is disabled/unavailable.

    Active only when `MCG_SUBAGENT=react` with `ANTHROPIC_API_KEY` + the SDK present. The
    failure fallback is always on; `MCG_REACT_VALIDATE=off` disables checking clean tables.
    """
    mode = os.environ.get("MCG_SUBAGENT", "static").strip().lower()
    if mode != "react" or not _agent_auth_available():
        return None
    try:
        from mcg_swarm.subagent.sdk_runner import ClaudeSDKAgentRunner
        from mcg_swarm.subagent.table_check import TableCheckPolicy, TableValidator
        return TableValidator(
            ClaudeSDKAgentRunner(), TableCheckPolicy(validate=_validate_enabled()))
    except Exception as e:
        _warn_once("MCG_SUBAGENT=react table validator unavailable (%s); skipping.", e)
        return None


def analyze_band(path, band, header, llm=None) -> SegmentReport:
    """Back-compat shim: build a minimal BandTask (no handle signals) and run static analysis."""
    task = BandTask(path=path, band=band, header=list(header))
    return StaticSubagent(llm).analyze(task)


__all__ = [
    "Subagent", "BandTask", "StaticSubagent", "HeaderVerification",
    "build_subagent", "build_table_validator", "analyze_band",
]
