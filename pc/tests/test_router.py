"""Тесты маршрутизации моделей по сложности (core/agent/router.py)."""

from __future__ import annotations

import json

from core.agent.router import (
    _extract_json,
    choose_model,
    is_distributed_plan,
    normalize_plan,
    plan_subtasks,
    resolve_tiers,
)
from core.llm.base import AssistantTurn, LLMClient


class _Judge(LLMClient):
    """Судья-двойник: отдаёт заданный вердикт (или падает)."""

    def __init__(self, verdict: str = "SIMPLE", *, boom: bool = False) -> None:
        self.model = "judge/model"
        self._verdict = verdict
        self._boom = boom
        self.closed = False

    async def complete(self, messages, **kwargs):  # type: ignore[override]
        if self._boom:
            raise RuntimeError("судья упал")
        return AssistantTurn(content=self._verdict)

    async def aclose(self) -> None:
        self.closed = True


def _factory(judge):
    return lambda **kw: judge


# ------------------------------------------------------------- resolve_tiers


def test_unconfigured_returns_none(settings):
    s = settings.model_copy(update={"model_fast": "", "model_strong": "big"})
    assert resolve_tiers(s) is None


def test_flat_fields_resolve(settings):
    s = settings.model_copy(update={"model_fast": "cheap", "model_strong": "big", "model_router": ""})
    tiers = resolve_tiers(s)
    assert tiers["fast"]["model"] == "cheap" and tiers["strong"]["model"] == "big"
    assert tiers["router"]["model"] == "cheap"  # оценщик пуст → дешёвая


def test_json_tiers_carry_providers(settings):
    cfg = {
        "fast": {"model": "gpt-mini", "base_url": "https://a/v1", "api_key": "ka"},
        "strong": {"model": "claude", "base_url": "https://b/v1", "api_key": "kb"},
    }
    s = settings.model_copy(update={"model_tiers": json.dumps(cfg)})
    tiers = resolve_tiers(s)
    assert tiers["strong"]["base_url"] == "https://b/v1"
    assert tiers["router"]["model"] == "gpt-mini"  # нет router → дешёвая


# ------------------------------------------------------------- choose_model


async def test_none_when_unconfigured(settings):
    tier, note = await choose_model("задача", settings, _factory(_Judge()))
    assert tier is None and note == ""


async def test_simple_picks_fast(settings):
    s = settings.model_copy(update={"model_fast": "cheap", "model_strong": "big"})
    judge = _Judge("SIMPLE")
    tier, note = await choose_model("привет", s, _factory(judge))
    assert tier["model"] == "cheap" and "дешёвая" in note and judge.closed


async def test_complex_picks_strong(settings):
    s = settings.model_copy(update={"model_fast": "cheap", "model_strong": "big"})
    tier, note = await choose_model("отрефактори модуль", s, _factory(_Judge("COMPLEX")))
    assert tier["model"] == "big" and "сильная" in note


async def test_judge_error_defaults_to_strong(settings):
    s = settings.model_copy(update={"model_fast": "cheap", "model_strong": "big"})
    tier, note = await choose_model("задача", s, _factory(_Judge(boom=True)))
    assert tier["model"] == "big" and "сильная" in note


async def test_cross_provider_passes_overrides(settings):
    """Судья создаётся с base_url/api_key своего тира (кросс-провайдер)."""
    cfg = {
        "fast": {"model": "gpt-mini", "base_url": "https://a/v1", "api_key": "ka"},
        "strong": {"model": "claude", "base_url": "https://b/v1", "api_key": "kb"},
        "router": {"model": "judge", "base_url": "https://c/v1", "api_key": "kc"},
    }
    s = settings.model_copy(update={"model_tiers": json.dumps(cfg)})
    seen = {}

    def factory(**kw):
        seen.update(kw)
        return _Judge("COMPLEX")

    tier, _ = await choose_model("сложное", s, factory)
    assert seen == {"model": "judge", "base_url": "https://c/v1", "api_key": "kc"}
    assert tier == cfg["strong"]  # выбрана сильная со своим провайдером


# ------------------------------------------------------- план подзадач (распределение)


def test_extract_json_tolerates_fences():
    assert _extract_json('```json\n{"subtasks":[]}\n```') == {"subtasks": []}
    assert _extract_json("бла-бла {\"a\":1} хвост") == {"a": 1}
    assert _extract_json("нет json") == {}


def test_normalize_plan_filters_and_defaults_tier():
    data = {"subtasks": [
        {"text": "  сделать A ", "tier": "fast"},
        {"text": "сделать B", "tier": "STRONG"},
        {"text": "", "tier": "fast"},          # пустой текст — выкинуть
        {"text": "C", "tier": "неведомо"},      # неизвестный тир → strong
        "мусор",                                # не dict — выкинуть
    ]}
    plan = normalize_plan(data)
    assert plan == [
        {"text": "сделать A", "tier": "fast"},
        {"text": "сделать B", "tier": "strong"},
        {"text": "C", "tier": "strong"},
    ]


def test_is_distributed_only_when_mixed_tiers():
    assert is_distributed_plan([{"text": "a", "tier": "fast"}, {"text": "b", "tier": "strong"}]) is True
    assert is_distributed_plan([{"text": "a", "tier": "fast"}, {"text": "b", "tier": "fast"}]) is False
    assert is_distributed_plan([{"text": "a", "tier": "strong"}]) is False
    assert is_distributed_plan([]) is False


async def test_plan_subtasks_parses_and_uses_judge_provider(settings):
    cfg = {
        "fast": {"model": "cheap"},
        "strong": {"model": "big"},
        "router": {"model": "judge", "base_url": "https://c/v1", "api_key": "kc"},
    }
    s = settings.model_copy(update={"model_tiers": json.dumps(cfg)})
    seen = {}

    class _Planner(_Judge):
        async def complete(self, messages, **kwargs):  # type: ignore[override]
            return AssistantTurn(content='{"subtasks":[{"text":"easy 1","tier":"fast"},'
                                          '{"text":"easy 2","tier":"fast"},'
                                          '{"text":"hard 3","tier":"strong"}]}')

    def factory(**kw):
        seen.update(kw)
        return _Planner()

    plan = await plan_subtasks("три задачи", s, factory)
    assert [x["tier"] for x in plan] == ["fast", "fast", "strong"]
    assert plan[2]["text"] == "hard 3"
    assert seen == {"model": "judge", "base_url": "https://c/v1", "api_key": "kc"}
    assert is_distributed_plan(plan) is True


async def test_plan_subtasks_empty_on_unconfigured(settings):
    s = settings.model_copy(update={"model_fast": "", "model_strong": ""})
    assert await plan_subtasks("что-то", s, _factory(_Judge())) == []


async def test_plan_subtasks_empty_on_bad_json(settings):
    s = settings.model_copy(update={"model_fast": "cheap", "model_strong": "big"})
    assert await plan_subtasks("что-то", s, _factory(_Judge("не json совсем"))) == []
