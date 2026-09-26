"""Тесты пер-папочной памяти (.agent/memory.md) и роутинга remember."""

from __future__ import annotations

from core.folder_memory import FolderMemory
from core.memory import MemoryStore
from core.tools.base import ToolContext
from core.tools.builtin.memory_tools import RememberTool


def test_folder_memory_append_read_dedup(settings):
    fm = FolderMemory(settings.workspace)
    assert fm.append("В проекте используется FastAPI", "project")
    assert "FastAPI" in fm.read()
    # Повторная та же заметка не дублируется.
    assert fm.append("В проекте используется FastAPI", "project") is False
    assert fm.read().count("FastAPI") == 1
    assert fm.append("   ", "fact") is False


def test_folder_memory_prompt_section(settings):
    fm = FolderMemory(settings.workspace)
    assert fm.prompt_section() == ""  # пусто → нет секции
    fm.append("Тесты гоняем через pytest -q", "project")
    section = fm.prompt_section()
    assert "<folder_memory>" in section
    assert "pytest" in section


async def test_remember_routes_project_to_folder(settings):
    store = MemoryStore(settings.data_dir)
    ctx = ToolContext(settings=settings, memory=store)
    res = await RememberTool().invoke(
        {"text": "Модуль оплаты живёт в billing/", "category": "project"}, ctx
    )
    assert res.ok
    assert "память папки" in res.content.lower()
    # Ушло в memory.md папки, НЕ в глобальный стор.
    assert "billing" in FolderMemory(settings.workspace).read()
    assert all("billing" not in f.text for f in store.all())


async def test_remember_routes_user_to_global(settings):
    store = MemoryStore(settings.data_dir)
    ctx = ToolContext(settings=settings, memory=store)
    res = await RememberTool().invoke(
        {"text": "Пользователя зовут Саша", "category": "user"}, ctx
    )
    assert res.ok
    assert any("Саша" in f.text for f in store.all())
    assert "Саша" not in FolderMemory(settings.workspace).read()


async def test_remember_scope_override_to_global(settings):
    store = MemoryStore(settings.data_dir)
    ctx = ToolContext(settings=settings, memory=store)
    await RememberTool().invoke(
        {"text": "Важный факт проекта", "category": "project", "scope": "global"}, ctx
    )
    assert any("Важный факт" in f.text for f in store.all())
