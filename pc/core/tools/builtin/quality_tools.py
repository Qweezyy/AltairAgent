"""Инструменты проверки кода: тесты и линтеры.

Смысл цикла `правка -> проверка -> исправление`: агент не должен отчитываться
«готово» по ощущениям. Он запускает то, что реально проверяет код, получает
сжатый список падений и чинит их сам, пока проверка не станет зелёной.

Почему не просто execute_command: здесь есть определение команды под проект
(pytest/npm/go/cargo), разбор вывода и его сжатие. Полный лог pytest на
большом проекте забивает контекст, а модели нужны 20 строк из него.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from core.errors import ToolError
from core.i18n import tr
from core.quality import (
    Command,
    build_report,
    describe_project,
    detect_lint_commands,
    detect_test_commands,
)
from core.security.paths import resolve_path
from core.tools.base import Tool, ToolContext, ToolResult
from core.utils.proc import run_process


async def _execute(command: Command, cwd: Path, timeout: float) -> ToolResult:
    result = await run_process(command.argv, cwd=cwd, timeout=timeout)
    report = build_report(command.tool, result.stdout, result.stderr, result.returncode, result.timed_out)

    body = report.render()
    if not report.ok:
        body += (
            "\n\nЧто делать: исправь причину и запусти проверку снова. "
            "Не сообщай пользователю об успехе, пока проверка не пройдёт."
        )
    return ToolResult(content=f"Команда: {command.display}\n\n{body}", ok=report.ok)


def _missing_tool_hint(command: Command, output: str) -> str | None:
    """Отличает «инструмент не установлен» от настоящих ошибок проверки."""
    markers = ("No module named", "не является внутренней", "not found", "не найден", "ENOENT")
    if any(marker in output for marker in markers):
        return (
            f"Похоже, {command.tool} не установлен. Установи его "
            f"(например, pip install {command.tool}) или запусти другую проверку."
        )
    return None


class RunTestsArgs(BaseModel):
    path: str = Field(default=".", description="Project folder or a specific test file/folder")
    command: str = Field(
        default="",
        description="Custom command (e.g. 'pytest tests/test_api.py -k login'); empty = detect automatically",
    )
    timeout: float = Field(default=300.0, ge=5, le=1800, description="Time limit in seconds")


class RunTestsTool(Tool):
    name = "run_tests"
    description = (
        "Runs the project's tests (pytest, npm test, go test, cargo test — detected automatically) "
        "and returns a compact report of what failed and why. Run it after code changes; target a "
        "file or -k filter while iterating, the full suite before finishing."
    )
    Args = RunTestsArgs
    category = "execute"
    dangerous = True  # тесты выполняют произвольный код проекта
    timeout = None

    def approval_reason(self, args: RunTestsArgs) -> str:  # type: ignore[override]
        return tr("appr.tests", path=args.path, cmd=args.command or tr("appr.tests_auto"))

    def auto_verdict(self, args, ctx) -> str:  # type: ignore[override]
        """Тесты — это проверка, ради которой агента и просили работать."""
        return "allow"

    async def run(self, args: RunTestsArgs, ctx: ToolContext) -> ToolResult:
        base = resolve_path(args.path, settings=ctx.settings, must_exist=True)
        cwd = base if base.is_dir() else base.parent

        if args.command.strip():
            from core.utils.proc import shell_argv

            command = Command(
                kind="tests",
                tool="pytest" if "pytest" in args.command else "custom",
                argv=shell_argv(args.command.strip()),
                description=args.command.strip(),
            )
            return await _execute(command, cwd, args.timeout)

        commands = detect_test_commands(cwd)
        if not commands:
            raise ToolError(
                f"Не удалось определить, чем запускать тесты в '{args.path}' "
                f"({describe_project(cwd)}). Укажи команду явно через аргумент command."
            )

        command = commands[0]
        if not base.is_dir():
            command = Command(
                kind=command.kind,
                tool=command.tool,
                argv=[*command.argv, str(base)],
                description=f"{command.description} для {base.name}",
            )

        result = await _execute(command, cwd, args.timeout)
        if not result.ok:
            hint = _missing_tool_hint(command, result.content)
            if hint:
                return ToolResult(content=f"{result.content}\n\n{hint}", ok=False)
        return result


class RunLintArgs(BaseModel):
    path: str = Field(default=".", description="Папка проекта")
    fix: bool = Field(default=False, description="Просить линтер исправить, что умеет сам (ruff --fix)")
    timeout: float = Field(default=180.0, ge=5, le=900)


class RunLintTool(Tool):
    name = "run_lint"
    description = (
        "Запускает линтеры и проверку типов (ruff, eslint, mypy, tsc, go vet — определяются "
        "автоматически) и возвращает список замечаний с файлами и строками. "
        "Вызывай после правок кода вместе с run_tests."
    )
    Args = RunLintArgs
    category = "execute"
    dangerous = True
    timeout = None

    def approval_reason(self, args: RunLintArgs) -> str:  # type: ignore[override]
        return tr("appr.lint", path=args.path, fix=tr("appr.lint_fix") if args.fix else "")

    def auto_verdict(self, args, ctx) -> str:  # type: ignore[override]
        """Линтер ничего не меняет."""
        return "allow"

    async def run(self, args: RunLintArgs, ctx: ToolContext) -> ToolResult:
        base = resolve_path(args.path, settings=ctx.settings, must_exist=True, must_be_dir=True)
        commands = detect_lint_commands(base)
        if not commands:
            # Отсутствие линтеров — не ошибка кода, а отсутствие инструментов.
            # Возвращаем успех с пояснением, иначе модель решит, что что-то сломано.
            return ToolResult(
                content=(
                    f"В '{args.path}' линтеры не настроены — проверка пропущена. "
                    "Это не ошибка. Корректность кода проверяй через run_tests."
                )
            )

        chunks: list[str] = []
        all_ok = True
        for command in commands:
            argv = command.argv
            if args.fix and command.tool == "ruff":
                argv = [*argv, "--fix"]
            result = await _execute(
                Command(kind=command.kind, tool=command.tool, argv=argv, description=command.description),
                base,
                args.timeout,
            )
            all_ok = all_ok and result.ok
            chunks.append(result.content)

        return ToolResult(content="\n\n---\n\n".join(chunks), ok=all_ok)
