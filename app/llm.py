"""Groq LLM client (OpenAI-compatible API) with retries, model fallback and robust JSON parsing.

Groq's reasoning models sometimes wrap output in <think> tags, add prose around JSON, or
fail `json_object` validation. Every call here survives that: retry, then fall back to the
secondary model, then fall back to tolerant parsing.
"""
from __future__ import annotations

import json
import re
from typing import Any, Protocol

from .inspector import Inspector


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    async def complete(self, system: str, user: str, *, label: str, temperature: float = 0.2) -> str: ...
    async def complete_json(self, system: str, user: str, *, label: str) -> dict[str, Any]: ...


_THINK = re.compile(r"<think>.*?</think>", re.S)


# gpt-oss likes typographic hyphens/spaces ("NW\u2011240"); they would break ticket tags and name matching.
_TYPOGRAPHIC = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u00a0": " ",
                              "\u202f": " ", "\u2009": " "})


def extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model response."""
    text = _THINK.sub("", text or "").translate(_TYPOGRAPHIC).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start: i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise LLMError("model response did not contain valid JSON")


class GroqLLM:
    def __init__(
        self,
        api_key: str | None,
        base_url: str,
        model: str,
        fallback_model: str | None,
        timeout: float,
        inspector: Inspector,
        max_tokens: int = 3500,
    ) -> None:
        from openai import AsyncOpenAI

        if not api_key:
            raise LLMError("GROQ_API_KEY is not set")
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=2)
        self.models = [m for m in (model, fallback_model) if m]
        self.inspector = inspector
        self.max_tokens = max_tokens

    async def _call(self, model: str, messages: list[dict[str, str]], temperature: float, json_mode: bool) -> str:
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature,
                                  "max_completion_tokens": self.max_tokens}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = await self.client.chat.completions.create(**kwargs)
        return _THINK.sub("", resp.choices[0].message.content or "").strip()

    async def _run(self, system: str, user: str, *, label: str, temperature: float, json_mode: bool) -> tuple[str, str]:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last: Exception | None = None
        for model in self.models:
            for use_json_mode in ([True, False] if json_mode else [False]):
                try:
                    async with self.inspector.track("llm", label=label, model=model, json_mode=use_json_mode) as out:
                        text = await self._call(model, messages, temperature, use_json_mode)
                        if json_mode:
                            extract_json(text)  # validate inside the tracked block so failures are visible
                        out["chars"] = len(text)
                    return text, model
                except Exception as exc:  # noqa: BLE001 - try next strategy/model
                    last = exc
        raise LLMError(f"all LLM attempts failed: {last}")

    async def complete(self, system: str, user: str, *, label: str, temperature: float = 0.2) -> str:
        text, _ = await self._run(system, user, label=label, temperature=temperature, json_mode=False)
        return text

    async def complete_json(self, system: str, user: str, *, label: str) -> dict[str, Any]:
        text, _ = await self._run(system, user, label=label, temperature=0.0, json_mode=True)
        return extract_json(text)
