"""Фоновые команды общего назначения (аналог run_in_background / BashOutput / KillShell).

В отличие от `execute_command` (ждёт завершения) и `start_dev_server` (заточен под
dev-серверы), эти инструменты запускают ЛЮБУЮ долгую команду в фоне и позволяют
дочитывать её вывод по мере появления: сборка, длинный тест-ран, watcher, бэкап.

Переиспользуют тот же процессо-глобальный менеджер, что и dev-серверы
(`core/devserver/manager.py`): Popen со слитыми stdout/stderr, фоновый читатель в
кольцевой буфер, инкрементальное чтение и корректная остановка дерева процессов
(на Windows — через taskkill /T). Менеджер гасится в lifespan приложения.
"""

from __future__ import annotations

import asyncio
import itertools
import threading
from pathlib import Path

from pydantic import BaseModel, Field

from core.devserver import get_manager
from core.devserver.manager import DevServerError
from core.events import LogEvent
from core.i18n import tr
from core.tools.base import Tool, ToolContext, ToolResult
from core.tools.builtin.shell import (
    check_command,
    cwd_outside_workspace,
    is_read_only_command,
    resolve_command_cwd,
)

#: Максимум, сколько инструмент подождёт свежего вывода за один вызов.
_MAX_WAIT = 20.0

#: Потолок ожидания для wait_for (30 минут) — страховка от вечного зависания задачи.
_WAIT_CAP = 1800.0

_counter = itertools.count(1)
_counter_lock = threading.Lock()


def _auto_name() -> str:
    with _counter_lock:
        return f"job-{next(_counter)}"


class RunBackgroundArgs(BaseModel):
    command: str = Field(description="Любая неинтерактивная команда: сборка, тесты, watcher и т.п.")
    name: str = Field(default="", description="Короткое имя для последующих обращений (пусто — назначу сам)")
    cwd: str = Field(
        default=".",
        description="Folder to run in: relative to the workspace, or an absolute path (outside it needs approval)",
    )
    wait_sec: float = Field(default=1.0, description="Сколько секунд подождать первого вывода (0–20)")


class RunBackgroundTool(Tool):
    name = "run_background"
    description = (
        "Запускает команду в ФОНЕ и сразу возвращает управление, не дожидаясь завершения. "
        "Для долгих задач (сборка, длинный тест-ран, watcher, бэкап), за которыми нужно "
        "наблюдать. Потом читай вывод через read_background и останавливай через stop_background. "
        "Для быстрых команд используй execute_command, для dev-серверов — start_dev_server."
    )
    Args = RunBackgroundArgs
    category = "execute"
    dangerous = True
    timeout = None

    def approval_reason(self, args: RunBackgroundArgs) -> str:  # type: ignore[override]
        text = tr("appr.bg_run", name=args.name or tr("appr.auto"), cmd=args.command)
        cwd = (args.cwd or ".").strip()
        if cwd not in (".", "") and (Path(cwd).is_absolute() or ".." in cwd):
            text += tr("appr.in_folder", cwd=cwd)
        return text

    def auto_verdict(self, args: RunBackgroundArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        # Same rule as execute_command: read-only runs silently, but never outside the workspace.
        if cwd_outside_workspace(args.cwd, ctx) is not None:
            return "ask"
        return "allow" if is_read_only_command(args.command) else "ask"

    async def run(self, args: RunBackgroundArgs, ctx: ToolContext) -> ToolResult:
        check_command(args.command)
        cwd, _ = resolve_command_cwd(args.cwd, ctx)
        name = args.name.strip() or _auto_name()

        from core.secrets_store import load_env

        try:
            job = get_manager().start(
                name, args.command, cwd, env=load_env(ctx.settings.workspace)
            )
        except DevServerError as exc:
            return ToolResult.fail(str(exc))

        wait = max(0.0, min(args.wait_sec, _MAX_WAIT))
        if wait:
            await asyncio.sleep(wait)

        first = job.read_new()
        if not job.is_running():
            body = "\n".join(first) or "(без вывода)"
            ok = job.exit_code() == 0
            verdict = "успешно" if ok else f"с кодом {job.exit_code()}"
            return ToolResult(
                content=(
                    f"Фоновая команда «{name}» уже завершилась {verdict}.\n{body}"
                ),
                ok=ok,
            )

        parts = [
            f"Фоновая команда «{name}» запущена (pid {job.proc.pid}). "
            f"Читай вывод: read_background name=\"{name}\".",
        ]
        parts.append("\n".join(first) if first else "(вывода пока нет)")
        return ToolResult(content="\n\n".join(parts))


class ReadBackgroundArgs(BaseModel):
    name: str = Field(description="Имя фоновой команды, заданное при запуске")
    wait_sec: float = Field(default=0.0, description="Сколько секунд подождать нового вывода (0–20)")


class ReadBackgroundTool(Tool):
    name = "read_background"
    description = (
        "Возвращает НОВЫЙ вывод фоновой команды с прошлого чтения и её статус "
        "(работает / завершилась с кодом). Вызывай, чтобы следить за ходом и поймать результат."
    )
    Args = ReadBackgroundArgs
    category = "read"
    timeout = None

    async def run(self, args: ReadBackgroundArgs, ctx: ToolContext) -> ToolResult:
        try:
            job = get_manager().get(args.name)
        except DevServerError as exc:
            return ToolResult.fail(str(exc))

        deadline = max(0.0, min(args.wait_sec, _MAX_WAIT))
        lines = job.read_new()
        while not lines and deadline > 0 and job.is_running():
            step = min(0.5, deadline)
            await asyncio.sleep(step)
            deadline -= step
            lines = job.read_new()

        header = f"Команда «{args.name}»"
        if not job.is_running():
            header += f" ЗАВЕРШИЛАСЬ (код {job.exit_code()})"
        else:
            header += " выполняется"

        if not lines:
            tail = job.tail(3)
            hint = "\nПоследние строки:\n" + "\n".join(tail) if tail else ""
            return ToolResult(content=f"{header}: нового вывода нет.{hint}")

        return ToolResult(content=f"{header}. Новый вывод:\n" + "\n".join(lines))


class WaitForArgs(BaseModel):
    seconds: float = Field(
        default=0.0, description="Подождать столько секунд (таймер). 0 — не по времени"
    )
    background: str = Field(
        default="", description="Имя фоновой задачи: ждать её завершения (вместо таймера)"
    )
    reason: str = Field(default="", description="Зачем ждём — покажется пользователю в ленте")
    timeout: float = Field(default=_WAIT_CAP, ge=1, le=_WAIT_CAP, description="Потолок ожидания, сек")


class WaitForTool(Tool):
    name = "wait_for"
    description = (
        "Пауза с последующим продолжением работы. Два режима: подождать N секунд (таймер, "
        "например «проверю через 2 минуты») ИЛИ дождаться завершения фоновой задачи "
        "(background=имя, например долгой сборки). Когда время вышло или задача закончилась — "
        "инструмент вернёт управление, и ты идёшь проверять результат. Пользователь видит, что идёт ожидание."
    )
    Args = WaitForArgs
    category = "read"
    timeout = None

    async def _note(self, ctx: ToolContext, text: str) -> None:
        try:
            await ctx.emitter(LogEvent(text=text, level="info"))
        except Exception:  # noqa: BLE001 - статус не должен ронять ожидание
            pass

    async def run(self, args: WaitForArgs, ctx: ToolContext) -> ToolResult:
        cap = max(1.0, min(args.timeout, _WAIT_CAP))
        tag = f" — {args.reason.strip()}" if args.reason.strip() else ""

        # Режим ожидания завершения фоновой задачи.
        if args.background.strip():
            name = args.background.strip()
            try:
                job = get_manager().get(name)
            except DevServerError as exc:
                return ToolResult.fail(str(exc))

            await self._note(ctx, f"⏳ Жду завершения задачи «{name}»{tag}…")
            waited = 0.0
            while job.is_running() and waited < cap:
                step = min(1.0, cap - waited)
                await asyncio.sleep(step)
                waited += step

            if job.is_running():
                tail = "\n".join(job.tail(5))
                return ToolResult(
                    content=(
                        f"Задача «{name}» всё ещё выполняется спустя {int(waited)} с "
                        f"(достигнут лимит ожидания). Продолжай ждать через wait_for или проверь "
                        f"read_background. Последние строки:\n{tail}"
                    )
                )
            code = job.exit_code()
            tail = "\n".join(job.tail(8))
            verdict = "успешно" if code == 0 else f"с кодом {code}"
            return ToolResult(
                content=(
                    f"✅ Задача «{name}» завершилась {verdict} за ~{int(waited)} с ожидания. "
                    f"Теперь проверь результат.\nПоследние строки вывода:\n{tail or '(пусто)'}"
                ),
                ok=code == 0,
            )

        # Режим таймера.
        delay = min(args.seconds, cap)
        if delay <= 0:
            return ToolResult.fail(
                "Укажи seconds (таймер) или background (ждать задачу). Оба пусты."
            )
        await self._note(ctx, f"⏳ Пауза {int(delay)} с{tag}…")
        await asyncio.sleep(delay)
        return ToolResult(
            content=(
                f"Прошло {int(delay)} с{tag}. Продолжаю — можно проверять то, ради чего ждали."
            )
        )


class WatchBackgroundArgs(BaseModel):
    seconds: float = Field(default=0.0, description="Уведомить через столько секунд (таймер). 0 — не по времени")
    background: str = Field(default="", description="Имя фоновой задачи: уведомить, когда она завершится")
    note: str = Field(default="", description="Текст напоминания — что проверить/сделать, когда сработает")
    timeout: float = Field(default=_WAIT_CAP, ge=1, le=_WAIT_CAP, description="Потолок наблюдения, сек")


class WatchBackgroundTool(Tool):
    name = "watch_background"
    description = (
        "НЕ блокирует работу: ставит фоновое напоминание и сразу возвращает управление, "
        "чтобы ты продолжал заниматься другим. Когда пройдёт время (seconds) ИЛИ завершится "
        "фоновая задача (background=имя), тебе придёт уведомление на границе следующего шага — "
        "оно не прервёт текущее действие, но ты его увидишь и сможешь заняться напоминанием. "
        "Отличие от wait_for: тот останавливает тебя и ждёт, а этот работает параллельно."
    )
    Args = WatchBackgroundArgs
    category = "read"
    timeout = None

    async def run(self, args: WatchBackgroundArgs, ctx: ToolContext) -> ToolResult:
        cap = max(1.0, min(args.timeout, _WAIT_CAP))
        note = args.note.strip()

        if args.background.strip():
            name = args.background.strip()
            try:
                get_manager().get(name)  # проверяем, что задача существует
            except DevServerError as exc:
                return ToolResult.fail(str(exc))

            async def _watch_job() -> None:
                waited = 0.0
                while waited < cap:
                    try:
                        job = get_manager().get(name)
                    except DevServerError:
                        break
                    if not job.is_running():
                        code = job.exit_code()
                        verdict = "успешно" if code == 0 else f"с кодом {code}"
                        msg = f"Фоновая задача «{name}» завершилась {verdict}."
                        if note:
                            msg += f" Напоминание: {note}"
                        ctx.scratch.setdefault("notifications", []).append(msg)
                        return
                    await asyncio.sleep(1.0)
                    waited += 1.0

            task = asyncio.create_task(_watch_job(), name=f"watch-{name}")
            ctx.scratch.setdefault("_watch_tasks", []).append(task)
            tail = f" Напомню: {note}." if note else ""
            return ToolResult(
                content=(
                    f"Слежу за задачей «{name}» в фоне — уведомлю, когда завершится, "
                    f"и продолжу работать дальше.{tail}"
                )
            )

        delay = min(args.seconds, cap)
        if delay <= 0:
            return ToolResult.fail("Укажи seconds (таймер) или background (следить за задачей).")

        async def _watch_timer() -> None:
            await asyncio.sleep(delay)
            msg = f"Прошло {int(delay)} с (фоновый таймер)."
            if note:
                msg += f" Напоминание: {note}"
            ctx.scratch.setdefault("notifications", []).append(msg)

        task = asyncio.create_task(_watch_timer(), name="watch-timer")
        ctx.scratch.setdefault("_watch_tasks", []).append(task)
        tail = f" Напомню: {note}." if note else ""
        return ToolResult(
            content=(
                f"Поставил фоновый таймер на {int(delay)} с — уведомлю по истечении, "
                f"работу не прерываю.{tail}"
            )
        )


class StopBackgroundArgs(BaseModel):
    name: str = Field(description="Имя фоновой команды для остановки")


class StopBackgroundTool(Tool):
    name = "stop_background"
    description = "Останавливает фоновую команду по имени (гасит всё дерево процессов)."
    Args = StopBackgroundArgs
    category = "execute"
    dangerous = True
    timeout = None

    def approval_reason(self, args: StopBackgroundArgs) -> str:  # type: ignore[override]
        return tr("appr.bg_stop", name=args.name)

    def auto_verdict(self, args: StopBackgroundArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        # Остановка своего же процесса безопасна.
        return "allow"

    async def run(self, args: StopBackgroundArgs, ctx: ToolContext) -> ToolResult:
        stopped = get_manager().stop(args.name)
        if not stopped:
            return ToolResult.fail(f"Фоновая команда «{args.name}» не найдена или уже остановлена.")
        return ToolResult(content=f"Фоновая команда «{args.name}» остановлена.")
