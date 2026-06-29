"""ClaudeSDKAgentRunner — an application-side AgentRunner backed by the Claude Agent SDK.

Adapts the swarm's framework-agnostic `Tool` objects into SDK tools, runs the agent loop
with an allow-listed toolset and a turn budget, and collects the final structured result
via a `finalize` tool whose input schema IS the verifier's patch schema.

Host investigation capabilities (e.g. 'Bash', 'Read', 'Grep') are configured here, on the
runner, via `host_tools` + `permission_mode`. They are merged into the allow-list so the
agent can investigate when things go wrong — but the table mutation still exits only via
`finalize` → schema validation → verify-before-accept (the swarm's gate is untouched).

The `claude_agent_sdk` import is lazy: constructing `ClaudeSDKAgentRunner` raises
ImportError when the SDK is absent; the application decides degradation (inject None).

NOTE: SDK signatures evolve — verify `permission_mode` values and option names against
current docs (https://platform.claude.com/llms.txt) before relying on the live path.
"""
from __future__ import annotations

import asyncio
import json

from mcg_swarm.subagent.tools import Tool

_SYSTEM = (
    "You verify the column metadata of ONE spreadsheet table band that a fast "
    "deterministic pass already produced. Use the read-only tools to inspect the actual "
    "cells (peek the header candidates, the data, and especially the tail for totals "
    "rows), then call `finalize` exactly once with corrections to column dtype/unit/role "
    "and any anomalies you found. Only include columns you are changing. Never invent "
    "cell values."
)


def build_allowed_tools(tools, host_tools=()):
    """Final SDK allow-list: swarm domain tools + finalize + injected host built-ins."""
    return (
        [f"mcp__band__{t.name}" for t in tools]
        + ["mcp__band__finalize"]
        + list(host_tools)
    )


class ClaudeSDKAgentRunner:
    """AgentRunner backed by the Claude Agent SDK."""

    def __init__(self, model: str = "claude-haiku-4-5-20251001", max_turns: int = 8,
                 host_tools=(), permission_mode: str | None = None) -> None:
        import claude_agent_sdk as _sdk  # noqa: F401  (lazy: ImportError → app injects None)
        self._model = model
        self._max_turns = max_turns
        self._host_tools = tuple(host_tools)
        self._permission_mode = permission_mode

    def run(self, seed: str, tools: list[Tool], *, schema) -> dict:
        return asyncio.run(self._run_async(seed, tools, schema))

    async def _run_async(self, seed: str, tools: list[Tool], schema) -> dict:
        from claude_agent_sdk import (
            ClaudeAgentOptions, create_sdk_mcp_server, query, tool,
        )

        captured: dict = {}

        def _adapt(t: Tool):
            @tool(t.name, t.description, t.input_schema)
            async def _handler(args):
                result = t.handler(args or {})
                return {"content": [{"type": "text", "text": json.dumps(result, default=str)}]}
            return _handler

        finalize_schema = schema.model_json_schema() if schema is not None else {"type": "object"}

        @tool("finalize", "Submit final column corrections and anomalies.", finalize_schema)
        async def _finalize(args):
            captured.update(args or {})
            return {"content": [{"type": "text", "text": "ok"}]}

        sdk_tools = [_adapt(t) for t in tools] + [_finalize]
        server = create_sdk_mcp_server(name="band", version="1.0.0", tools=sdk_tools)
        allowed = build_allowed_tools(tools, self._host_tools)
        opt_kwargs = dict(
            system_prompt=_SYSTEM,
            mcp_servers={"band": server},
            allowed_tools=allowed,
            max_turns=self._max_turns,
            model=self._model,
        )
        if self._permission_mode is not None:
            opt_kwargs["permission_mode"] = self._permission_mode
        options = ClaudeAgentOptions(**opt_kwargs)

        async for _msg in query(prompt=seed, options=options):
            pass  # we only need the side effect: the agent's finalize tool call

        if not captured:
            raise RuntimeError("agent did not call finalize")
        if schema is not None:
            return schema.model_validate(captured).model_dump(exclude_none=True)
        return dict(captured)
