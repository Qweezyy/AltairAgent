"""Talking to providers that are cheap but not always steady.

Measured on real providers (docs/… lab notes in the changelog): an answer can fail without an
HTTP error — a gateway error text sent as the model's reply, an empty stream, a stream cut at
our loop guard — and the agent then took it for a finished task. A provider also has slow
periods that last hours, where retrying the same provider only queues again. And without an
explicit reasoning level a reasoning model may think without end. This module holds what the
clients need against all of that:

* `reasoning_extra()` — the request fields that set the reasoning level, in each provider's
  dialect (OpenRouter `reasoning.effort`, OpenAI `reasoning_effort`, Z.ai-style `thinking`);
* `judge_turn()` — whether a finished answer is really one or a failure in disguise;
* `ProviderGate` — at most N requests in flight per provider, N lowered when the provider
  answers "too many concurrent requests";
* `health()` — a per-provider breaker: failing or repeatedly slow providers are skipped for a
  few minutes when a backup model is configured.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

from core.llm.base import AssistantTurn

#: Reasoning levels the app uses. "default" sends nothing (the provider decides).
LEVELS = ("minimal", "low", "medium", "high")

#: Texts gateways send as the "answer" when they did not run the model. Only matched when the
#: reply is short and has no tool calls, so a normal answer that mentions them is not caught.
GATEWAY_ERROR_PHRASES = (
    "the request could not be completed",
    "please retry later, or reduce the request",
    "upstream request failed",
    "no healthy upstream",
    "service temporarily unavailable",
)


class BadTurn(Exception):
    """A reply that is not a real answer; the client retries it like a dropped connection."""


def dialect(base_url: str, model: str, override: str = "auto") -> str:
    """How this provider takes a reasoning level: openrouter | openai | zai | none."""
    if override and override != "auto":
        return override
    base = (base_url or "").lower()
    name = (model or "").lower()
    if "openrouter.ai" in base:
        return "openrouter"
    if any(h in base for h in ("z.ai", "bigmodel.cn", "gateyourway")) or name.startswith("glm"):
        return "zai"
    return "openai"


def reasoning_extra(level: str | None, base_url: str, model: str, override: str = "auto") -> dict[str, Any]:
    """Request fields for a reasoning level ({} for "default" / unknown levels)."""
    if level not in LEVELS:
        return {}
    kind = dialect(base_url, model, override)
    if kind == "openrouter":
        # OpenRouter takes minimal..high; some endpoints refuse "none"/enabled=false, so the
        # lowest we send is "low" for "minimal" there.
        return {"reasoning": {"effort": "low" if level == "minimal" else level}}
    if kind == "zai":
        # Z.ai-style APIs have an on/off switch: low and minimal turn thinking off.
        return {"thinking": {"type": "disabled" if level in ("minimal", "low") else "enabled"}}
    if kind == "openai":
        return {"reasoning_effort": level}
    return {}


def _text_chars(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> int:
    """Characters of text the request carries (images and other media are not counted)."""
    total = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            total += sum(len(str(p.get("text", ""))) for p in content if isinstance(p, dict) and p.get("type") == "text")
        for call in m.get("tool_calls") or []:
            total += len(str((call.get("function") or {}).get("arguments", "")))
    if tools:
        total += len(json.dumps(tools, ensure_ascii=False))
    return total


def judge_turn(turn: AssistantTurn, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
               *, cut: bool = False) -> str | None:
    """Why this answer is not a real one, or None when it is.

    * cut — the stream hit our loop guard (the model was thinking or repeating without end);
    * empty — neither text nor a tool call;
    * gateway — a short reply that is a gateway's error text;
    * unprocessed — the provider counted far fewer prompt tokens than the request holds, so it
      did not run the model on it (seen with the "could not be completed" replies).
    """
    if turn.tool_calls:
        return None
    if cut:
        return "cut"
    text = (turn.content or "").strip()
    if not text:
        return "empty"
    low = text.lower()
    if len(text) < 400 and any(p in low for p in GATEWAY_ERROR_PHRASES):
        return "gateway"
    prompt = (turn.usage or {}).get("prompt_tokens") or 0
    expected = _text_chars(messages, tools) / 4.5
    if prompt and expected > 2000 and prompt < 0.5 * expected:
        return "unprocessed"
    return None


# ---------------------------------------------------------------- in-flight limit per provider

@dataclass
class ProviderGate:
    """At most `limit` requests in flight. Loop-agnostic (no asyncio primitives kept), so it is
    safe to share across event loops (tests, the CLI). `limit` 0 means no limit until the
    provider says "too many concurrent requests"; then it drops to what was in flight minus one
    and climbs back by one after every 20 clean answers."""

    limit: int = 0
    configured: int = 0
    in_flight: int = 0
    clean: int = 0

    async def acquire(self) -> None:
        while self.limit and self.in_flight >= self.limit:
            await asyncio.sleep(0.2)
        self.in_flight += 1

    def release(self, ok: bool = True) -> None:
        self.in_flight = max(0, self.in_flight - 1)
        if ok and self.limit and self.limit != self.configured:
            self.clean += 1
            if self.clean >= 20:
                self.clean = 0
                self.limit += 1
                if self.configured and self.limit >= self.configured:
                    self.limit = self.configured
                elif not self.configured and self.limit >= 16:
                    self.limit = 0  # back to unlimited

    def too_many(self) -> None:
        """The provider refused for concurrency: hold fewer requests in flight."""
        self.clean = 0
        self.limit = max(1, self.in_flight - 1)


_GATES: dict[str, ProviderGate] = {}


def gate(base_url: str, configured: int = 0) -> ProviderGate:
    g = _GATES.get(base_url)
    if g is None:
        g = _GATES[base_url] = ProviderGate(limit=configured, configured=configured)
    elif configured and g.configured != configured:
        g.configured = configured
        g.limit = configured if not g.limit else min(g.limit, configured)
    return g


def is_concurrency_refusal(text: str) -> bool:
    low = (text or "").lower()
    return "concurren" in low or "too many requests in flight" in low


# ---------------------------------------------------------------- breaker per provider

@dataclass
class Health:
    sick_until: float = 0.0
    slow_streak: int = 0
    reason: str = ""
    history: list[str] = field(default_factory=list)

    @property
    def sick(self) -> bool:
        return time.time() < self.sick_until

    def failed(self, minutes: float, reason: str) -> None:
        self.sick_until = time.time() + minutes * 60
        self.reason = reason
        self.slow_streak = 0

    def answered(self, first_byte_s: float, slow_after: float, minutes: float) -> bool:
        """Records an answer; True when the provider is now taken as sick for being slow."""
        if slow_after and first_byte_s > slow_after:
            self.slow_streak += 1
            if self.slow_streak >= 2:
                self.failed(minutes, f"slow: first byte after {first_byte_s:.0f} s twice in a row")
                return True
            return False
        self.slow_streak = 0
        return False


_HEALTH: dict[str, Health] = {}


def health(key: str) -> Health:
    return _HEALTH.setdefault(key, Health())


def reset_state() -> None:
    """Forget gates and health (tests)."""
    _GATES.clear()
    _HEALTH.clear()
