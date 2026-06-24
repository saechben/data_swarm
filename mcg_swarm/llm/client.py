from __future__ import annotations
import json, os, re
from typing import Any, Optional, Protocol, Type

from pydantic import BaseModel, ValidationError


def extract_json(text: str) -> dict:
    """Extract the first JSON object from text, handling fenced blocks and prose."""
    # (a) Try raw JSON first (stripped)
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # (b) Try ```json ... ``` fenced block
    m = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # (c) Try ``` ... ``` unlabeled fenced block
    m = re.search(r"```\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # (d) Find first balanced {...} object in prose
    start = text.find("{")
    while start != -1:
        depth = 0
        i = start
        in_str = False
        escape = False
        while i < len(text):
            ch = text[i]
            if escape:
                escape = False
            elif ch == "\\" and in_str:
                escape = True
            elif ch == '"':
                in_str = not in_str
            elif not in_str:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:i + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break
            i += 1
        start = text.find("{", start + 1)

    raise ValueError(f"no JSON object found in text: {text[:200]!r}")


# A schema is a Pydantic model class (enforced) or None (no enforcement).
Schema = Optional[Type[BaseModel]]


class LLMSchemaError(ValueError):
    """An LLM response did not conform to the requested output schema.

    Raised at the client boundary so a malformed response fails loud HERE rather
    than surfacing as a downstream KeyError/AttributeError. Callers that wrap
    ``complete`` in try/except (subagent, header fallback, resolver) turn this into
    their normal deterministic fallback — so enforcement makes the swarm *less*
    brittle, not more.
    """


def _is_model(schema: Any) -> bool:
    return isinstance(schema, type) and issubclass(schema, BaseModel)


def _enforce(raw: Any, schema: Schema) -> dict:
    """Validate ``raw`` against ``schema`` and return a clean dict.

    - ``schema is None`` (or not a model): pass ``raw`` through unchanged (legacy /
      unstructured calls).
    - A Pydantic model: validate, then return ``model_dump(exclude_none=True)`` — a
      plain dict with every required field present and unset fields omitted (so
      callers' ``.get(key, default)`` semantics are preserved exactly).
    """
    if not _is_model(schema):
        return raw
    try:
        model = schema.model_validate(raw)
    except ValidationError as exc:
        raise LLMSchemaError(
            f"LLM response did not match schema {schema.__name__}: {exc}"
        ) from exc
    return model.model_dump(exclude_none=True)


class LLMClient(Protocol):
    def complete(self, system: str, user: str, *, schema: Schema = None) -> dict: ...


class _SchemaEnforcedClient:
    """Base for LLM clients: ``complete`` = backend call + boundary schema enforcement.

    Concrete clients implement ``_raw_complete`` only; validation lives here ONCE so
    EVERY backend — the one-shot ``AnthropicClient``, the test ``FakeLLMClient``, or a
    future agentic/ReAct client — returns a response already validated against the
    requested schema. The swarm calls ``complete`` and never learns which backend ran.
    """

    def complete(self, system: str, user: str, *, schema: Schema = None) -> dict:
        raw = self._raw_complete(system, user, schema=schema)
        return _enforce(raw, schema)

    def _raw_complete(self, system: str, user: str, *, schema: Schema = None) -> Any:
        raise NotImplementedError


class FakeLLMClient(_SchemaEnforcedClient):
    def __init__(self, responses):
        self._responses = responses
        self._i = 0
        self.calls: list[dict] = []

    def _raw_complete(self, system: str, user: str, *, schema: Schema = None) -> Any:
        self.calls.append({"system": system, "user": user, "schema": schema})
        if callable(self._responses):
            return self._responses(system=system, user=user, schema=schema)
        resp = self._responses[self._i]   # IndexError when exhausted (fail-loud in tests)
        self._i += 1
        return resp


class AnthropicClient(_SchemaEnforcedClient):
    def __init__(self, model: str = "claude-opus-4-8", api_key: Optional[str] = None):
        from anthropic import Anthropic          # lazy import
        self._client = Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])
        self._model = model

    def _raw_complete(self, system: str, user: str, *, schema: Schema = None) -> Any:
        if _is_model(schema):
            hint = json.dumps(schema.model_json_schema())
            instr = f"{user}\n\nReturn ONLY a JSON object matching this JSON Schema:\n{hint}"
        else:
            instr = user
        msg = self._client.messages.create(
            model=self._model, max_tokens=2048, system=system,
            messages=[{"role": "user", "content": instr}])
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        return extract_json(text)


def default_client() -> LLMClient:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicClient()
    raise RuntimeError("No ANTHROPIC_API_KEY; inject an LLMClient explicitly (e.g. FakeLLMClient).")
