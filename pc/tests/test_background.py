"""Тесты фоновых команд общего назначения (run/read/stop_background)."""

from __future__ import annotations

import asyncio

import pytest

from core.devserver import get_manager
from core.tools.base import ToolContext
from core.tools.builtin.background_tools import (
    ReadBackgroundTool,
    RunBackgroundTool,
    StopBackgroundTool,
    WaitForTool,
    WatchBackgroundTool,
)


@pytest.fixture(autouse=True)
def _cleanup():
    """Гарантированно гасим все фоновые процессы после каждого теста."""
    yield
    get_manager().shutdown()


async def test_quick_command_finishes(ctx: ToolContext):
    res = await RunBackgroundTool().run(
        RunBackgroundTool.Args(command="echo background-hello", name="quick", wait_sec=2.0), ctx
    )
    assert "background-hello" in res.content
    # Быстрая команда успевает завершиться в пределах ожидания.
    assert "завершилась" in res.content


async def test_long_command_runs_then_stops(ctx: ToolContext):
    run = await RunBackgroundTool().run(
        RunBackgroundTool.Args(
            command='python -c "import time; time.sleep(30)"', name="sleeper", wait_sec=0.5
        ),
        ctx,
    )
    assert "запущена" in run.content

    read = await ReadBackgroundTool().run(
        ReadBackgroundTool.Args(name="sleeper", wait_sec=0.0), ctx
    )
    assert "выполняется" in read.content

    stop = await StopBackgroundTool().run(StopBackgroundTool.Args(name="sleeper"), ctx)
    assert "остановлена" in stop.content

    # Остановка снимает задачу с учёта — повторное чтение сообщает, что её нет.
    await asyncio.sleep(0.3)
    gone = await ReadBackgroundTool().run(ReadBackgroundTool.Args(name="sleeper"), ctx)
    assert not gone.ok


async def test_read_unknown_job(ctx: ToolContext):
    res = await ReadBackgroundTool().run(ReadBackgroundTool.Args(name="нет-такого"), ctx)
    assert not res.ok


async def test_auto_name_when_empty(ctx: ToolContext):
    res = await RunBackgroundTool().run(
        RunBackgroundTool.Args(command="echo hi", name="", wait_sec=1.0), ctx
    )
    assert "job-" in res.content


async def test_wait_for_timer(ctx: ToolContext):
    res = await WaitForTool().run(
        WaitForTool.Args(seconds=0.3, reason="проверить X"), ctx
    )
    assert "Прошло" in res.content
    assert "проверить X" in res.content


async def test_wait_for_needs_argument(ctx: ToolContext):
    res = await WaitForTool().run(WaitForTool.Args(), ctx)
    assert not res.ok


async def test_watch_timer_notifies_without_blocking(ctx: ToolContext):
    res = await WatchBackgroundTool().run(
        WatchBackgroundTool.Args(seconds=0.3, note="проверить сборку"), ctx
    )
    # Возврат управления мгновенный (не блокирует).
    assert "таймер" in res.content.lower()
    assert ctx.scratch.get("notifications", []) == []
    # После срабатывания уведомление ложится в очередь.
    await asyncio.sleep(0.6)
    notes = ctx.scratch.get("notifications", [])
    assert any("проверить сборку" in n for n in notes)


async def test_watch_background_completion_notifies(ctx: ToolContext):
    await RunBackgroundTool().run(
        RunBackgroundTool.Args(
            command='python -c "import time; time.sleep(0.5)"', name="job", wait_sec=0.1
        ),
        ctx,
    )
    res = await WatchBackgroundTool().run(
        WatchBackgroundTool.Args(background="job", note="глянуть результат", timeout=10), ctx
    )
    assert "слежу" in res.content.lower()
    await asyncio.sleep(1.5)
    notes = ctx.scratch.get("notifications", [])
    assert any("job" in n and "завершилась" in n for n in notes)


async def test_wait_for_background_completion(ctx: ToolContext):
    await RunBackgroundTool().run(
        RunBackgroundTool.Args(
            command='python -c "import time; time.sleep(1)"', name="build", wait_sec=0.1
        ),
        ctx,
    )
    res = await WaitForTool().run(
        WaitForTool.Args(background="build", reason="дождаться сборки", timeout=10), ctx
    )
    assert "завершилась" in res.content
    assert res.ok
