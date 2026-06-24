"""ClaudeSDKAgentRunner — the ONLY module that imports the agent framework.

Adapts our framework-agnostic `Tool` objects into Claude Agent SDK tools, runs the agent
loop with an allow-listed toolset and a turn budget, and collects the final structured
result via a `finalize` tool whose input schema IS the verifier's patch schema (so the
result is validated tool input, not brittle free-text JSON).

The `claude_agent_sdk` import is lazy: constructing `ClaudeSDKAgentRunner` raises
ImportError when the SDK is absent, which `build_subagent` catches to fall back to static.
Any runtime failure is caught by `ReActVerifier`, which then keeps the static result.

NOTE: SDK signatures evolve — verify against current docs
(https://platform.claude.com/llms.txt) before relying on the live path. This module is
deliberately isolated so such churn never touches the tools, the digest, or the swarm.
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


class ClaudeSDKAgentRunner:
    """AgentRunner backed by the Claude Agent SDK."""

    def __init__(self, model: str = "claude-haiku-4-5-20251001", max_turns: int = 8) -> None:
        import claude_agent_sdk as _sdk  # noqa: F401  (lazy: ImportError → static fallback)
        self._model = model
        self._max_turns = max_turns

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
        allowed = [f"mcp__band__{t.name}" for t in tools] + ["mcp__band__finalize"]
        options = ClaudeAgentOptions(
            system_prompt=_SYSTEM,
            mcp_servers={"band": server},
            allowed_tools=allowed,
            max_turns=self._max_turns,
            model=self._model,
        )

        async for _msg in query(prompt=seed, options=options):
            pass  # we only need the side effect: the agent's finalize tool call

        if not captured:
            raise RuntimeError("agent did not call finalize")
        if schema is not None:
            return schema.model_validate(captured).model_dump(exclude_none=True)
        return dict(captured)
