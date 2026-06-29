"""agent_runtime — application-side AgentRunner implementations (provider adapters).

Lives OUTSIDE mcg_swarm: the swarm depends only on the AgentRunner protocol and never
on a provider. These runners are built by the app and injected into run_swarm.
"""
from __future__ import annotations

from agent_runtime.claude_sdk_runner import ClaudeSDKAgentRunner, build_allowed_tools

__all__ = ["ClaudeSDKAgentRunner", "build_allowed_tools"]
