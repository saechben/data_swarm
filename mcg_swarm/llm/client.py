from __future__ import annotations
import json, os, re
from typing import Any, Callable, Optional, Protocol


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
        return extract_json(text)


def default_client() -> LLMClient:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicClient()
    raise RuntimeError("No ANTHROPIC_API_KEY; inject an LLMClient explicitly (e.g. FakeLLMClient).")
