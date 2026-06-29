"""The AgentRunner port — decouples the ReAct strategy from the agent framework.

A runner receives the seed prompt and the framework-agnostic tool list, drives an
agent loop, and returns the final structured patch (validated against `schema`). The
real implementation lives in `agent_runtime.claude_sdk_runner`; `FakeAgentRunner` here
testable fully offline by replaying a scripted sequence of tool calls.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from mcg_swarm.subagent.tools import Tool


@runtime_checkable
class AgentRunner(Protocol):
    def run(self, seed: str, tools: list[Tool], *, schema) -> dict: ...


class FakeAgentRunner:
    """Deterministic runner for tests: execute scripted tool calls, return a canned patch.

    actions: ordered ``[{"tool": name, "args": {...}}, ...]`` executed against the real
             toolset (so probes exercise the actual BandView).
    final:   the patch dict returned when ``finals`` is None; validated against ``schema``
             to mirror the boundary.
    finals:  optional sequence of patch dicts returned in order across successive ``run``
             calls; the last element is clamped (repeated) once exhausted.
    calls:   counter incremented on every ``run`` invocation (starts at 0).
    Observations from each tool call are recorded on ``self.observations``.
    """

    def __init__(self, actions: list[dict], final: dict | None = None,
                 finals: list | None = None) -> None:
        self.actions = actions
        self.final = final
        self.finals = finals
        self.observations: list = []
        self.calls: int = 0

    def run(self, seed: str, tools: list[Tool], *, schema) -> dict:
        self.calls += 1
        by_name = {t.name: t for t in tools}
        for act in self.actions:
            tool = by_name[act["tool"]]
            self.observations.append(tool.handler(act.get("args", {})))
        if self.finals is not None:
            i = min(self.calls - 1, len(self.finals) - 1)
            raw = self.finals[i]
        else:
            raw = self.final or {}
        if schema is not None:
            return schema.model_validate(raw).model_dump(exclude_none=True)
        return dict(raw)
