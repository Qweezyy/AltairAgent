"""LLM with backup models: if the primary doesn't answer, try the next one.

Used by model routing — each tier (fast/strong) can list backup models, so a
provider outage or an unavailable model doesn't kill the run: we transparently
fall through to the next candidate in the same tier.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from core.llm import reliability
from core.llm.base import AssistantTurn, LLMClient
from core.logging_setup import get_logger

logger = get_logger("llm.fallback")


class FallbackLLM(LLMClient):
    """Tries candidates in order; on failure switches to the next backup.

    Clients are built lazily and all are closed on aclose(). Note: a candidate
    that fails only AFTER it started streaming text could cause a small
    duplicate; in practice "model doesn't answer" errors (connect/timeout/auth/
    404) fail before any text streams, so the switch is clean.
    """

    def __init__(self, candidates: list[dict[str, Any]], build: Callable[..., LLMClient]) -> None:
        cands = [c for c in candidates if c.get("model")]
        if not cands:
            raise ValueError("FallbackLLM требует хотя бы одну модель")
        self._cands = cands
        self._build = build
        self._clients: dict[int, LLMClient] = {}
        self.model = cands[0]["model"]

    def _client(self, index: int) -> LLMClient:
        if index not in self._clients:
            kw = {k: v for k, v in self._cands[index].items() if v and k in ("model", "base_url", "api_key")}
            self._clients[index] = self._build(**kw)
        return self._clients[index]

    def _key(self, index: int) -> str:
        cand = self._cands[index]
        client = self._clients.get(index)
        base = getattr(client, "base_url", None) or cand.get("base_url") or ""
        return f"{base}|{cand.get('model')}"

    def _order(self) -> list[int]:
        """Healthy candidates first (in their order), then the ones the breaker holds back —
        those are still tried when nothing healthy is left."""
        indexes = list(range(len(self._cands)))
        healthy = [i for i in indexes if not reliability.health(self._key(i)).sick]
        return healthy + [i for i in indexes if i not in healthy]

    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> AssistantTurn:
        from core.settings import get_settings

        settings = get_settings()
        last_exc: Exception | None = None
        order = self._order()
        for pos, index in enumerate(order):
            cand = self._cands[index]
            try:
                client = self._client(index)
            except Exception as exc:  # noqa: BLE001 - не удалось создать клиент
                last_exc = exc
                logger.warning("Запасная модель #%d (%s): не создать — %s", index, cand.get("model"), exc)
                continue
            client.reasoning = self.reasoning
            # With a backup behind it a client gives up after two attempts: the step moves on
            # instead of waiting out five retries against a provider that is down or slow.
            client.max_attempts = 2 if pos < len(order) - 1 else self.max_attempts
            key = self._key(index)
            try:
                turn = await client.complete(messages, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - модель не ответила → следующая
                last_exc = exc
                reliability.health(key).failed(settings.llm_breaker_minutes, str(exc)[:120])
                nxt = self._cands[order[pos + 1]]["model"] if pos + 1 < len(order) else "—"
                logger.warning("Модель %s не ответила (%s) → пробую %s", cand.get("model"), str(exc)[:120], nxt)
                continue
            self.model = cand["model"]
            if reliability.health(key).answered(turn.first_byte_s, settings.llm_slow_first_byte,
                                                settings.llm_breaker_minutes):
                logger.warning("%s is slow (%s): the next steps go to a backup model for %.0f min",
                               cand.get("model"), reliability.health(key).reason, settings.llm_breaker_minutes)
            if index > 0:
                logger.info("Основная модель не ответила — использована запасная %s", self.model)
            return turn
        raise last_exc if last_exc else RuntimeError("Нет доступных моделей в тире")

    async def aclose(self) -> None:
        for client in self._clients.values():
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass
