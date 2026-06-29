# Agent Runner Injection — Design

**Date:** 2026-06-29
**Status:** Approved (brainstorming) — pending spec review
**Topic:** Evict provider/transport wiring (Claude SDK → LiteLLM → Bedrock) from the swarm; inject the ReAct runner from the outside.

## Problem

The ReAct verification agent's provider wiring is built *inside* the swarm package. Today `run_swarm` (`mcg_swarm/runner.py:35-36`) calls `build_subagent` / `build_table_validator` (`mcg_swarm/subagent/__init__.py`), which:

- read env to select a provider (`MCG_SUBAGENT`),
- probe auth (`_agent_auth_available`: `ANTHROPIC_API_KEY` or `claude` CLI),
- lazy-import the Claude Agent SDK,
- construct `ClaudeSDKAgentRunner()` directly.

In the target production setting the runner is a Claude-SDK ReAct agent initialized against **Bedrock via LiteLLM**. That connection logic does not belong next to orchestration. Worse, the env-selection branch is **dead code in production** (the provider is always the Bedrock runner there) — an untested liability sitting beside the swarm.

The orchestrator itself is already clean (it imports no SDK; it knows only the `Subagent` and `AgentRunner` Protocols). The coupling that remains lives entirely in the **factories** and in `sdk_runner.py`.

## Goal

The swarm depends only on an **abstraction** ("something that can run an agent loop"). The concrete runner — SDK choice, LiteLLM transport, Bedrock provider, credentials, host capabilities — is built by the **application** and **injected** at the swarm's entry point. The swarm never names a provider.

## Decisions (locked)

1. **Pure dependency injection (path A).** The runner is constructed entirely outside `mcg_swarm` and passed into `run_swarm`. No provider-selection seam remains in the swarm. (Rejected: an in-package `build_runner(provider=…)` factory — it would be dead code in production.)
2. **Inject at the `AgentRunner` seam**, not the full `Subagent`. The provider connection is the `AgentRunner`. Escalation policy, the static deterministic fallback, verify-before-accept, and repair-pass logic are **swarm domain knowledge** and stay inside `mcg_swarm`.
3. **Graceful degradation is the app's decision.** `runner=None` → static-only band subagent and no table validator. The auth probe and env switch are deleted from the swarm; the app decides whether it can build a runner.
4. **Gated investigation (option a).** The agent gets full investigative reach (bash / filesystem / grep) via the runner's host capabilities, but the **table mutation still exits only through `finalize` → verify-before-accept**. The "provably better or keep static / never-raise" guarantee is preserved. (Rejected: free-action bash that mutates state outside the gate.)
5. **Host capabilities live on the injected runner**, not in the swarm tool list. The SDK's built-in Bash/Read/Grep are enabled by the app-built runner via `allowed_tools` + `permission_mode`. (Rejected: adding a `subprocess`-backed bash `Tool` to `build_band_toolset` — that drops host power back into the swarm.)
6. **`SwarmConfig` value object** holds behavior knobs only. Config = plain data the swarm acts on. It **never** holds provider/model/credentials or the runner.
7. **Runner and config are separate arguments (option i).** Collaborators are injected; values are configured. `llm` likewise stays a separate injected collaborator.

## Architecture

```
┌─ APP / composition layer  (OUTSIDE mcg_swarm) ──────────────────────────┐
│  build_bedrock_react_runner()                                           │
│    Claude Agent SDK → LiteLLM → Bedrock      (the "mess")               │
│    + host caps: Bash / Read / Grep, permission_mode, sandbox policy     │
│  returns: AgentRunner ───────────────┐                                  │
└───────────────────────────────────────┼─────────────────────────────────┘
                                         ▼
   run_swarm(workbooks, *, llm=None,
             runner: AgentRunner | None = None,
             config: SwarmConfig = SwarmConfig())
                                         │   mcg_swarm — never names a provider
                                         ▼
   build_subagent(llm, runner, config)          build_table_validator(runner, config)
     runner is not None →                          runner is not None →
       EscalatingSubagent(                           TableValidator(
         StaticSubagent(llm),                          runner,
         ReActVerifier(runner),                        TableCheckPolicy(
         EscalationPolicy(validate=config.validate))     validate=config.validate,
     runner is None → StaticSubagent(llm)               max_passes=config.repair_max_passes))
                                                     runner is None → None
```

**Dependency direction:** app → `mcg_swarm` (the app imports `AgentRunner` / `Tool` types from the swarm). The swarm never imports the app or any provider. This is unchanged for the orchestrator and now also true of the factories.

## Components

### Stays in `mcg_swarm` (unchanged behavior)
- `AgentRunner` Protocol — `mcg_swarm/subagent/agent_runner.py`. `run(seed: str, tools: list[Tool], *, schema) -> dict`. **Signature unchanged.**
- `Tool` dataclass + `build_band_toolset` — `mcg_swarm/subagent/tools.py`. The 6 read-only band probes + `finalize` semantics. **Unchanged.**
- `EscalatingSubagent` / `EscalationPolicy`, `ReActVerifier`, `TableValidator` / `TableCheckPolicy`, `StaticSubagent`. The verify-before-accept and repair loops. **Unchanged logic**; they now receive an injected `runner` and policy values from `config`.
- `orchestrate_table` — already takes `subagent` / `table_validator` opaquely. **No change.**

### Leaves `mcg_swarm`
- `ClaudeSDKAgentRunner` (`mcg_swarm/subagent/sdk_runner.py`) → relocates to the new provider layer. It is a provider adapter, not swarm logic.

### Deleted from `mcg_swarm`
- `_agent_auth_available()`
- the `MCG_SUBAGENT` branch in both factories
- the lazy `from mcg_swarm.subagent.sdk_runner import ClaudeSDKAgentRunner` imports and every `ClaudeSDKAgentRunner()` construction
- the env reads `_validate_enabled()` / `_max_passes()` (values now arrive via `SwarmConfig`)

### New / changed in `mcg_swarm`
- `SwarmConfig` (frozen dataclass):
  ```python
  @dataclass(frozen=True)
  class SwarmConfig:
      validate: bool = True          # was MCG_REACT_VALIDATE (default on)
      repair_max_passes: int = 3     # was MCG_REPAIR_MAX_PASSES (default 3, clamped >= 1)
      # room to grow: escalation thresholds, band size gates
  ```
- `build_subagent(llm=None, runner=None, config=SwarmConfig())` — no env, no auth probe. `runner is None` → `StaticSubagent(llm)`. Else `EscalatingSubagent(StaticSubagent(llm), ReActVerifier(runner), EscalationPolicy(validate=config.validate))`.
- `build_table_validator(runner=None, config=SwarmConfig())` — `runner is None` → `None`. Else `TableValidator(runner, TableCheckPolicy(validate=config.validate, max_passes=config.repair_max_passes))`.
- `run_swarm(workbooks, *, llm=None, runner=None, config=SwarmConfig())` — threads `runner` + `config` into both factories.

### New provider layer (outside `mcg_swarm`)
A sibling package, recommended name `agent_runtime/`:
- `bedrock_react_runner.py` — **new.** Production runner: Claude Agent SDK → LiteLLM → Bedrock, with host capabilities enabled. Implements `AgentRunner`.
- `claude_sdk_runner.py` — the **relocated** `ClaudeSDKAgentRunner` (Anthropic-direct), retained for demo/tests. Implements `AgentRunner`.

Both import `AgentRunner` / `Tool` from `mcg_swarm`.

## Tool / capability model (gated)

Two distinct tool kinds, in two distinct homes:

- **Domain tools** — the swarm's 6 read-only band probes + `finalize`. Passed per call via `runner.run(seed, tools, schema)`. Owned by the swarm.
- **Host capabilities** — Bash / Read / Grep. Enabled on the **app-built runner** at construction, via SDK `allowed_tools` + `permission_mode` (+ sandbox policy the app chooses).

The concrete runner's `run()` **merges** the two into the final whitelist:

```
allowed_tools = [f"mcp__band__{t.name}" for t in tools] + ["mcp__band__finalize"]
              + [host built-ins the runner was configured with]   # e.g. "Bash", "Read", "Grep"
```

**Safety invariant (gate held):** the agent may read/grep/run scratch commands to *investigate*, but the only path that changes the table is the structured `finalize` patch, which is validated against `schema` and then run through the quality gate (verify-before-accept). Host side effects are investigation, not the fix. The never-raise behavior of `ReActVerifier` / `TableValidator` is unchanged.

The app owns *how much* host power the agent gets (which commands, sandboxed or not, permission mode); the swarm owns *what domain actions exist*. Neither concern leaks into the other.

## Composition roots

`demo_walkthrough.py` and the test suite become composition roots: each builds a concrete `AgentRunner` (the relocated `ClaudeSDKAgentRunner`, or a fake) and passes it into `run_swarm(..., runner=..., config=...)`. The demo's "real ReAct must be active" assertion is satisfied by constructing the runner explicitly rather than by env + auth probe.

## Testing strategy

- **Pure-value config:** `SwarmConfig(validate=False)` constructs with no runner — exercises factory wiring without any SDK.
- **`runner=None` path:** `run_swarm(wb)` → static-only; assert no validator, deterministic output (existing static suite, unchanged).
- **Injected fake runner:** a `FakeAgentRunner` implementing `run()` returns canned patches — drives `ReActVerifier` / `TableValidator` deterministically, no network, no SDK. Replaces the env-driven `MCG_SUBAGENT=react` test setup.
- **Tool-merge unit test:** the concrete runner's `run()` produces a whitelist containing both `mcp__band__*` and the configured host built-ins.
- **Gate invariant:** with a fake runner returning a worse table, verify-before-accept keeps the static result (regression guard for the never-raise guarantee).
- Existing band/table/formula suites must stay green (247 passed / 2 skipped baseline on `main`).

## Migration / back-compat

- `build_subagent` / `build_table_validator` signatures change (env → params). `analyze_band` shim stays.
- Env vars `MCG_SUBAGENT`, `MCG_REACT_VALIDATE`, `MCG_REPAIR_MAX_PASSES`, and `_agent_auth_available` semantics are **removed from the swarm**. If env-based configuration is still wanted operationally, the *app* (demo / production composition root) reads env and translates into `SwarmConfig` + a runner-or-None — at the edge, not inside the swarm.
- One-time migration of any caller relying on env-driven `run_swarm`.

## Out of scope

- Implementing the LiteLLM → Bedrock connection details (provider layer internals) — this design only fixes the *seam*; the Bedrock runner is built against it next.
- Changing the band probe toolset, escalation thresholds, or the patch schemas.
- Any orchestrator (`orchestrate_table`) logic change.
```
