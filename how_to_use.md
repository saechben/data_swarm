# How to Run the Swarm — Handoff Guide

This document describes **exactly what must be provided** to run the MCG swarm
extraction pipeline against real data, and what each input unlocks. It is written
for a coding agent taking over the "run it with a real agent runtime" task.

The whole system is entered through **one function**:

```python
from mcg_swarm.runner import run_swarm
from mcg_swarm.config import SwarmConfig

extraction = run_swarm(workbooks, *, llm=None, runner=None, config=SwarmConfig())
```

`run_swarm` returns a `WorkbookExtraction` (see [Output](#output)). It **never raises**
on bad tabs — a broken sheet lands its errors on its own table/findings, the file still
returns. The only hard failure is an unreadable workbook (returned as an `errors` entry).

---

## The three inputs

### 1. `workbooks` — the data source (REQUIRED)

Accepts any of:

| Form | Example |
|------|---------|
| Path string | `run_swarm("/path/to/book.xlsx")` |
| Single-source dict | `run_swarm({"main": "/path/to/book.xlsx"})` |
| A `WorkbookSource` | `run_swarm(my_source)` |

Internally normalised by `mcg_swarm.source.as_source`. `.xlsx` on disk is the
common case. That is the only mandatory input — with just this you get the
**deterministic-only** path (no LLM, no agent).

### 2. `llm` — an `LLMClient` (OPTIONAL)

A structured-output client used for the messy-tab header fallback and as the
baseline `StaticSubagent`. Interface (port in `mcg_swarm/llm/client.py`):

```python
class LLMClient(Protocol):
    def complete(self, system: str, user: str, *, schema=None) -> dict: ...
```

Real implementation:

```python
from mcg_swarm.llm.client import AnthropicClient
llm = AnthropicClient(model="claude-opus-4-8")   # or claude-haiku-4-5-20251001
```

- **Requires `ANTHROPIC_API_KEY`** in the environment (or pass `api_key=`). Uses the
  Anthropic Messages API.
- `llm=None` → fully deterministic header handling. Optional; it does **not** enable
  the ReAct agent or boundary repair — that is the `runner` below.

### 3. `runner` — an `AgentRunner` (OPTIONAL, this is the important one)

The injected agent runtime. **Providing a runner is what activates the agentic
capabilities**, including the Phase-2 boundary repair. Port
(`mcg_swarm/subagent/agent_runner.py`):

```python
class AgentRunner(Protocol):
    def run(self, seed: str, tools: list[Tool], *, schema, system: str | None = None) -> dict: ...
```

Real implementation — Claude Agent SDK adapter (`agent_runtime/claude_sdk_runner.py`):

```python
from agent_runtime import ClaudeSDKAgentRunner

runner = ClaudeSDKAgentRunner(
    model="claude-haiku-4-5-20251001",  # per-agent model
    max_turns=8,                         # agent loop budget
    host_tools=(),                       # e.g. ("Read","Grep","Bash") to let it investigate
    permission_mode=None,                # SDK permission mode if host_tools are granted
)
```

**Requirements to construct/use the SDK runner:**
- The `claude-agent-sdk` package must be installed (`pip install claude-agent-sdk`;
  currently present: v0.2.110). Import is lazy — constructing `ClaudeSDKAgentRunner`
  raises `ImportError` if the SDK is absent, and the app is expected to degrade by
  injecting `runner=None`.
- **Auth is via the Claude Code CLI session, not `ANTHROPIC_API_KEY`.** The SDK uses
  the local CLI auth. Verify the CLI is logged in before a live run.
- Each `run()` call drives a live agent loop, so a runner makes extraction **slower and
  token-costly** (one agent invocation per table it inspects/repairs).

You may also supply **your own** `AgentRunner` (any object with the `run` signature
above) to back a different provider — the swarm depends only on the port, never on a
provider.

#### What the runner unlocks (all gated on `runner is not None`)
Built by the factories in `mcg_swarm/subagent/__init__.py`:
- **`build_subagent`** → band-level `EscalatingSubagent` (static first, ReAct verifier on
  trouble; and on clean bands too when `config.validate`). Fixes column dtype/unit/role.
- **`build_table_validator`** → table-level `TableValidator` (whole-table ReAct recovery:
  header re-detection, etc.). `None` without a runner.
- **`build_structural_reviewer`** → **Phase-2 Layer 2** sheet re-cut / dropped-table
  repair. `None` when `runner is None` **or** `config.alter_boundaries` is `False`.

Without a runner, all three are inert and the pipeline is **detection-only**: it still
*flags* problems (e.g. `uncovered-data` for a dropped table) but does not repair them.

---

## `config` — behavior knobs

`SwarmConfig` is a frozen dataclass (`mcg_swarm/config.py`):

| Field | Default | Meaning |
|-------|---------|---------|
| `validate` | `True` | Run the agent on otherwise-clean tables too (not just on trouble). |
| `repair_max_passes` | `3` | Max table-level repair passes. |
| `alter_boundaries` | `True` | Enable Phase-2 boundary re-cut. **Only takes effect if a runner is also injected.** |

`SwarmConfig` deliberately knows nothing about providers/keys/models — those live on the
`llm`/`runner` you inject.

---

## Minimal runnable recipes

```python
from mcg_swarm.runner import run_swarm
from mcg_swarm.config import SwarmConfig

WB = {"main": "/path/to/book.xlsx"}

# A) Deterministic only — no LLM, no agent (fast; detection but no repair)
ext = run_swarm(WB)

# B) With LLM baseline (messy-tab fallback) but still no agentic repair
from mcg_swarm.llm.client import AnthropicClient          # needs ANTHROPIC_API_KEY
ext = run_swarm(WB, llm=AnthropicClient(model="claude-haiku-4-5-20251001"))

# C) FULL agentic — band verifier + table validator + Phase-2 boundary repair
from agent_runtime import ClaudeSDKAgentRunner            # needs Claude Code CLI auth + SDK
runner = ClaudeSDKAgentRunner(model="claude-haiku-4-5-20251001", max_turns=8)
ext = run_swarm(
    WB,
    llm=AnthropicClient(model="claude-haiku-4-5-20251001"),   # optional but recommended
    runner=runner,
    config=SwarmConfig(alter_boundaries=True),                # default; explicit for clarity
)
```

Recipe **C** is the one that exercises Phase 2. To repair dropped tables you need
`runner` set AND `alter_boundaries=True` (the default).

---

## Output

`run_swarm` returns a `WorkbookExtraction` (`mcg_swarm/schemas.py`):

```python
ext.workbook            # str
ext.sheets              # list[str]
ext.tables              # list[CanonicalTable]
ext.findings            # list[Finding]   (workbook/sheet-scope)
ext.errors              # list[str]        (derived from error-severity findings)
```

Each `CanonicalTable` carries `table_id` (`{sheet}__{i}`, or `{sheet}__{i}_{j}` for a
re-cut sheet), `region`, `header_row`, `columns`, `formulas`, `findings`, and derived
`errors`.

**Reading `Finding`s** (`category`, `severity`, `scope`, `message`, `resolution`):
- `resolution` ∈ `{open, fixed, rejected}`.
- `uncovered-data` (severity `error`, scope `sheet`) = a dropped/second table was
  detected. With no runner it stays `open`. With a runner + `alter_boundaries`, an
  accepted re-cut flips it to `fixed`; a re-cut that the verify-before-accept gate
  rejects flips it to `rejected` (baseline kept — never silent corruption).

To rebuild extraction indices for downstream use:
`build_indices(path, ext)` (also in `mcg_swarm/runner.py`).

---

## Running through the eval harness

```bash
.venv/bin/python eval/run_benchmark.py --adapter swarm            # all 18 workbooks
.venv/bin/python eval/run_benchmark.py --adapter swarm --workbooks store_ops.xlsx
.venv/bin/python eval/run_benchmark.py --build --adapter oracle   # (re)generate data + reference
```

Outputs land in `eval/results/` (`scorecard_swarm.json`, `report_swarm.html`).
`eval/results/` is git-ignored.

### ⚠️ The eval adapter currently injects NO runner
`eval/adapters/swarm_adapter.py::prepare` calls:

```python
llm = AnthropicClient(...) if os.environ.get("ANTHROPIC_API_KEY") else None
ext = run_swarm({"main": workbook_path}, llm=llm)   # NOTE: no runner= , no config=
```

So today the benchmark runs the **detection-only** path — Phase-2 boundary repair and the
ReAct verifier never fire, and results reflect the deterministic baseline. To measure the
agentic system (the point of the handoff), edit `prepare` to build and inject a runner,
e.g.:

```python
from agent_runtime import ClaudeSDKAgentRunner
runner = ClaudeSDKAgentRunner(model="claude-haiku-4-5-20251001", max_turns=8)
ext = run_swarm({"main": workbook_path}, llm=llm, runner=runner,
                config=SwarmConfig(alter_boundaries=True))
```

Recommend gating this behind a flag/env (e.g. only build the runner when a
`SWARM_RUNNER=1` env var is set) so `--adapter swarm` can still run deterministically,
and starting on the affected subset to bound cost:
`store_ops.xlsx segment_report.xlsx multi_region_sales.xlsx` (these have `uncovered-data`
detections that Phase 2 should repair) plus `quarterly_pnl.xlsx` and `cashflow_signs.xlsx`
for contrast.

---

## Gotchas / checklist before a live run

- [ ] `runner` injected (not `None`) — otherwise Phase 2 and the ReAct verifier are inert.
- [ ] `config.alter_boundaries` is `True` (default) — required in addition to the runner.
- [ ] `claude-agent-sdk` installed and the **Claude Code CLI is authenticated** (SDK runner
      uses CLI auth, *not* `ANTHROPIC_API_KEY`).
- [ ] `ANTHROPIC_API_KEY` set **only if** you also want the `AnthropicClient` `llm` (messy-tab
      fallback). The runner does not need it; the `llm` does.
- [ ] Expect higher latency/cost: the runner makes one live agent call per table it works.
- [ ] Results are trustworthy by construction: every repair goes through
      verify-before-accept + live re-validation, so a hallucinated re-cut is a no-op
      (`rejected`), never corruption.
```
