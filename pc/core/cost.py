"""Оценка стоимости запроса по ценам моделей.

Зачем: агент ходит в платный API, и человек должен видеть, во что обошлась
задача, — иначе «31 030 токенов» ничего не говорит. Цены берём из того же
каталога OpenRouter, что и список моделей (кэш на диске), поэтому отдельного
источника цен держать не нужно.

Если цен нет (каталог не скачан, локальная модель) — стоимость неизвестна, и
мы показываем только токены, честно не выдумывая число.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from core.logging_setup import get_logger
from core.settings import Settings

logger = get_logger("cost")

#: Варианты модели, не влияющие на цену: снимок по дате, :free, :batch и т.п.
#: Их отсекаем при сопоставлении с каталогом.
_VARIANT_SUFFIX = ("-0", "-1", "-2", "-20", "-latest")


@dataclass(slots=True)
class Cost:
    """Стоимость и токены запроса."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    usd: float = 0.0
    #: Известна ли цена (нет — показываем только токены).
    priced: bool = False

    def to_public(self) -> dict:
        return {
            "tokens": self.total_tokens,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "usd": round(self.usd, 6),
            "priced": self.priced,
        }


def load_pricing(settings: Settings) -> dict[str, tuple[float, float]]:
    """Карта {model_id: (цена_промпта, цена_ответа)} за миллион токенов."""
    cache = settings.app_dir / "storage" / "models.json"
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

    pricing: dict[str, tuple[float, float]] = {}
    for model in data.get("models", []):
        model_id = model.get("id")
        prompt = model.get("prompt_price")
        completion = model.get("completion_price")
        # Автороутер и бесплатные модели имеют цену -1 или 0 — их пропускаем.
        if model_id and isinstance(prompt, (int, float)) and prompt > 0:
            pricing[model_id] = (float(prompt), float(completion or 0))
    return pricing


def _match_price(model: str, pricing: dict[str, tuple[float, float]]) -> tuple[float, float] | None:
    """Находит цену модели: точное совпадение, затем без варианта/суффикса."""
    if model in pricing:
        return pricing[model]

    # Отсекаем вариант после двоеточия: «gpt-5:free» -> «gpt-5».
    base = model.split(":", 1)[0]
    if base in pricing:
        return pricing[base]

    # Модель может быть датированным снимком: «...-flash-0731» -> «...-flash».
    for known in pricing:
        if base.startswith(known) and base[len(known):].lstrip("-").isdigit():
            return pricing[known]
    return None


def estimate_cost(usage: dict[str, int], model: str, pricing: dict[str, tuple[float, float]]) -> Cost:
    """Считает стоимость запроса. Без известной цены usd=0 и priced=False."""
    prompt = int(usage.get("prompt_tokens", 0) or 0)
    completion = int(usage.get("completion_tokens", 0) or 0)
    total = int(usage.get("total_tokens", 0) or 0) or (prompt + completion)

    price = _match_price(model, pricing) if model else None
    if price is None:
        return Cost(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)

    prompt_rate, completion_rate = price
    usd = prompt / 1_000_000 * prompt_rate + completion / 1_000_000 * completion_rate
    return Cost(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        usd=usd,
        priced=True,
    )
