#!/usr/bin/env python
"""
MCG Swarm — debugger walkthrough. Each scenario just hands a file path to the
swarm; the swarm splits the tables and fans out subagents on its own.

    python demo_walkthrough.py multi   # workbook that fans out to multiple band subagents
    python demo_walkthrough.py fix     # workbook where the LIVE ReAct agent fixes a static error
    python demo_walkthrough.py both    # (default) both

Data sources live in demo_data/. Set a breakpoint on the run_swarm() call and
step in. The 'fix' scenario makes real claude_agent_sdk calls via the logged-in
`claude` CLI (~50-70s); 'multi' is static and fast.
"""
from __future__ import annotations
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mcg_swarm.runner import run_swarm

DEMO_WB = os.path.join(ROOT, "demo_data")
MULTI_WB = os.path.join(DEMO_WB, "multi_subagent.xlsx")   # 12k rows -> 3 row-bands
FIX_WB = os.path.join(DEMO_WB, "react_fix_sampling.xlsx")  # static mislabels ResolvedDays


def scenario_multi():
    """Big single table -> the swarm splits it into bands -> multiple subagents."""
    os.environ["MCG_SUBAGENT"] = "static"
    ext = run_swarm({"main": MULTI_WB})
    print(ext)
    return ext


def scenario_fix():
    """Live ReAct agent corrects a dtype the static 20-row sample got wrong."""
    os.environ["MCG_SUBAGENT"] = "react"
    os.environ["MCG_REACT_VALIDATE"] = "on"
    ext = run_swarm({"main": FIX_WB})
    print(ext)
    return ext


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "both"
    if name in ("multi", "both"):
        scenario_multi()
    if name in ("fix", "both"):
        scenario_fix()


if __name__ == "__main__":
    main()
