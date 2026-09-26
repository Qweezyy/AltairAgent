"""Инструменты git: как разработчик смотрит статус, диффы, историю и коммитит.

Читающие (status/diff/log/blame) безопасны и идут без подтверждения. Пишущие
(commit/branch) меняют репозиторий — спрашивают пользователя. Пуш сознательно НЕ
включён: отправка во внешний репозиторий — действие, которое выполняет сам
пользователь.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.git import GitUnavailable, is_repo, run_git
from core.git.repo import current_branch
from core.i18n import tr
from core.tools.base import EmptyArgs, Tool, ToolContext, ToolResult


async def _ensure_repo(ctx: ToolContext) -> str | None:
    """Возвращает текст-ошибку, если это не git-репозиторий, иначе None."""
    try:
        if await is_repo(ctx.settings.workspace):
            return None
    except GitUnavailable as exc:
        return str(exc)
    return (
        "Рабочая папка — не git-репозиторий. Чтобы начать отслеживать историю, "
        "выполните `git init` (через execute_command или попросите пользователя)."
    )


class GitStatusTool(Tool):
    name = "git_status"
    description = (
        "Показывает состояние git-репозитория: текущая ветка и какие файлы изменены, "
        "добавлены в индекс или не отслеживаются. Вызывай, чтобы понять, что уже сделано."
    )
    Args = EmptyArgs
    category = "read"

    async def run(self, args: EmptyArgs, ctx: ToolContext) -> ToolResult:
        err = await _ensure_repo(ctx)
        if err:
            return ToolResult.fail(err)
        ws = ctx.settings.workspace
        branch = await current_branch(ws)
        result = await run_git(["status", "--porcelain=v1", "--branch"], ws)
        if not result.ok:
            return ToolResult.fail(result.stderr.strip() or "git status не выполнился.")
        lines = result.stdout.splitlines()
        changes = [ln for ln in lines if not ln.startswith("##")]
        if not changes:
            return ToolResult(content=f"Ветка {branch}. Рабочее дерево чистое — изменений нет.")
        head = f"Ветка {branch}. Изменений: {len(changes)}"
        return ToolResult(content=f"{head}\n" + "\n".join(f"  {c}" for c in changes[:200]))


class GitDiffArgs(BaseModel):
    path: str = Field(default="", description="Limit the diff to a file or folder (empty: all changes)")
    staged: bool = Field(default=False, description="Show staged changes (git diff --cached)")
    against: str = Field(default="", description="Compare against a revision (e.g. HEAD~1 or a commit hash)")


class GitDiffTool(Tool):
    name = "git_diff"
    description = (
        "Shows code changes as a unified diff: the working tree, the index (staged=true) or against "
        "a revision. Use it to review your own edits before reporting or committing."
    )
    Args = GitDiffArgs
    category = "read"
    timeout = 30.0

    async def run(self, args: GitDiffArgs, ctx: ToolContext) -> ToolResult:
        err = await _ensure_repo(ctx)
        if err:
            return ToolResult.fail(err)
        ws = ctx.settings.workspace

        git_args = ["diff"]
        if args.against.strip():
            git_args.append(args.against.strip())
        elif args.staged:
            git_args.append("--cached")
        if args.path.strip():
            git_args += ["--", args.path.strip()]

        # Сначала краткая сводка (stat), потом сам дифф — так модель видит масштаб.
        stat = await run_git([*git_args[:1], "--stat", *git_args[1:]], ws)
        diff = await run_git(git_args, ws)
        if not diff.ok:
            return ToolResult.fail(diff.stderr.strip() or "git diff не выполнился.")
        if not diff.stdout.strip():
            return ToolResult(content="Изменений нет (дифф пуст).")
        summary = stat.stdout.strip()
        return ToolResult(content=(f"{summary}\n\n" if summary else "") + diff.stdout)


class GitLogArgs(BaseModel):
    count: int = Field(default=15, ge=1, le=100, description="Сколько последних коммитов показать")
    path: str = Field(default="", description="История конкретного файла/папки (пусто — весь репозиторий)")


class GitLogTool(Tool):
    name = "git_log"
    description = (
        "Показывает историю коммитов (хэш, дата, автор, сообщение). Можно по конкретному файлу — "
        "чтобы понять, как и зачем код менялся. Вызывай, чтобы разобраться в истории проекта."
    )
    Args = GitLogArgs
    category = "read"

    async def run(self, args: GitLogArgs, ctx: ToolContext) -> ToolResult:
        err = await _ensure_repo(ctx)
        if err:
            return ToolResult.fail(err)
        git_args = [
            "log",
            f"-n{args.count}",
            "--pretty=format:%h  %ad  %an  %s",
            "--date=short",
        ]
        if args.path.strip():
            git_args += ["--", args.path.strip()]
        result = await run_git(git_args, ctx.settings.workspace)
        if not result.ok:
            return ToolResult.fail(result.stderr.strip() or "git log не выполнился.")
        if not result.stdout.strip():
            return ToolResult(content="Коммитов пока нет.")
        return ToolResult(content=result.stdout)


class GitBlameArgs(BaseModel):
    path: str = Field(description="Файл, для которого нужна авторство/история строк")
    start_line: int = Field(ge=1, description="Первая строка диапазона")
    end_line: int = Field(ge=1, description="Последняя строка диапазона")


class GitBlameTool(Tool):
    name = "git_blame"
    description = (
        "Показывает, в каком коммите и кем последний раз изменена каждая строка файла в заданном "
        "диапазоне. Незаменимо, чтобы понять, ПОЧЕМУ код такой: находит коммит, где он появился."
    )
    Args = GitBlameArgs
    category = "read"

    async def run(self, args: GitBlameArgs, ctx: ToolContext) -> ToolResult:
        err = await _ensure_repo(ctx)
        if err:
            return ToolResult.fail(err)
        start, end = min(args.start_line, args.end_line), max(args.start_line, args.end_line)
        result = await run_git(
            ["blame", "-L", f"{start},{end}", "--date=short", "-w", "--", args.path.strip()],
            ctx.settings.workspace,
        )
        if not result.ok:
            return ToolResult.fail(result.stderr.strip() or "git blame не выполнился.")
        return ToolResult(content=result.stdout or "(пусто)")


class GitCommitArgs(BaseModel):
    message: str = Field(description="Сообщение коммита: коротко и по сути, что и зачем")
    paths: list[str] = Field(
        default_factory=list,
        description="Какие файлы закоммитить. Пусто — все отслеживаемые изменения",
    )


class GitCommitTool(Tool):
    name = "git_commit"
    description = (
        "Создаёт коммит: индексирует изменения и фиксирует их с сообщением. По умолчанию берёт все "
        "отслеживаемые изменения; можно указать конкретные файлы. Делай осмысленные атомарные коммиты "
        "с понятным сообщением. Пуш не выполняется — отправку делает пользователь."
    )
    Args = GitCommitArgs
    category = "execute"
    dangerous = True
    timeout = 30.0

    def approval_reason(self, args: GitCommitArgs) -> str:  # type: ignore[override]
        scope = ", ".join(args.paths) if args.paths else tr("appr.all_changes")
        return tr("appr.git_commit", scope=scope, msg=args.message)

    async def run(self, args: GitCommitArgs, ctx: ToolContext) -> ToolResult:
        err = await _ensure_repo(ctx)
        if err:
            return ToolResult.fail(err)
        ws = ctx.settings.workspace
        message = args.message.strip()
        if not message:
            return ToolResult.fail("Пустое сообщение коммита.")

        # Индексируем: конкретные файлы или все отслеживаемые изменения.
        if args.paths:
            add = await run_git(["add", "--", *[p.strip() for p in args.paths]], ws)
        else:
            add = await run_git(["add", "-u"], ws)
        if not add.ok:
            return ToolResult.fail(add.stderr.strip() or "git add не выполнился.")

        staged = await run_git(["diff", "--cached", "--name-only"], ws)
        if not staged.stdout.strip():
            return ToolResult.fail("Нечего коммитить: в индексе нет изменений.")

        commit = await run_git(["commit", "-m", message], ws)
        if not commit.ok:
            return ToolResult.fail(commit.stderr.strip() or commit.stdout.strip() or "git commit не выполнился.")
        return ToolResult(content=commit.stdout.strip() or "Коммит создан.")


class GitRestoreArgs(BaseModel):
    paths: list[str] = Field(
        description="Файлы, которым вернуть состояние. Обязательно — сплошной откат всего опасен"
    )
    source: str = Field(
        default="", description="Из какой ревизии восстановить (пусто — отменить незакоммиченные правки)"
    )


class GitRestoreTool(Tool):
    name = "git_restore"
    description = (
        "Возвращает указанным файлам состояние из git: без source — отменяет незакоммиченные "
        "правки (аккуратный откат отдельного файла), с source — восстанавливает версию из ревизии. "
        "ВНИМАНИЕ: несохранённые изменения в этих файлах теряются. Всегда перечисляй конкретные файлы."
    )
    Args = GitRestoreArgs
    category = "execute"
    dangerous = True
    timeout = 20.0

    def approval_reason(self, args: GitRestoreArgs) -> str:  # type: ignore[override]
        src = tr("appr.restore_from", src=args.source) if args.source.strip() else tr("appr.restore_undo")
        return tr("appr.git_restore", src=src, paths=", ".join(args.paths))

    async def run(self, args: GitRestoreArgs, ctx: ToolContext) -> ToolResult:
        err = await _ensure_repo(ctx)
        if err:
            return ToolResult.fail(err)
        paths = [p.strip() for p in args.paths if p.strip()]
        if not paths:
            return ToolResult.fail("Не указаны файлы для восстановления.")

        git_args = ["restore"]
        if args.source.strip():
            git_args += ["--source", args.source.strip()]
        git_args += ["--", *paths]
        result = await run_git(git_args, ctx.settings.workspace)
        if not result.ok:
            return ToolResult.fail(result.stderr.strip() or "git restore не выполнился.")
        return ToolResult(content=f"Восстановлены файлы: {', '.join(paths)}.")


class GitBranchArgs(BaseModel):
    action: str = Field(default="list", description="list (список), create (создать), switch (переключиться)")
    name: str = Field(default="", description="Имя ветки для create/switch")


class GitBranchTool(Tool):
    name = "git_branch"
    description = (
        "Работа с ветками: list — показать ветки, create — создать и переключиться на новую, "
        "switch — переключиться на существующую. Заводи отдельную ветку под крупную задачу, "
        "чтобы не смешивать её с чужой работой."
    )
    Args = GitBranchArgs
    category = "execute"
    dangerous = True
    timeout = 20.0

    def approval_reason(self, args: GitBranchArgs) -> str:  # type: ignore[override]
        return tr("appr.git_branch", action=args.action, name=args.name).strip()

    def auto_verdict(self, args: GitBranchArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        # Просмотр веток безопасен; создание/переключение — меняют состояние.
        return "allow" if args.action.strip().lower() == "list" else "ask"

    async def run(self, args: GitBranchArgs, ctx: ToolContext) -> ToolResult:
        err = await _ensure_repo(ctx)
        if err:
            return ToolResult.fail(err)
        ws = ctx.settings.workspace
        action = args.action.strip().lower()
        name = args.name.strip()

        if action == "list":
            result = await run_git(["branch", "--all"], ws)
            return ToolResult(content=result.stdout.strip() or "Веток нет.")
        if action == "create":
            if not name:
                return ToolResult.fail("Не указано имя ветки.")
            result = await run_git(["checkout", "-b", name], ws)
        elif action == "switch":
            if not name:
                return ToolResult.fail("Не указано имя ветки.")
            result = await run_git(["checkout", name], ws)
        else:
            return ToolResult.fail("action должен быть: list, create или switch.")

        if not result.ok:
            return ToolResult.fail(result.stderr.strip() or "git не выполнился.")
        return ToolResult(content=result.stdout.strip() or result.stderr.strip() or "Готово.")
