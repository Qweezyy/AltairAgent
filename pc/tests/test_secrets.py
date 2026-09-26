"""Хранилище секретов и инструменты запроса/просмотра."""

from __future__ import annotations

import pytest

from core.secrets_store import (
    SecretError,
    delete_secret,
    list_secrets,
    load_env,
    mask,
    set_secret,
    validate_name,
)
from core.tools.base import ToolContext
from core.tools.builtin.secret_tools import ListSecretsTool, RequestSecretTool
from tests.fakes import EventCollector

# ------------------------------------------------------------------ хранилище


def test_validate_name():
    assert validate_name("OPENAI_API_KEY") == "OPENAI_API_KEY"
    with pytest.raises(SecretError):
        validate_name("2KEY")  # с цифры
    with pytest.raises(SecretError):
        validate_name("my key")  # пробел


def test_mask_hides_value():
    assert mask("sk-1234567890abcdef") == "sk-…ef"
    assert mask("short") == "*****"


def test_set_and_list(settings):
    ws = settings.workspace
    set_secret(ws, "API_KEY", "sk-secret-value-123456")
    infos = list_secrets(ws)
    assert [i.name for i in infos] == ["API_KEY"]
    # В списке — только маска, не полное значение.
    assert "sk-secret-value-123456" not in infos[0].masked


def test_set_updates_existing(settings):
    ws = settings.workspace
    set_secret(ws, "TOKEN", "first-value-xxx")
    set_secret(ws, "TOKEN", "second-value-yyy")
    env_text = (ws / ".env").read_text(encoding="utf-8")
    assert env_text.count("TOKEN=") == 1  # не задвоилось
    assert load_env(ws)["TOKEN"] == "second-value-yyy"


def test_quoting_roundtrip(settings):
    ws = settings.workspace
    set_secret(ws, "PASS", 'has space "and" quote')
    assert load_env(ws)["PASS"] == 'has space "and" quote'


def test_delete(settings):
    ws = settings.workspace
    set_secret(ws, "A", "value-a-123")
    set_secret(ws, "B", "value-b-123")
    assert delete_secret(ws, "A") is True
    assert [i.name for i in list_secrets(ws)] == ["B"]
    assert delete_secret(ws, "A") is False


def test_env_added_to_gitignore(settings):
    ws = settings.workspace
    set_secret(ws, "K", "v-123456")
    gitignore = (ws / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore


def test_load_env_empty(settings):
    assert load_env(settings.workspace) == {}


def test_reject_empty_value(settings):
    with pytest.raises(SecretError):
        set_secret(settings.workspace, "K", "")


# ------------------------------------------------------------------ инструменты


@pytest.mark.asyncio
async def test_request_secret_emits_event_no_value(settings):
    events = EventCollector()
    ctx = ToolContext(settings=settings, emitter=events)
    result = await RequestSecretTool().run(
        RequestSecretTool.Args(name="OPENAI_API_KEY", purpose="для генерации"), ctx
    )
    assert result.ok
    # Событие для UI ушло.
    req = [e for e in events.events if getattr(e, "type", "") == "secret.requested"]
    assert req and req[0].name == "OPENAI_API_KEY"
    # В ответе агенту — имя, но никакого значения.
    assert "OPENAI_API_KEY" in result.content
    assert "os.environ" in result.content.lower() or "переменную окружения" in result.content


@pytest.mark.asyncio
async def test_request_secret_bad_name(settings):
    result = await RequestSecretTool().run(
        RequestSecretTool.Args(name="bad name!"), ToolContext(settings=settings)
    )
    assert not result.ok


@pytest.mark.asyncio
async def test_list_secrets_tool(settings):
    set_secret(settings.workspace, "MY_KEY", "value-123456")
    result = await ListSecretsTool().run(ListSecretsTool.Args(), ToolContext(settings=settings))
    assert "MY_KEY" in result.content
    assert "value-123456" not in result.content  # значение скрыто
