from __future__ import annotations
import json, os
from typing import Any, Callable, Optional, Protocol


class LLMClient(Protocol):
    def complete(self, system: str, user: str, *, schema: Optional[dict] = None) -> dict: ...


class FakeLLMClient:
    def __init__(self, responses):
        self._responses = responses
        self._i = 0
        self.calls: list[dict] = []

    def complete(self, system: str, user: str, *, schema: Optional[dict] = None) -> dict:
        self.calls.append({"system": system, "user": user, "schema": schema})
        if callable(self._responses):
            return self._responses(system=system, user=user, schema=schema)
        resp = self._responses[self._i]   # IndexError when exhausted (fail-loud in tests)
        self._i += 1
        return resp


class AnthropicClient:
    def __init__(self, model: str = "claude-opus-4-8", api_key: Optional[str] = None):
        from anthropic import Anthropic          # lazy import
        self._client = Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])
        self._model = model

    def complete(self, system: str, user: str, *, schema: Optional[dict] = None) -> dict:
        instr = user if schema is None else f"{user}\n\nReturn ONLY JSON matching: {json.dumps(schema)}"
        msg = self._client.messages.create(
            model=self._model, max_tokens=2048, system=system,
            messages=[{"role": "user", "content": instr}])
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        return json.loads(text)


def default_client() -> LLMClient:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicClient()
    raise RuntimeError("No ANTHROPIC_API_KEY; inject an LLMClient explicitly (e.g. FakeLLMClient).")
