"""Оценка стоимости и бюджет токенов на задачу."""

from __future__ import annotations

import json

import pytest

from core.cost import estimate_cost, load_pricing

# --------------------------------------------------------------- стоимость


@pytest.fixture
def pricing_cache(settings):
    """Кладёт кэш каталога моделей с ценами (как его пишет ModelCatalog)."""
    cache = settings.app_dir / "storage" / "models.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps(
            {
                "fetched_at": 0,
                "models": [
                    {"id": "anthropic/claude-sonnet-4.5", "prompt_price": 3.0, "completion_price": 15.0},
                    {"id": "deepseek/deepseek-v4-flash", "prompt_price": 0.1875, "completion_price": 0.9375},
                    {"id": "openrouter/auto-beta", "prompt_price": -1, "completion_price": -1},
                ],
            }
        ),
        encoding="utf-8",
    )
    return cache


def test_cost_is_computed_from_pricing(settings, pricing_cache):
    pricing = load_pricing(settings)
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000, "total_tokens": 2_000_000}

    cost = estimate_cost(usage, "anthropic/claude-sonnet-4.5", pricing)
    assert cost.priced
    assert cost.usd == pytest.approx(3.0 + 15.0)  # по миллиону каждого
    assert cost.total_tokens == 2_000_000


def test_dated_snapshot_matches_base_price(settings, pricing_cache):
    """«...-flash-0731» — датированный снимок, цена берётся у базовой модели."""
    pricing = load_pricing(settings)
    cost = estimate_cost(
        {"prompt_tokens": 1_000_000, "completion_tokens": 0}, "deepseek/deepseek-v4-flash-0731", pricing
    )
    assert cost.priced
    assert cost.usd == pytest.approx(0.1875)


def test_unknown_model_reports_tokens_without_price(settings, pricing_cache):
    """Локальная модель без цены — честно показываем токены, не выдумываем сумму."""
    pricing = load_pricing(settings)
    cost = estimate_cost({"total_tokens": 500}, "ollama/llama-local", pricing)
    assert not cost.priced
    assert cost.usd == 0.0
    assert cost.total_tokens == 500


def test_auto_router_has_no_price(settings, pricing_cache):
    """У автороутера цена -1 (зависит от модели) — в карту цен не попадает."""
    pricing = load_pricing(settings)
    assert "openrouter/auto-beta" not in pricing


def test_missing_cache_means_no_pricing(settings):
    """Нет кэша каталога — карта цен пустая, стоимость неизвестна, без падений."""
    assert load_pricing(settings) == {}
    cost = estimate_cost({"total_tokens": 100}, "any/model", {})
    assert not cost.priced


def test_total_tokens_inferred_when_absent(settings, pricing_cache):
    pricing = load_pricing(settings)
    cost = estimate_cost({"prompt_tokens": 10, "completion_tokens": 5}, "unknown", pricing)
    assert cost.total_tokens == 15


# ----------------------------------------------------- бюджет в цикле агента


async def test_token_budget_stops_the_run(settings):
    """При достижении бюджета агент завершает задачу, не жгя токены дальше."""
    from core.agent.runner import AgentRunner
    from core.llm.base import AssistantTurn
    from core.tools import build_default_registry
    from tests.fakes import ScriptedLLM, tool_call

    settings.max_run_tokens = 100
    settings.approval_mode = "bypass"

    # Первый ответ вызывает инструмент и «съедает» 150 токенов — выше бюджета.
    llm = ScriptedLLM(
        [
            AssistantTurn(
                tool_calls=[tool_call("list_directory", path=".")],
                usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            ),
            AssistantTurn(content="Итог: бюджет исчерпан, вот что успел."),
        ]
    )
    runner = AgentRunner(llm=llm, registry=build_default_registry(), settings=settings)
    result = await runner.run("сделай что-нибудь долгое")

    assert result.ok
    # Ровно два обращения к модели: рабочий шаг + вынужденный финал.
    assert len(llm.calls) == 2
    assert "исчерпан" in result.text.lower() or "успел" in result.text.lower()


async def test_no_budget_means_no_early_stop(settings):
    """Без бюджета (0) ранней остановки по токенам нет."""
    from core.agent.runner import AgentRunner
    from core.llm.base import AssistantTurn
    from core.tools import build_default_registry
    from tests.fakes import ScriptedLLM

    settings.max_run_tokens = 0
    llm = ScriptedLLM(
        [AssistantTurn(content="сразу ответ", usage={"total_tokens": 999999})]
    )
    runner = AgentRunner(llm=llm, registry=build_default_registry(), settings=settings)
    result = await runner.run("вопрос")
    assert result.ok
    assert len(llm.calls) == 1
