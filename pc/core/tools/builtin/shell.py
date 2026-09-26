"""Выполнение консольных команд."""

from __future__ import annotations

import asyncio
import re

from pydantic import BaseModel, Field

from core.errors import PermissionDenied
from core.events import CheckpointCreated
from core.i18n import tr
from core.security.paths import resolve_path
from core.security.risk import BLOCK_PATTERNS
from core.shadow_git import ShadowGit
from core.tools.base import Tool, ToolContext, ToolResult
from core.utils.proc import run_process, shell_argv

#: Сколько файлов, изменённых одной командой, максимум берём под откат — чтобы
#: `npm install` на тысячи файлов не забил хранилище снимков.
_MAX_COMMAND_CHECKPOINTS = 100

#: Каталог «запрещено всегда» переехал в core.security.risk (единый источник для
#: независимого верификатора и этого инструмента). Здесь — псевдоним для читаемости.
BLOCKED_PATTERNS = BLOCK_PATTERNS


#: Команды, которые только смотрят: их безопасно выполнять без вопроса.
READ_ONLY_COMMANDS = {
    "ls", "dir", "pwd", "cat", "type", "head", "tail", "find", "findstr", "grep",
    "tree", "echo", "where", "which", "wc", "stat", "du", "df", "whoami", "date",
}

#: Подкоманды git, которые ничего не меняют в репозитории.
READ_ONLY_GIT = {"status", "log", "diff", "show", "branch", "remote", "config", "blame"}


def is_read_only_command(command: str) -> bool:
    """Меняет ли команда состояние системы.

    Осознанно строгая проверка: всё с перенаправлением, склейкой команд или
    неизвестным именем считается изменяющим. Ошибиться в сторону лишнего
    вопроса безопаснее, чем молча выполнить установку пакета.
    """
    text = command.strip().lower()
    if not text or any(symbol in text for symbol in (">", ">>", "|", "&", ";", "`", "$(")):
        return False

    parts = text.split()
    head = parts[0].removesuffix(".exe")

    if head == "git":
        return len(parts) > 1 and parts[1] in READ_ONLY_GIT
    if head in ("python", "python3", "py") and len(parts) > 2:
        # Проверки читают код, но ничего не устанавливают.
        return parts[1] == "-m" and parts[2] in ("pytest", "ruff", "mypy", "unittest")
    if head in ("npm", "pnpm", "yarn") and len(parts) > 1:
        return parts[1] in ("test", "run", "ls", "list", "outdated")
    if head in ("get-childitem", "get-content", "get-location", "select-string"):
        return True
    return head in READ_ONLY_COMMANDS


def check_command(command: str) -> None:
    """Кидает PermissionDenied, если команда попадает в чёрный список."""
    lowered = command.lower()
    for pattern, why in BLOCKED_PATTERNS:
        if re.search(pattern, lowered):
            raise PermissionDenied(
                f"Команда заблокирована политикой безопасности ({why}). "
                "Если это действительно нужно — попроси пользователя выполнить вручную."
            )


class ExecuteCommandArgs(BaseModel):
    command: str = Field(description="A PowerShell (Windows) or sh (Linux/macOS) command")
    cwd: str = Field(default=".", description="Working folder relative to the workspace")
    timeout: float | None = Field(default=None, description="Timeout in seconds (default from settings)")


class ExecuteCommandTool(Tool):
    name = "execute_command"
    description = (
        "Runs a console command and returns the exit code, stdout and stderr. For git, package "
        "managers, scripts and builds that finish on their own; nothing interactive or long-running "
        "(use run_background for that). Prefer commands with concise output (quiet flags, filters)."
    )
    Args = ExecuteCommandArgs
    category = "execute"
    dangerous = True
    timeout = None  # управляем таймаутом сами, внутри run()

    def approval_reason(self, args: ExecuteCommandArgs) -> str:  # type: ignore[override]
        return tr("appr.shell", cmd=args.command)

    def auto_verdict(self, args: ExecuteCommandArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        """В режиме «Авто» пропускаем только команды, которые ничего не меняют."""
        return "allow" if is_read_only_command(args.command) else "ask"

    async def run(self, args: ExecuteCommandArgs, ctx: ToolContext) -> ToolResult:
        check_command(args.command)
        cwd = resolve_path(args.cwd, settings=ctx.settings, must_exist=True, must_be_dir=True)
        timeout = args.timeout or ctx.settings.shell_timeout

        from core.secrets_store import load_env

        # Снимок «до» для отката: только у меняющих команд и если включены снимки.
        before_sha = None
        shadow = None
        if ctx.checkpoints is not None and not is_read_only_command(args.command):
            shadow, before_sha = await _shadow_snapshot(ctx)

        result = await run_process(
            shell_argv(args.command),
            cwd=cwd,
            timeout=timeout,
            # Секреты из .env доступны команде; субагенту (F8) — нет.
            env={} if ctx.scratch.get("no_secrets") else load_env(ctx.settings.workspace),
        )

        if shadow is not None and before_sha is not None:
            await _register_command_checkpoints(ctx, shadow, before_sha)

        parts = [f"Код возврата: {result.returncode}"]
        if result.stdout.strip():
            parts.append(f"stdout:\n{result.stdout.strip()}")
        if result.stderr.strip():
            parts.append(f"stderr:\n{result.stderr.strip()}")
        if not result.stdout.strip() and not result.stderr.strip():
            parts.append("(вывод пуст)")

        return ToolResult(content="\n\n".join(parts), ok=result.ok)


async def _shadow_snapshot(ctx: ToolContext) -> tuple[ShadowGit | None, str | None]:
    """Готовит теневой снимок «до команды». Любая ошибка — тихо без отката."""
    shadow = ShadowGit(ctx.run_id, ctx.settings.workspace, ctx.settings.data_dir)
    if not await asyncio.to_thread(shadow.available):
        return None, None
    sha = await asyncio.to_thread(shadow.snapshot)
    return (shadow, sha) if sha else (None, None)


async def _register_command_checkpoints(ctx: ToolContext, shadow: ShadowGit, before_sha: str) -> None:
    """Сравнивает состояние после команды со снимком «до» и регистрирует откат
    ровно для затронутых файлов (чужие файлы не трогаем)."""
    after_sha = await asyncio.to_thread(shadow.snapshot)
    if not after_sha:
        return
    changed = await asyncio.to_thread(shadow.changed_since, before_sha)
    for status, rel_path in changed[:_MAX_COMMAND_CHECKPOINTS]:
        existed = status in ("M", "D")  # A — файл создан командой (откат = удалить)
        prior = await asyncio.to_thread(shadow.show, before_sha, rel_path) if existed else None
        checkpoint = await asyncio.to_thread(
            ctx.checkpoints.record_prior, rel_path, "command", prior, existed
        )
        await ctx.emitter(
            CheckpointCreated(
                id=checkpoint.id,
                path=checkpoint.path,
                op=checkpoint.op,
                recoverable=checkpoint.recoverable,
            )
        )
