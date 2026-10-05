"""Выполнение консольных команд."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from pydantic import BaseModel, Field

from core.errors import PathNotAllowed, PermissionDenied, ToolError
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


def resolve_command_cwd(raw: str, ctx: ToolContext) -> tuple[Path, bool]:
    """The folder a command runs in, and whether it lies outside the workspace sandbox.

    File tools stay inside the sandbox, but commands may run anywhere the user wants to work
    (another project, a system folder): that is what a terminal is for. The price is an
    approval for every such command, and no rollback there — `outside` drives both.
    """
    try:
        return resolve_path(raw, settings=ctx.settings, must_exist=True, must_be_dir=True), False
    except PathNotAllowed:
        text = (raw or ".").strip().strip('"').strip("'")
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = ctx.settings.workspace / candidate
        try:
            folder = candidate.resolve()
        except OSError as exc:
            raise ToolError(f"Bad folder '{raw}': {exc}") from exc
        if not folder.is_dir():
            raise ToolError(f"The folder '{folder}' does not exist.") from None
        return folder, True


def cwd_outside_workspace(raw: str, ctx: ToolContext) -> Path | None:
    try:
        folder, outside = resolve_command_cwd(raw, ctx)
    except ToolError:
        return None
    return folder if outside else None


class ExecuteCommandArgs(BaseModel):
    command: str = Field(description="A PowerShell (Windows) or sh (Linux/macOS) command")
    cwd: str = Field(
        default=".",
        description=(
            "Folder to run in: relative to the workspace, or an absolute path anywhere. Outside the "
            "workspace the user approves every command and there is no rollback."
        ),
    )
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
        cwd = (args.cwd or ".").strip()
        if cwd not in (".", "") and (Path(cwd).is_absolute() or ".." in cwd):
            return tr("appr.shell_outside", cmd=args.command, cwd=cwd)
        return tr("appr.shell", cmd=args.command)

    def auto_verdict(self, args: ExecuteCommandArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        """In "Auto" only commands that change nothing pass silently — and only inside the
        workspace: outside it every command is the user's call."""
        if cwd_outside_workspace(args.cwd, ctx) is not None:
            return "ask"
        return "allow" if is_read_only_command(args.command) else "ask"

    async def run(self, args: ExecuteCommandArgs, ctx: ToolContext) -> ToolResult:
        check_command(args.command)
        cwd, outside = resolve_command_cwd(args.cwd, ctx)
        timeout = args.timeout or ctx.settings.shell_timeout

        from core.secrets_store import load_env

        # Снимок «до» для отката: только у меняющих команд и если включены снимки.
        before_sha = None
        shadow = None
        # Rollback snapshots cover the workspace only; outside it the user approved without one.
        if ctx.checkpoints is not None and not outside and not is_read_only_command(args.command):
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

        parts = [f"Exit code: {result.returncode}"]
        if outside:
            parts.insert(0, f"(ran in {cwd}, outside the workspace — no rollback there)")
        if result.stdout.strip():
            parts.append(f"stdout:\n{result.stdout.strip()}")
        if result.stderr.strip():
            parts.append(f"stderr:\n{result.stderr.strip()}")
        if not result.stdout.strip() and not result.stderr.strip():
            parts.append("(no output)")

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
