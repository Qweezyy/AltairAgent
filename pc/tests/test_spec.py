"""Режим архитектора: письменный план-документ (Spec Mode)."""

from __future__ import annotations

import pytest

from core.tools.base import ToolContext
from core.tools.builtin.spec_tools import PlanFile, WritePlanArgs, WritePlanTool, _render
from tests.fakes import EventCollector


def _args(**over) -> WritePlanArgs:
    base = dict(
        title="Добавить экспорт",
        goal="Дать пользователю выгружать чат в PDF.",
        approach="Отдельный модуль export, конвертация через Chromium.",
        files=[PlanFile(path="core/export.py", change="новый модуль экспорта")],
        steps=["Написать модуль", "Добавить эндпоинт", "Проверить тестами"],
        verification="pytest + ручная проверка PDF",
        risks="Chromium может отсутствовать",
    )
    base.update(over)
    return WritePlanArgs(**base)


def test_render_has_all_sections():
    md = _render(_args())
    for header in ("# План:", "## Цель", "## Подход", "## Затрагиваемые файлы", "## Шаги", "## Проверка", "## Риски"):
        assert header in md
    assert "1. Написать модуль" in md
    assert "`core/export.py`" in md


def test_render_skips_empty_sections():
    md = _render(_args(approach="", risks="", files=[], verification=""))
    assert "## Подход" not in md
    assert "## Риски" not in md
    assert "## Затрагиваемые файлы" not in md
    assert "## Цель" in md  # цель обязательна


@pytest.mark.asyncio
async def test_write_plan_saves_and_emits(settings):
    events = EventCollector()
    ctx = ToolContext(settings=settings, emitter=events)
    tool = WritePlanTool()

    result = await tool.run(_args(), ctx)
    assert result.ok
    # Файл создан в рабочей папке.
    plan_file = settings.workspace / "implementation_plan.md"
    assert plan_file.exists()
    assert "## Шаги" in plan_file.read_text(encoding="utf-8")

    # Событие артефакта (markdown) для «Превью».
    art = [e for e in events.events if getattr(e, "type", "") == "artifact.created"]
    assert art and art[0].kind == "markdown"

    # Живой чек-лист наполнен шагами в статусе pending.
    plan_updates = [e for e in events.events if getattr(e, "type", "") == "plan.updated"]
    assert plan_updates
    steps = plan_updates[0].steps
    assert len(steps) == 3
    assert all(s.status == "pending" for s in steps)


@pytest.mark.asyncio
async def test_write_plan_adds_md_extension(settings):
    ctx = ToolContext(settings=settings, emitter=EventCollector())
    tool = WritePlanTool()
    await tool.run(_args(path="docs/my_plan"), ctx)
    assert (settings.workspace / "docs" / "my_plan.md").exists()


def test_auto_verdict_allows(settings):
    tool = WritePlanTool()
    assert tool.auto_verdict(_args(), ToolContext(settings=settings)) == "allow"
