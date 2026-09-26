"""Инструменты наблюдателя за dev-серверами.

Типичный цикл агента:
  1. start_dev_server(command="npm run dev", name="web") — поднять сервер;
  2. read_dev_server(name="web", wait_sec=3) — почитать логи, увидеть ошибку;
  3. edit_file(...) — починить причину;
  4. read_dev_server(name="web", wait_sec=3) — убедиться, что пересборка прошла;
  5. stop_dev_server(name="web") — по завершении работы.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from core.devserver import get_manager, scan_output
from core.devserver.detect import looks_ready
from core.devserver.manager import DevServerError
from core.i18n import tr
from core.security.paths import resolve_path
from core.tools.base import EmptyArgs, Tool, ToolContext, ToolResult
from core.tools.builtin.shell import check_command

#: Максимум, сколько инструмент подождёт свежих логов за один вызов.
_MAX_WAIT = 20.0


def _format_errors(lines: list[str]) -> str:
    """Готовит блок с найденными ошибками, если они есть."""
    errors = scan_output(lines)
    if not errors:
        return ""
    seen: set[str] = set()
    rows: list[str] = []
    for err in errors:
        if err.text in seen:
            continue
        seen.add(err.text)
        rows.append(f"  • [{err.source}] {err.text}")
    return "⚠️ Похоже на ошибки:\n" + "\n".join(rows[:15])


class StartDevServerArgs(BaseModel):
    command: str = Field(description="Команда запуска, например «npm run dev» или «uvicorn app:app --reload»")
    name: str = Field(description="Короткое имя сервера для последующих обращений, например «web»")
    cwd: str = Field(default=".", description="Рабочая папка относительно workspace")
    wait_sec: float = Field(default=3.0, description="Сколько секунд подождать первых логов (0–20)")


class StartDevServerTool(Tool):
    name = "start_dev_server"
    description = (
        "Запускает долгоживущий dev-сервер (npm run dev, uvicorn --reload, cargo watch и т.п.) "
        "в фоне и возвращает первые строки логов. В отличие от execute_command процесс не ждёт "
        "завершения — потом читайте его через read_dev_server. Не используйте для обычных команд."
    )
    Args = StartDevServerArgs
    category = "execute"
    dangerous = True
    timeout = None

    def approval_reason(self, args: StartDevServerArgs) -> str:  # type: ignore[override]
        return tr("appr.dev_start", name=args.name, cmd=args.command)

    async def run(self, args: StartDevServerArgs, ctx: ToolContext) -> ToolResult:
        check_command(args.command)
        cwd = resolve_path(args.cwd, settings=ctx.settings, must_exist=True, must_be_dir=True)
        try:
            from core.secrets_store import load_env

            server = get_manager().start(
                args.name, args.command, cwd, env=load_env(ctx.settings.workspace)
            )
        except DevServerError as exc:
            return ToolResult.fail(str(exc))

        wait = max(0.0, min(args.wait_sec, _MAX_WAIT))
        if wait:
            await asyncio.sleep(wait)

        # Первый лог сдвигает курсор чтения, чтобы read_dev_server отдавал уже
        # только новое.
        first = server.read_new()
        if not server.is_running():
            body = "\n".join(first) or "(без вывода)"
            return ToolResult.fail(
                f"Сервер «{args.name}» завершился сразу (код {server.exit_code()}).\n{body}"
            )

        parts = [f"Сервер «{args.name}» запущен (pid {server.proc.pid}). Первые логи:"]
        parts.append("\n".join(first) if first else "(логов пока нет)")
        errors = _format_errors(first)
        if errors:
            parts.append(errors)
        return ToolResult(content="\n\n".join(parts))


class ReadDevServerArgs(BaseModel):
    name: str = Field(description="Имя сервера, заданное при запуске")
    wait_sec: float = Field(default=0.0, description="Сколько секунд подождать новых логов, если их пока нет (0–20)")


class ReadDevServerTool(Tool):
    name = "read_dev_server"
    description = (
        "Возвращает НОВЫЕ строки логов dev-сервера с прошлого чтения и помечает похожие на "
        "ошибки сборки/рантайма. Используйте после правок, чтобы проверить, что пересборка "
        "(HMR/reload) прошла без ошибок. wait_sec даёт серверу время отреагировать."
    )
    Args = ReadDevServerArgs
    category = "read"
    timeout = None

    async def run(self, args: ReadDevServerArgs, ctx: ToolContext) -> ToolResult:
        try:
            server = get_manager().get(args.name)
        except DevServerError as exc:
            return ToolResult.fail(str(exc))

        wait = max(0.0, min(args.wait_sec, _MAX_WAIT))
        deadline = wait
        lines: list[str] = server.read_new()
        # Ждём появления логов короткими интервалами, но не дольше wait.
        while not lines and deadline > 0 and server.is_running():
            step = min(0.5, deadline)
            await asyncio.sleep(step)
            deadline -= step
            lines = server.read_new()

        header = f"Сервер «{args.name}»"
        if not server.is_running():
            header += f" ЗАВЕРШИЛСЯ (код {server.exit_code()})"

        if not lines:
            tail = server.tail(3)
            hint = "\nПоследние строки:\n" + "\n".join(tail) if tail else ""
            return ToolResult(content=f"{header}: новых логов нет.{hint}")

        parts = [f"{header}. Новые логи:", "\n".join(lines)]
        errors = _format_errors(lines)
        if errors:
            parts.append(errors)
        elif looks_ready(lines):
            parts.append("✅ Похоже, сервер успешно собрался/перезапустился.")
        return ToolResult(content="\n\n".join(parts))


class StopDevServerArgs(BaseModel):
    name: str = Field(description="Имя сервера для остановки")


class StopDevServerTool(Tool):
    name = "stop_dev_server"
    description = "Останавливает ранее запущенный dev-сервер по имени."
    Args = StopDevServerArgs
    category = "execute"
    dangerous = True
    timeout = None

    def approval_reason(self, args: StopDevServerArgs) -> str:  # type: ignore[override]
        return tr("appr.dev_stop", name=args.name)

    def auto_verdict(self, args: StopDevServerArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        # Остановка своего же процесса безопасна — не дёргаем пользователя.
        return "allow"

    async def run(self, args: StopDevServerArgs, ctx: ToolContext) -> ToolResult:
        stopped = get_manager().stop(args.name)
        if not stopped:
            return ToolResult.fail(f"Сервер «{args.name}» не найден или уже остановлен.")
        return ToolResult(content=f"Сервер «{args.name}» остановлен.")


class ListDevServersTool(Tool):
    name = "list_dev_servers"
    description = "Показывает запущенные dev-серверы: имя, команду, статус, аптайм."
    Args = EmptyArgs
    category = "read"

    async def run(self, args: EmptyArgs, ctx: ToolContext) -> ToolResult:
        servers = get_manager().all()
        if not servers:
            return ToolResult(content="Нет запущенных dev-серверов.")
        rows = []
        for s in servers:
            st = s.status()
            state = "работает" if st["running"] else f"завершён (код {st['exit_code']})"
            rows.append(
                f"• {st['name']}: {st['command']} — {state}, "
                f"аптайм {st['uptime_sec']} с, строк лога {st['log_lines']}"
            )
        return ToolResult(content="\n".join(rows))
