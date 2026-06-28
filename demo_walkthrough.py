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
from mcg_swarm.subagent import build_subagent, build_table_validator

DEMO_WB = os.path.join(ROOT, "demo_data")
MULTI_WB = os.path.join(DEMO_WB, "multi_subagent.xlsx")   # 12k rows -> 3 row-bands
FIX_WB = os.path.join(DEMO_WB, "react_fix_sampling.xlsx")  # number col that turns to text
REPAIR_LOG = os.path.join(DEMO_WB, "repair_log.jsonl")


def _require_real_react() -> None:
    """Fail loud unless the genuine ReAct agent is wired — never silently use the stub.

    build_subagent/build_table_validator read env at call time, exactly as run_swarm
    does, so this asserts what run_swarm is about to construct."""
    sa = build_subagent()
    tv = build_table_validator()
    band, table = type(sa).__name__, (type(tv).__name__ if tv else None)
    if band != "EscalatingSubagent" or tv is None:
        raise RuntimeError(
            "Real ReAct agent NOT active (got band=%s table_validator=%s). "
            "Need MCG_SUBAGENT=react AND a logged-in `claude` CLI or ANTHROPIC_API_KEY. "
            "Refusing to run on the static stub." % (band, table))
    print(f"[real ReAct active] band={band} table_validator={table}")


def scenario_multi():
    """Big single table -> the swarm splits it into bands -> multiple subagents."""
    os.environ["MCG_SUBAGENT"] = "static"
    ext = run_swarm({"main": MULTI_WB})
    print(ext)
    return ext


def scenario_fix():
    """LIVE ReAct repair: a column declared number that turns to text mid-way.

    With the dtype-conformance gate, the static pass now flags this as a real
    `dtype-mismatch` error (no longer silent); the live ReAct loop then repairs it.
    """
    os.environ["MCG_SUBAGENT"] = "react"
    os.environ["MCG_REACT_VALIDATE"] = "on"
    os.environ["MCG_REPAIR_LOG"] = REPAIR_LOG
    if os.path.exists(REPAIR_LOG):
        os.remove(REPAIR_LOG)
    _require_real_react()

    ext = run_swarm({"main": FIX_WB})
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
