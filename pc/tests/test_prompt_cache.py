"""Кэширование промпта (F1): порядок сборки и трансформация в клиенте."""

from __future__ import annotations

from core.agent.prompt import build_system_prompt
from core.llm.base import CACHE_BREAKPOINT
from core.llm.openai_client import OpenAICompatClient, _supports_cache
from core.tools import build_default_registry


def test_build_prompt_puts_stable_before_volatile(settings):
    registry = build_default_registry()
    prompt = build_system_prompt(settings=settings, registry=registry, memory="ПАМЯТЬ: любит Rust")
    assert CACHE_BREAKPOINT in prompt
    stable, volatile = prompt.split(CACHE_BREAKPOINT, 1)
    # Стабильное — правила и инструменты; изменчивое — дата и память.
    assert "<environment>" in stable and "<approach>" in stable
    assert "Current date" in volatile and "любит Rust" in volatile
    assert "Current date" not in stable  # дата не должна ломать кэш-префикс


def test_supports_cache_detection():
    assert _supports_cache("anthropic/claude-sonnet-4.5") is True
    assert _supports_cache("claude-opus-4-8") is True
    assert _supports_cache("google/gemini-3.7-flash") is False
    assert _supports_cache("openai/gpt-5") is False


def _client(settings, model):
    return OpenAICompatClient(settings=settings, model=model)


def test_prepare_messages_anthropic_sets_cache_control(settings):
    client = _client(settings, "anthropic/claude-sonnet-4.5")
    system = "СТАБИЛЬНЫЙ ПРЕФИКС" + CACHE_BREAKPOINT + "Текущая дата: 2026-09-05"
    out = client._prepare_messages([{"role": "system", "content": system}, {"role": "user", "content": "hi"}])

    blocks = out[0]["content"]
    assert isinstance(blocks, list)
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[0]["text"] == "СТАБИЛЬНЫЙ ПРЕФИКС"
    assert blocks[1]["text"] == "Текущая дата: 2026-09-05" and "cache_control" not in blocks[1]
    # Маркер не утёк в запрос, пользовательское сообщение не тронуто.
    assert CACHE_BREAKPOINT not in str(out)
    assert out[1] == {"role": "user", "content": "hi"}


def test_prepare_messages_non_anthropic_is_plain_string(settings):
    client = _client(settings, "google/gemini-3.7-flash")
    system = "СТАБИЛЬНЫЙ" + CACHE_BREAKPOINT + "хвост"
    out = client._prepare_messages([{"role": "system", "content": system}])
    assert out[0]["content"] == "СТАБИЛЬНЫЙ\n\nхвост"  # маркер убран, склеено
    assert CACHE_BREAKPOINT not in out[0]["content"]


def test_prepare_messages_without_marker_caches_whole(settings):
    client = _client(settings, "anthropic/claude-sonnet-4.5")
    out = client._prepare_messages([{"role": "system", "content": "весь промпт"}])
    blocks = out[0]["content"]
    assert blocks[0]["text"] == "весь промпт" and blocks[0]["cache_control"]["type"] == "ephemeral"


def test_prepare_messages_leaves_midhistory_system_note(settings):
    client = _client(settings, "anthropic/claude-sonnet-4.5")
    msgs = [
        {"role": "system", "content": "префикс" + CACHE_BREAKPOINT + "хвост"},
        {"role": "user", "content": "q"},
        {"role": "system", "content": "[заметка] уточнение"},
    ]
    out = client._prepare_messages(msgs)
    assert out[2] == {"role": "system", "content": "[заметка] уточнение"}  # не тронута
