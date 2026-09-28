"""Тесты фоновых команд общего назначения (run/read/stop_background)."""

from __future__ import annotations

import asyncio
import time

import pytest

from core.devserver import get_manager
from core.reminders import LIVE_WAITS, ReminderStore
from core.tools.base import ToolContext
from core.tools.builtin.background_tools import (
    ReadBackgroundTool,
    RunBackgroundTool,
    StopBackgroundTool,
    WaitForTool,
    WatchBackgroundTool,
)
from server.reminders import job_state


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
    assert "finished" in res.content


async def test_long_command_runs_then_stops(ctx: ToolContext):
    run = await RunBackgroundTool().run(
        RunBackgroundTool.Args(
            command='python -c "import time; time.sleep(30)"', name="sleeper", wait_sec=0.5
        ),
        ctx,
    )
    assert "started" in run.content

    read = await ReadBackgroundTool().run(
        ReadBackgroundTool.Args(name="sleeper", wait_sec=0.0), ctx
    )
    assert "is running" in read.content

    stop = await StopBackgroundTool().run(StopBackgroundTool.Args(name="sleeper"), ctx)
    assert "stopped" in stop.content

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
    res = await WaitForTool().run(WaitForTool.Args(seconds=0.3, reason="check X"), ctx)
    assert "have passed" in res.content
    assert "check X" in res.content
    # The durable record of the wait is gone once the wait ended normally.
    assert ReminderStore(ctx.settings.data_dir).all() == []


async def test_wait_for_needs_argument(ctx: ToolContext):
    res = await WaitForTool().run(WaitForTool.Args(), ctx)
    assert not res.ok


async def test_wait_for_is_durable_across_an_app_close_but_not_a_user_stop(ctx: ToolContext):
    store = ReminderStore(ctx.settings.data_dir)

    async def cut(shutdown: bool):
        ctx.scratch["_shutdown"] = shutdown
        task = asyncio.create_task(WaitForTool().run(WaitForTool.Args(seconds=600, reason="the build"), ctx))
        for _ in range(100):
            if LIVE_WAITS:
                break
            await asyncio.sleep(0.02)
        assert len(store.active()) == 1
        # The scheduler must not end a wait that a live run is sleeping on.
        assert store.fire_due(time.time() + 10_000) == []
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not LIVE_WAITS

    await cut(shutdown=False)
    assert store.all() == []                      # the user stopped the task: the wait goes too
    await cut(shutdown=True)
    kept = store.active()
    assert len(kept) == 1 and kept[0].kind == "wait" and kept[0].note == "the build"
    # After the restart the scheduler ends it when its time comes and wakes the chat.
    fired = store.fire_due(time.time() + 601)
    assert [r.id for r in fired] == [kept[0].id] and "Your wait is over: the build" in fired[0].fired_text()


async def test_watch_timer_is_durable_and_does_not_block(ctx: ToolContext):
    res = await WatchBackgroundTool().run(WatchBackgroundTool.Args(seconds=0.3, note="check the build"), ctx)
    assert "timer" in res.content.lower()
    items = ReminderStore(ctx.settings.data_dir).active()
    assert len(items) == 1 and items[0].kind == "time" and items[0].note == "check the build"
    fired = ReminderStore(ctx.settings.data_dir).fire_due(time.time() + 1)
    assert [r.id for r in fired] == [items[0].id]


async def test_watch_background_completion_fires(ctx: ToolContext):
    await RunBackgroundTool().run(
        RunBackgroundTool.Args(command='python -c "import time; time.sleep(0.5); print(42)"', name="job", wait_sec=0.1),
        ctx,
    )
    res = await WatchBackgroundTool().run(WatchBackgroundTool.Args(background="job", note="look at the result"), ctx)
    assert "watching" in res.content.lower()
    store = ReminderStore(ctx.settings.data_dir)
    assert store.fire_due(time.time(), None, job_state) == []      # still running
    await asyncio.sleep(1.5)
    fired = store.fire_due(time.time(), None, job_state)
    assert len(fired) == 1
    text = fired[0].fired_text()
    assert "'job' finished successfully" in text and "42" in text and "look at the result" in text


async def test_run_background_notify_and_a_job_gone_after_restart(ctx: ToolContext):
    res = await RunBackgroundTool().run(
        RunBackgroundTool.Args(command='python -c "import time; time.sleep(30)"', name="slow", wait_sec=0.1, notify=True),
        ctx,
    )
    assert "woken" in res.content
    store = ReminderStore(ctx.settings.data_dir)
    watch = store.active()[0]
    assert watch.kind == "job" and watch.job == "slow" and watch.value
    # The same name under another pid (the app restarted and a new job took the name) is not it.
    assert job_state("slow", "1") == (True, job_state("missing-job", "")[1])
    get_manager().shutdown()
    fired = store.fire_due(time.time(), None, job_state)
    assert len(fired) == 1 and "is gone" in fired[0].fired_text()


async def test_stopping_a_job_drops_its_watch(ctx: ToolContext):
    await RunBackgroundTool().run(
        RunBackgroundTool.Args(command='python -c "import time; time.sleep(30)"', name="w", wait_sec=0.1, notify=True),
        ctx,
    )
    await StopBackgroundTool().run(StopBackgroundTool.Args(name="w"), ctx)
    assert ReminderStore(ctx.settings.data_dir).active() == []


async def test_wait_for_background_completion(ctx: ToolContext):
    await RunBackgroundTool().run(
        RunBackgroundTool.Args(command='python -c "import time; time.sleep(1)"', name="build", wait_sec=0.1),
        ctx,
    )
    res = await WaitForTool().run(WaitForTool.Args(background="build", reason="the build", timeout=10), ctx)
    assert "finished successfully" in res.content
    assert res.ok
    assert ReminderStore(ctx.settings.data_dir).all() == []
