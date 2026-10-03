"""Маршрутизация моделей по сложности задачи (дешёвая/сильная + оценщик).

Идея: держать три модели — дешёвую/слабую (для простого), сильную/дорогую (для
сложного) и оценщика («судью»), который по тексту запроса решает, какая нужна.
Так простые задачи не жгут дорогую модель, а сложные не достаются слабой.

Каждый тир хранит СВОЙ провайдер (model+base_url+api_key), поэтому дешёвая,
сильная и оценщик могут быть у разных провайдеров и с разными ключами. Источник —
settings.model_tiers (JSON, формируется интерфейсом из вкладки «Модели и
провайдеры»); если пусто — плоские model_fast/strong/router у текущего провайдера.

Маршрутизация — на весь прогон (один вызов судьи на сообщение): дёшево, не ломает
кэш промпта и целостность tool-call. Любой сбой судьи — в пользу СИЛЬНОЙ модели.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from core.logging_setup import get_logger
from core.settings import Settings

logger = get_logger("agent.router")

_SYSTEM = (
    "You are a task-complexity router for an autonomous AI agent. "
    "Answer with exactly one word: SIMPLE or COMPLEX."
)
_USER = (
    "Classify the user's request.\n"
    "COMPLEX = multi-step work, coding, debugging, refactoring, planning, "
    "analysis, or anything that needs tools or several steps.\n"
    "SIMPLE = a short factual answer, a tiny edit, a quick question, or chit-chat.\n\n"
    "Request:\n{task}\n\n"
    "One word (SIMPLE or COMPLEX):"
)


def resolve_tiers(settings: Settings) -> dict[str, dict[str, str]] | None:
    """Возвращает {fast,strong,router} с {model,base_url,api_key} или None.

    Приоритет — settings.model_tiers (JSON, свой провайдер у каждого тира). Если
    его нет, но заданы плоские model_fast/model_strong — используем их у текущего
    провайдера (base_url/api_key пустые → возьмутся из настроек в build_client).
    """
    raw = (settings.model_tiers or "").strip()
    if raw:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            data = {}
        fast, strong = data.get("fast") or {}, data.get("strong") or {}
        if isinstance(fast, dict) and isinstance(strong, dict) and fast.get("model") and strong.get("model"):
            router = data.get("router") or {}
            if not (isinstance(router, dict) and router.get("model")):
                router = fast
            return {"fast": fast, "strong": strong, "router": router}

    fast_m = (settings.model_fast or "").strip()
    strong_m = (settings.model_strong or "").strip()
    if fast_m and strong_m:
        router_m = (settings.model_router or "").strip() or fast_m
        return {
            "fast": {"model": fast_m},
            "strong": {"model": strong_m},
            "router": {"model": router_m},
        }
    return None


def _build_kwargs(tier: dict[str, str]) -> dict[str, Any]:
    """Аргументы для build_client из тира (пустые base_url/api_key не передаём)."""
    kw: dict[str, Any] = {"model": tier.get("model")}
    if tier.get("base_url"):
        kw["base_url"] = tier["base_url"]
    if tier.get("api_key"):
        kw["api_key"] = tier["api_key"]
    return kw


async def choose_model(
    task: str,
    settings: Settings,
    build_client: Callable[..., Any],
    *,
    timeout: float = 20.0,
) -> tuple[dict[str, str] | None, str]:
    """Выбирает тир под сложность задачи.

    Возвращает (tier, note): tier — {model, base_url?, api_key?} выбранной модели
    (или None, если маршрутизация не настроена — вызывающий работает как обычно);
    note — короткое человекочитаемое пояснение (для лога/интерфейса).
    """
    tiers = resolve_tiers(settings)
    if not tiers:
        return None, ""
    fast, strong, judge = tiers["fast"], tiers["strong"], tiers["router"]
    prompt = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _USER.format(task=(task or "").strip()[:2000])},
    ]

    try:
        client = build_client(**_build_kwargs(judge))
        client.reasoning = "minimal"  # a service call: the least reasoning (it decides the cost and the wait)
    except Exception as exc:  # noqa: BLE001 — сбой судьи не должен ронять прогон
        logger.warning("Роутер: не удалось создать судью (%s) → сильная модель", exc)
        return strong, f"оценщик недоступен → сильная модель ({strong.get('model')})"

    try:
        turn = await asyncio.wait_for(client.complete(prompt, max_tokens=400), timeout=timeout)
        verdict = (turn.content or "").strip().upper()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Роутер: оценка не удалась (%s) → сильная модель", exc)
        return strong, f"оценщик не ответил → сильная модель ({strong.get('model')})"
    finally:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            pass

    if "SIMPLE" in verdict and "COMPLEX" not in verdict:
        return fast, f"простая задача → дешёвая модель ({fast.get('model')})"
    return strong, f"сложная задача → сильная модель ({strong.get('model')})"


_PLAN_SYSTEM = (
    "You are a task router for an autonomous AI agent that has two models: a "
    "cheap/fast one and a strong/expensive one. You split the user's request "
    "into ordered subtasks and pick a model tier for each so cheap work goes to "
    "the cheap model and hard work to the strong one."
)
_PLAN_USER = (
    "Break the request into an ordered list of 1..6 subtasks that can be done in "
    "sequence, and pick a tier for each:\n"
    '- "fast": simple, mechanical, low-risk — small edits, lookups, boilerplate, '
    "short answers, formatting.\n"
    '- "strong": real reasoning — architecture, tricky debugging, subtle '
    "correctness, hard analysis, design.\n"
    "If the request is really ONE coherent task, return a single subtask.\n"
    "Each subtask text is a short self-contained imperative instruction that "
    "keeps the user's original wording/scope; do NOT invent work.\n"
    'Reply with ONLY JSON: {{"subtasks":[{{"text":"...","tier":"fast|strong"}}]}}\n\n'
    "Request:\n{task}"
)


def _extract_json(text: str) -> dict:
    """Достаёт JSON-объект из ответа судьи (в т.ч. в ```json ...``` рамке)."""
    raw = (text or "").strip()
    if not raw:
        return {}
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(raw[start : end + 1])
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def normalize_plan(data: dict) -> list[dict[str, str]]:
    """Приводит ответ судьи к списку {text, tier}; чужие/битые записи отбрасывает."""
    subs = data.get("subtasks") if isinstance(data, dict) else None
    out: list[dict[str, str]] = []
    for item in subs or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        tier = "fast" if str(item.get("tier") or "").strip().lower() == "fast" else "strong"
        out.append({"text": text, "tier": tier})
    return out


async def plan_subtasks(
    task: str,
    settings: Settings,
    build_client: Callable[..., Any],
    *,
    timeout: float = 25.0,
) -> list[dict[str, str]]:
    """Разбивает запрос на упорядоченные подзадачи с тиром модели для каждой.

    Возвращает список {text, tier}: одна запись — цельная задача; несколько с
    РАЗНЫМИ тирами — повод раздать подзадачи обеим моделям (дешёвая делает
    простое, сильная — сложное). Пустой список — маршрутизация не настроена или
    судья не ответил (вызывающий работает как обычно / по одной модели).
    """
    tiers = resolve_tiers(settings)
    if not tiers:
        return []
    judge = tiers["router"]
    prompt = [
        {"role": "system", "content": _PLAN_SYSTEM},
        {"role": "user", "content": _PLAN_USER.format(task=(task or "").strip()[:4000])},
    ]
    try:
        client = build_client(**_build_kwargs(judge))
        client.reasoning = "minimal"  # a service call: the least reasoning (it decides the cost and the wait)
    except Exception as exc:  # noqa: BLE001 — сбой судьи не должен ронять прогон
        logger.warning("Роутер: не удалось создать планировщика (%s)", exc)
        return []
    try:
        turn = await asyncio.wait_for(client.complete(prompt, max_tokens=600), timeout=timeout)
        return normalize_plan(_extract_json(turn.content or ""))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Роутер: планирование не удалось (%s)", exc)
        return []
    finally:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            pass


def is_distributed_plan(plan: list[dict[str, str]]) -> bool:
    """Стоит ли раздавать по обеим моделям: >=2 подзадач с РАЗНЫМИ тирами."""
    return len(plan) >= 2 and len({s["tier"] for s in plan}) >= 2


def tier_candidates(tier: dict[str, Any] | None) -> list[dict[str, str]]:
    """Основная модель тира + запасные (`fallbacks`) — по порядку для FallbackLLM."""
    def norm(d: dict[str, Any]) -> dict[str, str]:
        return {"model": d.get("model", ""), "base_url": d.get("base_url", ""), "api_key": d.get("api_key", "")}

    tier = tier or {}
    out: list[dict[str, str]] = []
    if tier.get("model"):
        out.append(norm(tier))
    for fb in tier.get("fallbacks") or []:
        if isinstance(fb, dict) and fb.get("model"):
            out.append(norm(fb))
    return out
