"""LLM with backup models: if the primary doesn't answer, try the next one.

Used by model routing — each tier (fast/strong) can list backup models, so a
provider outage or an unavailable model doesn't kill the run: we transparently
fall through to the next candidate in the same tier.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

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

    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> AssistantTurn:
        last_exc: Exception | None = None
        for index, cand in enumerate(self._cands):
            try:
                client = self._client(index)
            except Exception as exc:  # noqa: BLE001 - не удалось создать клиент
                last_exc = exc
                logger.warning("Запасная модель #%d (%s): не создать — %s", index, cand.get("model"), exc)
                continue
            try:
                turn = await client.complete(messages, **kwargs)
                self.model = cand["model"]
                if index > 0:
                    logger.info("Основная модель не ответила — использована запасная %s", self.model)
                return turn
            except Exception as exc:  # noqa: BLE001 - модель не ответила → следующая
                last_exc = exc
                nxt = self._cands[index + 1]["model"] if index + 1 < len(self._cands) else "—"
                logger.warning("Модель %s не ответила (%s) → пробую %s", cand.get("model"), str(exc)[:120], nxt)
                continue
        raise last_exc if last_exc else RuntimeError("Нет доступных моделей в тире")

    async def aclose(self) -> None:
        for client in self._clients.values():
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass
