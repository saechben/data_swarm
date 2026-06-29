"""Allow-list assembly for the relocated SDK runner (pure; no SDK/network needed)."""
from agent_runtime.claude_sdk_runner import build_allowed_tools
from mcg_swarm.subagent.tools import Tool


def _tool(name):
    return Tool(name=name, description="d", input_schema={"type": "object"}, handler=lambda a: {})


def test_allowed_tools_domain_only():
    assert build_allowed_tools([_tool("geometry"), _tool("peek_rows")]) == [
        "mcp__band__geometry", "mcp__band__peek_rows", "mcp__band__finalize",
    ]


def test_allowed_tools_merges_host_caps():
    # Gated investigation: host built-ins are appended to the domain allow-list.
    assert build_allowed_tools([_tool("geometry")], host_tools=("Bash", "Read", "Grep")) == [
        "mcp__band__geometry", "mcp__band__finalize", "Bash", "Read", "Grep",
    ]
