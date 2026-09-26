"""Каталог моделей OpenRouter для выпадающего списка в интерфейсе.

Зачем живой список, а не зашитый: моделей больше четырёхсот, они появляются и
исчезают каждую неделю, а цены и акции меняются ещё чаще. Любой список,
записанный в код, устареет к следующему запуску — поэтому он тянется с
`openrouter.ai/api/v1/models` и кэшируется на несколько часов.

Если сети нет, отдаётся короткий запасной набор: пустой выпадающий список
хуже устаревшего.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from core.logging_setup import get_logger
from core.settings import Settings, get_settings

logger = get_logger("models")

CATALOG_URL = "https://openrouter.ai/api/v1/models"

#: Кэш живёт полдня: список меняется не по минутам, а запрос лишний раз ждать не нужно.
CACHE_TTL = 12 * 3600

#: Всегда первым: автоподбор модели под задачу — не надо помнить названия.
AUTO_MODEL = "openrouter/auto-beta"

#: Проверенные варианты — их поднимаем наверх списка. Остальные идут следом.
FEATURED = (
    AUTO_MODEL,
    "anthropic/claude-sonnet-4.5",
    "anthropic/claude-opus-4.1",
    "openai/gpt-5",
    "openai/gpt-5-mini",
    "google/gemini-3.7-flash",
    "google/gemini-2.5-pro",
    "deepseek/deepseek-v4-pro",
    "deepseek/deepseek-v4-flash",
    "qwen/qwen3-coder",
    "x-ai/grok-4.6",
)

#: На случай, когда каталог недоступен (нет сети, первый запуск офлайн).
FALLBACK = tuple({"id": model_id, "name": model_id, "tools": True} for model_id in FEATURED)


@dataclass(slots=True)
class ModelCatalog:
    """Список моделей с кэшем на диске."""

    settings: Settings = field(default_factory=get_settings)

    @property
    def cache_path(self) -> Path:
        return self.settings.app_dir / "storage" / "models.json"

    async def load(self, *, force: bool = False) -> dict[str, Any]:
        """Каталог моделей. Возвращает {"models": [...], "source": "..."}"""
        cached = self._read_cache()
        if cached and not force and time.time() - cached.get("fetched_at", 0) < CACHE_TTL:
            return {"models": cached["models"], "source": "кэш", "fetched_at": cached["fetched_at"]}

        try:
            models = await self._fetch()
        except Exception as exc:  # noqa: BLE001 - офлайн это норма, а не поломка
            logger.info("Каталог моделей недоступен (%s)", exc)
            if cached:
                return {
                    "models": cached["models"],
                    "source": "кэш (сеть недоступна)",
                    "fetched_at": cached.get("fetched_at", 0),
                }
            return {"models": list(FALLBACK), "source": "запасной список", "fetched_at": 0}

        self._write_cache(models)
        return {"models": models, "source": "openrouter", "fetched_at": time.time()}

    # ------------------------------------------------------------------

    async def _fetch(self) -> list[dict[str, Any]]:
        from core.tools.builtin.web import get_http_client

        response = await get_http_client().get(CATALOG_URL, timeout=httpx.Timeout(20.0))
        response.raise_for_status()
        raw = response.json().get("data", [])
        return sort_models([entry for entry in (describe(item) for item in raw) if entry])

    def _read_cache(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data.get("models"), list) and data["models"] else None

    def _write_cache(self, models: list[dict[str, Any]]) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps({"fetched_at": time.time(), "models": models}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:  # pragma: no cover - кэш не критичен
            logger.debug("Не удалось сохранить кэш моделей", exc_info=True)


def describe(item: dict[str, Any]) -> dict[str, Any] | None:
    """Оставляет от записи каталога только то, что нужно списку выбора.

    Модели без поддержки вызова инструментов отбрасываются: агент без
    инструментов бесполезен, а в списке они только сбивают с толку.
    """
    model_id = str(item.get("id") or "").strip()
    if not model_id:
        return None

    supported = item.get("supported_parameters") or []
    if "tools" not in supported and model_id != AUTO_MODEL:
        return None

    architecture = item.get("architecture") or {}
    modalities = architecture.get("input_modalities") or []
    pricing = item.get("pricing") or {}

    return {
        "id": model_id,
        "name": str(item.get("name") or model_id),
        "context": int(item.get("context_length") or 0),
        "vision": "image" in modalities,
        "video": "video" in modalities,
        "prompt_price": _price(pricing.get("prompt")),
        "completion_price": _price(pricing.get("completion")),
        "tools": True,
    }


def _price(value: Any) -> float:
    """Цена за миллион токенов. -1 у автороутера означает «зависит от модели»."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(number * 1_000_000, 4) if number > 0 else number


def sort_models(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Проверенные модели наверх, остальные — по алфавиту."""
    order = {model_id: index for index, model_id in enumerate(FEATURED)}
    return sorted(
        models,
        key=lambda m: (order.get(m["id"], len(order)), m["id"]),
    )
