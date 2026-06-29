#!/usr/bin/env python
"""
MCG Swarm — debugger walkthrough. Each scenario just hands a file path to the
swarm; the swarm splits the tables and fans out subagents on its own.

    python demo_walkthrough.py multi   # workbook that fans out to multiple band subagents
    python demo_walkthrough.py fix     # LIVE ReAct repair of a real gate error (no stub, no mock)
    python demo_walkthrough.py both    # (default) both

Data sources live in demo_data/. Set a breakpoint on the run_swarm() call and
step in. The 'fix' scenario REQUIRES a real ReAct agent (logged-in `claude` CLI
or ANTHROPIC_API_KEY) and refuses to run on the static stub — it makes real
claude_agent_sdk calls (~50-70s). 'multi' is static and fast.
"""
from __future__ import annotations
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mcg_swarm.runner import run_swarm
from mcg_swarm.config import SwarmConfig
from mcg_swarm.subagent import build_subagent, build_table_validator

DEMO_WB = os.path.join(ROOT, "demo_data")
MULTI_WB = os.path.join(DEMO_WB, "multi_subagent.xlsx")   # 12k rows -> 3 row-bands
FIX_WB = os.path.join(DEMO_WB, "react_fix_sampling.xlsx")  # number col that turns to text
REPAIR_LOG = os.path.join(DEMO_WB, "repair_log.jsonl")


def _build_real_runner():
    """Construct the live Claude-SDK ReAct runner with host investigation caps.

    Raises ImportError/auth errors if the SDK/login is unavailable — the 'fix' scenario
    then refuses to run rather than silently using the static stub.
    permission_mode lets the agent run its host tools unattended; verify the exact value
    against current SDK docs.
    """
    from agent_runtime.claude_sdk_runner import ClaudeSDKAgentRunner
    return ClaudeSDKAgentRunner(
        host_tools=("Bash", "Read", "Grep"), permission_mode="bypassPermissions")


def _require_real_react(runner) -> None:
    """Fail loud unless the injected runner produces the genuine ReAct stack."""
    sa = build_subagent(runner=runner)
    tv = build_table_validator(runner=runner)
    band, table = type(sa).__name__, (type(tv).__name__ if tv else None)
    if band != "EscalatingSubagent" or tv is None:
        raise RuntimeError(
            "Real ReAct agent NOT active (got band=%s table_validator=%s). "
            "Need the Claude Agent SDK + a logged-in `claude` CLI or ANTHROPIC_API_KEY. "
            "Refusing to run on the static stub." % (band, table))
    print(f"[real ReAct active] band={band} table_validator={table}")


def scenario_multi():
    """Big single table -> the swarm splits it into bands -> multiple subagents (static)."""
    ext = run_swarm({"main": MULTI_WB})  # no runner -> static-only
    print(ext)
    return ext


def scenario_fix():
    """LIVE ReAct repair: a column declared number that turns to text mid-way.

    The static pass flags a real `dtype-mismatch`; the injected live ReAct runner then
    repairs it (and may use bash/read/grep to investigate before proposing the patch).
    """
    os.environ["MCG_REPAIR_LOG"] = REPAIR_LOG  # per-pass logging path (unchanged)
    if os.path.exists(REPAIR_LOG):
        os.remove(REPAIR_LOG)
    runner = _build_real_runner()
    _require_real_react(runner)

    ext = run_swarm({"main": FIX_WB}, runner=runner, config=SwarmConfig(validate=True))
    print(ext)

    # Surface what the real agent actually did, pass by pass (no mock anywhere).
    if os.path.exists(REPAIR_LOG):
        print("\n--- repair log (per-pass, real agent) ---")
        print(open(REPAIR_LOG).read().strip() or "(no table-level passes logged)")
    return ext


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "both"
    if name in ("multi", "both"):
        scenario_multi()
    if name in ("fix", "both"):
        scenario_fix()


if __name__ == "__main__":
    main()
