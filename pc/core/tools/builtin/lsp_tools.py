"""Инструменты языковой диагностики (LSP-класс): проверка типов через pyright."""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.lsp import type_check
from core.lsp.diagnostics import PyrightUnavailable
from core.security.paths import resolve_path
from core.tools.base import Tool, ToolContext, ToolResult

#: Сколько диагностик показываем разом, чтобы не забить контекст.
_MAX_SHOWN = 60


class TypeCheckArgs(BaseModel):
    path: str = Field(
        default=".", description="Файл или папка для проверки (по умолчанию весь проект)"
    )
    warnings: bool = Field(default=True, description="Показывать предупреждения, а не только ошибки")


class TypeCheckTool(Tool):
    name = "type_check"
    description = (
        "Проверяет типы и семантику Python-кода статическим анализатором pyright: несовпадения "
        "типов, неопределённые имена, неверные аргументы, недостижимый код. Ловит ошибки, "
        "которые не видит линтер (run_lint) и до запуска тестов. Запускай после правок и перед сдачей."
    )
    Args = TypeCheckArgs
    category = "read"
    timeout = None  # первый запуск pyright качает node; лимит держим внутри

    async def run(self, args: TypeCheckArgs, ctx: ToolContext) -> ToolResult:
        target = resolve_path(args.path, settings=ctx.settings, must_exist=True)
        try:
            result = await type_check(target, ctx.settings.workspace, settings=ctx.settings)
        except PyrightUnavailable as exc:
            return ToolResult.fail(
                f"{exc}\nПроверка типов недоступна — можно продолжить без неё, "
                "но лучше поставить pyright."
            )

        shown = [
            d
            for d in result.diagnostics
            if args.warnings or d.severity == "error"
        ]
        if not shown:
            summary = (
                f"Проверено файлов: {result.files_analyzed}. Ошибок типов нет."
                if result.ok
                else f"Ошибок: {result.error_count} (скрыты предупреждения)."
            )
            return ToolResult(content=f"✅ {summary}")

        lines = [
            f"Проверено файлов: {result.files_analyzed}. "
            f"Ошибок: {result.error_count}, предупреждений: {result.warning_count}.",
            "",
        ]
        icon = {"error": "✗", "warning": "⚠", "information": "ℹ"}
        for d in shown[:_MAX_SHOWN]:
            mark = icon.get(d.severity, "•")
            rule = f" [{d.rule}]" if d.rule else ""
            lines.append(f"{mark} {d.rel_path}:{d.line}:{d.col} — {d.message}{rule}")
        if len(shown) > _MAX_SHOWN:
            lines.append(f"... и ещё {len(shown) - _MAX_SHOWN}. Сузьте path.")
        return ToolResult(content="\n".join(lines), ok=result.error_count == 0)
