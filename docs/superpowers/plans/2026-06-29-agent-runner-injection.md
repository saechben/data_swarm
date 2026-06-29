# Agent Runner Injection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evict provider/transport wiring (Claude SDK → LiteLLM → Bedrock) from `mcg_swarm`; the application builds the ReAct `AgentRunner` and injects it into `run_swarm`.

**Architecture:** Pure dependency injection. `run_swarm` gains `runner: AgentRunner | None` and `config: SwarmConfig`. The factories stop reading env / probing auth / constructing runners — they receive an injected runner (`None` → static-only). The concrete SDK runner relocates out of the swarm into a sibling `agent_runtime/` package and gains host-capability merging (gated investigation: bash/read/grep on the runner, table mutation still exits only via `finalize` → verify-before-accept).

**Tech Stack:** Python 3, pytest, dataclasses, Claude Agent SDK (lazy import, app-side only).

## Global Constraints

- Test runner: `pytest`. Baseline must stay green: **247 passed, 2 skipped**. Run from repo root `/Users/benjaminsaechew/Documents/Claude/Projects/data_swarm`.
- Work on branch `feat/agent-runner-injection` (already created; spec committed there).
- **No provider/credential/model knowledge in `mcg_swarm`.** No env reads in `mcg_swarm` for agent wiring (`MCG_SUBAGENT`, `MCG_REACT_VALIDATE`, `MCG_REPAIR_MAX_PASSES` removed from the package).
- **`SwarmConfig` holds values only** — never a runner, provider, model, or credentials.
- **Never-raise guarantee preserved:** do not change `ReActVerifier` / `TableValidator` fallback behavior. The agent's only table-mutation path stays `finalize` → schema validation → verify-before-accept.
- Claude Agent SDK import stays **lazy** (inside the runner). Constructing the runner may raise `ImportError`; the *app* decides degradation by passing `None`.
- Keep existing style: `from __future__ import annotations`, terse docstrings.

---

### Task 1: `SwarmConfig` value object

**Files:**
- Create: `mcg_swarm/config.py`
- Test: `tests/test_swarm_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `SwarmConfig(validate: bool = True, repair_max_passes: int = 3)` — frozen dataclass, importable as `from mcg_swarm.config import SwarmConfig`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_swarm_config.py`:

```python
"""SwarmConfig: a frozen value object for swarm behavior knobs (no provider knowledge)."""
import pytest
from dataclasses import FrozenInstanceError

from mcg_swarm.config import SwarmConfig


def test_defaults():
    c = SwarmConfig()
    assert c.validate is True
    assert c.repair_max_passes == 3


def test_custom_values():
    c = SwarmConfig(validate=False, repair_max_passes=5)
    assert c.validate is False
    assert c.repair_max_passes == 5


def test_is_frozen():
    c = SwarmConfig()
    with pytest.raises(FrozenInstanceError):
        c.validate = False  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_swarm_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcg_swarm.config'`

- [ ] **Step 3: Write minimal implementation**

Create `mcg_swarm/config.py`:

```python
"""SwarmConfig — value object for swarm behavior knobs.

Holds plain data the swarm acts on. It deliberately knows nothing about providers,
models, credentials, or runners: those are injected collaborators, not configuration.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SwarmConfig:
    """Behavior knobs for a swarm run.

    validate:          also run the agent on otherwise-clean tables (was MCG_REACT_VALIDATE,
                       default on).
    repair_max_passes: max table-level repair passes (was MCG_REPAIR_MAX_PASSES, default 3).
    """

    validate: bool = True
    repair_max_passes: int = 3
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_swarm_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add mcg_swarm/config.py tests/test_swarm_config.py
git commit -m "feat(config): SwarmConfig value object for swarm behavior knobs"
```

---

### Task 2: Convert factories + `run_swarm` to dependency injection (delete env/auth)

**Files:**
- Modify: `mcg_swarm/subagent/__init__.py` (rewrite the factory section, lines ~9-122)
- Modify: `mcg_swarm/runner.py:33-42` (and import line 6)
- Modify (rewrite): `tests/test_subagent_build.py`
- Delete: `tests/test_validator_activation.py` (its cases move to Task 1 + this task's tests)

**Interfaces:**
- Consumes: `SwarmConfig` (Task 1); `FakeAgentRunner` from `mcg_swarm.subagent.agent_runner`; `StaticSubagent`, `EscalatingSubagent`/`EscalationPolicy`, `ReActVerifier`, `TableValidator`/`TableCheckPolicy` (existing).
- Produces:
  - `build_subagent(llm=None, runner: AgentRunner | None = None, config: SwarmConfig = SwarmConfig()) -> Subagent`
  - `build_table_validator(runner: AgentRunner | None = None, config: SwarmConfig = SwarmConfig())` → `TableValidator | None`
  - `run_swarm(workbooks, *, llm=None, runner: AgentRunner | None = None, config: SwarmConfig = SwarmConfig()) -> WorkbookExtraction`

- [ ] **Step 1: Write the failing tests**

Replace the entire contents of `tests/test_subagent_build.py`:

```python
"""build_subagent / build_table_validator wiring: runner injected, no env, no SDK."""
from mcg_swarm.subagent import build_subagent, build_table_validator, StaticSubagent
from mcg_swarm.subagent.escalating import EscalatingSubagent
from mcg_swarm.subagent.table_check import TableValidator
from mcg_swarm.subagent.agent_runner import FakeAgentRunner
from mcg_swarm.config import SwarmConfig


def _fake_runner():
    return FakeAgentRunner(actions=[], final={})


def test_no_runner_is_static():
    assert isinstance(build_subagent(runner=None), StaticSubagent)


def test_runner_gives_escalating():
    sub = build_subagent(runner=_fake_runner())
    assert isinstance(sub, EscalatingSubagent)


def test_validator_none_without_runner():
    assert build_table_validator(runner=None) is None


def test_validator_present_with_runner():
    assert isinstance(build_table_validator(runner=_fake_runner()), TableValidator)


def test_config_threads_validate_into_escalation():
    sub = build_subagent(runner=_fake_runner(), config=SwarmConfig(validate=False))
    assert sub._policy.validate is False
```

Delete the obsolete activation test (its `_max_passes` cases are covered by `test_swarm_config.py`; its validator-activation case is covered above):

```bash
git rm tests/test_validator_activation.py
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_subagent_build.py -v`
Expected: FAIL — `build_subagent()` does not yet accept `runner=`, and `test_no_runner_is_static` may pass while runner/escalating cases error with `TypeError: unexpected keyword argument 'runner'`.

- [ ] **Step 3: Rewrite the factory module**

Replace the contents of `mcg_swarm/subagent/__init__.py` from the module docstring through `__all__` with:

```python
"""Subagent package: analyze one band into a SegmentReport.

The swarm depends only on the `Subagent` port (`analyze(task) -> SegmentReport`) and is
unaware whether deterministic static analysis or an injected ReAct runner ran behind it.
`build_subagent` / `build_table_validator` take an injected `runner` (an `AgentRunner`)
built by the application; `runner is None` selects the static-only path. The swarm names
no provider and reads no env.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from mcg_swarm.config import SwarmConfig
from mcg_swarm.schemas import SegmentReport
from mcg_swarm.subagent.task import BandTask
from mcg_swarm.subagent.static import HeaderVerification, StaticSubagent


@runtime_checkable
class Subagent(Protocol):
    def analyze(self, task: BandTask) -> SegmentReport: ...


def build_subagent(llm=None, runner=None, config: SwarmConfig = SwarmConfig()) -> "Subagent":
    """Construct the band-level subagent.

    `runner is None` → deterministic `StaticSubagent`. Otherwise the escalating subagent:
    static-first, with the injected ReAct `runner` verifying on trouble (and on clean
    bands too when `config.validate`).
    """
    if runner is None:
        return StaticSubagent(llm)
    from mcg_swarm.subagent.escalating import EscalatingSubagent, EscalationPolicy
    from mcg_swarm.subagent.verifier import ReActVerifier
    return EscalatingSubagent(
        StaticSubagent(llm),
        ReActVerifier(runner),
        EscalationPolicy(validate=config.validate),
    )


def build_table_validator(runner=None, config: SwarmConfig = SwarmConfig()):
    """Construct the table-level validator, or `None` when no runner is injected."""
    if runner is None:
        return None
    from mcg_swarm.subagent.table_check import TableCheckPolicy, TableValidator
    return TableValidator(
        runner,
        TableCheckPolicy(validate=config.validate, max_passes=config.repair_max_passes),
    )


def analyze_band(path, band, header, llm=None) -> SegmentReport:
    """Back-compat shim: build a minimal BandTask (no handle signals) and run static analysis."""
    task = BandTask(path=path, band=band, header=list(header))
    return StaticSubagent(llm).analyze(task)


__all__ = [
    "Subagent", "BandTask", "StaticSubagent", "HeaderVerification",
    "build_subagent", "build_table_validator", "analyze_band",
]
```

This deletes `_warn_once`, `_validate_enabled`, `_max_passes`, `_agent_auth_available`, the `MCG_SUBAGENT` branch, the `os`/`shutil`/`logging` imports, and every `ClaudeSDKAgentRunner` import/construction.

- [ ] **Step 4: Thread runner + config through `run_swarm`**

In `mcg_swarm/runner.py`, change the import on line 6 and the body lines 13-42.

Replace line 6:

```python
from mcg_swarm.subagent import build_subagent, build_table_validator
```

with:

```python
from mcg_swarm.subagent import build_subagent, build_table_validator
from mcg_swarm.config import SwarmConfig
```

Replace the `def run_swarm(...)` signature and the construction block (lines 13 and 33-42). New signature line:

```python
def run_swarm(workbooks, *, llm=None, runner=None, config: SwarmConfig = SwarmConfig()) -> WorkbookExtraction:
```

New construction block (replacing current lines 33-42):

```python
    # The application injects the ReAct runner (built against its provider/transport).
    # runner is None → static-only band subagent and no table validator.
    subagent = build_subagent(llm=llm, runner=runner, config=config)
    table_validator = build_table_validator(runner=runner, config=config)
    tables, sheets = [], []
    for i, h in enumerate(handles):
        sheets.append(h.sheet)
        tables.append(orchestrate_table(
            source, h, table_id=f"{h.sheet}__{i}", llm=llm,
            subagent=subagent, table_validator=table_validator))
```

Note: `llm` becomes keyword-only. The eval adapter already calls `run_swarm({"main": ...}, llm=llm)` (keyword) — no change needed there. Test callers pass no runner → static path, unchanged behavior.

- [ ] **Step 5: Run the new + full suite**

Run: `pytest tests/test_subagent_build.py tests/test_swarm_config.py -v`
Expected: PASS (5 + 3)

Run: `pytest -q`
Expected: **246 passed, 2 skipped** (247 baseline − 1: the deleted `test_validator_activation.py` had 3 tests; `test_subagent_build.py` went 4→5; the removed live-SDK/auth tests are gone. Confirm the only deltas are in these two files and the count is internally consistent; no errors/failures.)

- [ ] **Step 6: Commit**

```bash
git add mcg_swarm/subagent/__init__.py mcg_swarm/runner.py tests/test_subagent_build.py
git rm tests/test_validator_activation.py
git commit -m "refactor(swarm): inject AgentRunner + SwarmConfig; drop env/auth from factories"
```

---

### Task 3: Relocate `ClaudeSDKAgentRunner` to `agent_runtime/` + host-capability merge

**Files:**
- Create: `agent_runtime/__init__.py`
- Move: `mcg_swarm/subagent/sdk_runner.py` → `agent_runtime/claude_sdk_runner.py` (via `git mv`, then edit)
- Modify: `mcg_swarm/subagent/agent_runner.py:5` (docstring pointer)
- Test: `tests/test_claude_sdk_runner.py`

**Interfaces:**
- Consumes: `Tool` from `mcg_swarm.subagent.tools`.
- Produces:
  - `build_allowed_tools(tools, host_tools=()) -> list[str]` (module-level, SDK-free, importable without the SDK installed).
  - `ClaudeSDKAgentRunner(model=..., max_turns=8, host_tools=(), permission_mode=None)` implementing `AgentRunner`.
  - Importable as `from agent_runtime.claude_sdk_runner import ClaudeSDKAgentRunner, build_allowed_tools` and `from agent_runtime import ClaudeSDKAgentRunner`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_claude_sdk_runner.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_claude_sdk_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_runtime'`

- [ ] **Step 3: Relocate the module**

```bash
mkdir -p agent_runtime
git mv mcg_swarm/subagent/sdk_runner.py agent_runtime/claude_sdk_runner.py
```

Create `agent_runtime/__init__.py`:

```python
"""agent_runtime — application-side AgentRunner implementations (provider adapters).

Lives OUTSIDE mcg_swarm: the swarm depends only on the AgentRunner protocol and never
on a provider. These runners are built by the app and injected into run_swarm.
"""
from __future__ import annotations

from agent_runtime.claude_sdk_runner import ClaudeSDKAgentRunner, build_allowed_tools

__all__ = ["ClaudeSDKAgentRunner", "build_allowed_tools"]
```

- [ ] **Step 4: Add the allow-list helper + host-capability config**

Edit `agent_runtime/claude_sdk_runner.py`. Replace the module docstring (lines 1-15) with:

```python
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
```

Add the helper immediately after the `_SYSTEM` string (after current line 30, before `class ClaudeSDKAgentRunner`):

```python
def build_allowed_tools(tools, host_tools=()):
    """Final SDK allow-list: swarm domain tools + finalize + injected host built-ins."""
    return (
        [f"mcp__band__{t.name}" for t in tools]
        + ["mcp__band__finalize"]
        + list(host_tools)
    )
```

Replace `__init__` (current lines 36-39) with:

```python
    def __init__(self, model: str = "claude-haiku-4-5-20251001", max_turns: int = 8,
                 host_tools=(), permission_mode: str | None = None) -> None:
        import claude_agent_sdk as _sdk  # noqa: F401  (lazy: ImportError → app injects None)
        self._model = model
        self._max_turns = max_turns
        self._host_tools = tuple(host_tools)
        self._permission_mode = permission_mode
```

In `_run_async`, replace the allow-list + options construction (current lines 67-74) with:

```python
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
```

- [ ] **Step 5: Fix the stale docstring pointer in the swarm**

In `mcg_swarm/subagent/agent_runner.py`, line 5 references the old location. Change:

```python
real implementation lives in `sdk_runner.py`; `FakeAgentRunner` here makes the verifier
```

to:

```python
real implementation lives in `agent_runtime.claude_sdk_runner`; `FakeAgentRunner` here
```

- [ ] **Step 6: Run tests + full suite**

Run: `pytest tests/test_claude_sdk_runner.py -v`
Expected: PASS (2 passed)

Run: `pytest -q`
Expected: **248 passed, 2 skipped** (246 from Task 2 + 2 new). No import errors from the relocated module.

- [ ] **Step 7: Commit**

```bash
git add agent_runtime/ mcg_swarm/subagent/agent_runner.py tests/test_claude_sdk_runner.py
git commit -m "refactor(runtime): relocate ClaudeSDKAgentRunner to agent_runtime + host-cap merge"
```

---

### Task 4: Composition roots — demo injects a runner

**Files:**
- Modify: `demo_walkthrough.py` (imports + `_require_real_react`, `scenario_multi`, `scenario_fix`)

**Interfaces:**
- Consumes: `run_swarm` (Task 2), `SwarmConfig` (Task 1), `build_subagent`/`build_table_validator` (Task 2), `ClaudeSDKAgentRunner` (Task 3).
- Produces: a runnable demo where the app constructs and injects the runner (no env-driven provider selection).

- [ ] **Step 1: Update imports**

In `demo_walkthrough.py`, replace lines 23-24:

```python
from mcg_swarm.runner import run_swarm
from mcg_swarm.subagent import build_subagent, build_table_validator
```

with:

```python
from mcg_swarm.runner import run_swarm
from mcg_swarm.config import SwarmConfig
from mcg_swarm.subagent import build_subagent, build_table_validator
```

- [ ] **Step 2: Build + assert the real runner explicitly**

Replace `_require_real_react` (lines 32-45) with:

```python
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
```

- [ ] **Step 3: Static scenario injects no runner**

Replace `scenario_multi` (lines 48-53):

```python
def scenario_multi():
    """Big single table -> the swarm splits it into bands -> multiple subagents (static)."""
    ext = run_swarm({"main": MULTI_WB})  # no runner -> static-only
    print(ext)
    return ext
```

- [ ] **Step 4: Fix scenario builds + injects the runner**

Replace `scenario_fix` (lines 56-76):

```python
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
```

- [ ] **Step 5: Verify the offline path runs**

Run: `python demo_walkthrough.py multi`
Expected: prints a `WorkbookExtraction` and exits 0 (static path, no SDK/auth required).

Run: `python -c "import demo_walkthrough"`
Expected: imports clean, no error (no env/provider coupling at import time).

- [ ] **Step 6: Full suite regression**

Run: `pytest -q`
Expected: **248 passed, 2 skipped** (unchanged from Task 3 — the demo is not under pytest).

- [ ] **Step 7: Commit**

```bash
git add demo_walkthrough.py
git commit -m "refactor(demo): build and inject the ReAct runner; drop env-driven selection"
```

---

## Self-Review

**Spec coverage:**
- Decision 1 (pure DI, A) → Task 2 (`run_swarm`/factories take injected runner; env branch deleted).
- Decision 2 (inject at `AgentRunner` seam) → Task 2 (factories build `EscalatingSubagent`/`TableValidator` from the injected runner; escalation/repair logic untouched).
- Decision 3 (`runner=None` → static-only; auth probe/env deleted) → Task 2 (`build_subagent`/`build_table_validator` None branches; `_agent_auth_available` removed).
- Decision 4 (gated investigation) → Task 3 (`finalize`/schema/verify-before-accept untouched; host tools merged but mutation path unchanged) + Global Constraints.
- Decision 5 (host caps on the runner, not swarm tools) → Task 3 (`host_tools`/`permission_mode` on `ClaudeSDKAgentRunner`; `build_allowed_tools` merge) + Task 4 (demo configures them).
- Decision 6 (`SwarmConfig` values only) → Task 1.
- Decision 7 (runner separate from config) → Task 2 (`run_swarm(..., runner=..., config=...)` distinct params).
- Relocation of `sdk_runner.py` out of `mcg_swarm` → Task 3.
- Deletes (`_agent_auth_available`, `MCG_SUBAGENT` branch, env reads) → Task 2.
- Testing strategy (pure-value config; `runner=None`; injected fake runner; tool-merge unit test; gate invariant via existing suite) → Tasks 1-3 tests + full-suite regressions.
- Composition roots (demo/tests build runner) → Task 4 (demo); test callers default to `None` (Task 2 Step 5).

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to". Every code/edit step shows complete content and exact commands.

**Type consistency:** `build_subagent(llm, runner, config)`, `build_table_validator(runner, config)`, `run_swarm(workbooks, *, llm, runner, config)`, `SwarmConfig(validate, repair_max_passes)`, `ClaudeSDKAgentRunner(model, max_turns, host_tools, permission_mode)`, `build_allowed_tools(tools, host_tools)` — names/signatures used identically across tasks. `FakeAgentRunner(actions=, final=)` matches `agent_runner.py`. `Tool(name=, description=, input_schema=, handler=)` matches `tools.py`.

**Note on test count:** Task 2 expects 246 (−1 net from deleting `test_validator_activation.py` + reshaping `test_subagent_build.py`); Task 3 expects 248 (+2). If the live `test_react_uses_cli_auth_without_key` was being skipped in this environment, adjust the skipped count accordingly — the invariant is **zero failures/errors**, with deltas confined to the files each task touches.
