"""Навигация по коду: карта проекта и поиск символов.

Зачем: раньше, чтобы понять структуру проекта, агент читал файлы целиком и
съедал контекст. Карта даёт ту же информацию на порядок дешевле — имена,
сигнатуры и номера строк, по которым можно точечно дочитать нужное.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from core.codemap import build_project_map, find_definitions, find_usages, outline_file
from core.codemap.structural import QUERY_KINDS, structural_search
from core.errors import ToolError
from core.security.paths import resolve_path, safe_relpath
from core.tools.base import Tool, ToolContext


class CodeMapArgs(BaseModel):
    path: str = Field(default=".", description="File or folder (default: the whole project)")
    show_imports: bool = Field(default=False, description="Include each file's imports")
    max_files: int = Field(default=120, ge=1, le=400, description="Maximum files in the map")


class CodeMapTool(Tool):
    name = "code_map"
    description = (
        "Shows code structure — classes, functions and methods per file with line numbers and "
        "signatures. Start here in an unfamiliar project or file: far cheaper than reading whole "
        "files; then read just the needed ranges with read_file."
    )
    Args = CodeMapArgs
    category = "read"
    timeout = 120.0

    async def run(self, args: CodeMapArgs, ctx: ToolContext) -> str:
        target = resolve_path(args.path, settings=ctx.settings, must_exist=True)
        workspace = ctx.settings.workspace

        def work() -> str:
            if target.is_file():
                outline = outline_file(target, safe_relpath(target, ctx.settings))
                return outline.render(show_imports=args.show_imports)

            outlines, truncated = build_project_map(
                target, workspace, max_files=args.max_files
            )
            if not outlines:
                raise ToolError(
                    f"В '{args.path}' не найдено файлов с кодом. Проверь путь через list_directory."
                )

            symbols = sum(len(o.symbols) for o in outlines)
            head = f"Карта кода: {len(outlines)} файлов, {symbols} определений"
            if truncated:
                head += f" (показаны первые {args.max_files} — уточни path)"

            body = "\n\n".join(o.render(show_imports=args.show_imports) for o in outlines)
            return f"{head}\n\n{body}"

        return await asyncio.to_thread(work)


class FindSymbolArgs(BaseModel):
    name: str = Field(description="Точное имя класса, функции, метода или константы")
    path: str = Field(default=".", description="Где искать (по умолчанию весь проект)")
    with_usages: bool = Field(default=True, description="Показывать места использования")


class FindSymbolTool(Tool):
    name = "find_symbol"
    description = (
        "Находит, ГДЕ ОПРЕДЕЛЁН символ (класс, функция, метод) и где он используется. "
        "Заменяет ручной перебор grep_search: сразу отделяет определение от вызовов. "
        "Используй перед изменением или удалением функции, чтобы не сломать вызывающий код."
    )
    Args = FindSymbolArgs
    category = "read"
    timeout = 120.0

    async def run(self, args: FindSymbolArgs, ctx: ToolContext) -> str:
        base = resolve_path(args.path, settings=ctx.settings, must_exist=True, must_be_dir=False)
        workspace = ctx.settings.workspace
        name = args.name.strip()
        if not name:
            raise ToolError("Не указано имя символа.")

        def work() -> str:
            search_root = base if base.is_dir() else base.parent
            definitions = find_definitions(search_root, workspace, name)

            lines: list[str] = []
            if definitions:
                lines.append(f"Определения '{name}' ({len(definitions)}):")
                for outline, symbol in definitions[:20]:
                    where = f"{outline.rel_path}:{symbol.line}"
                    kind = symbol.kind
                    parent = f" (в классе {symbol.parent})" if symbol.parent else ""
                    lines.append(f"  {where}  [{kind}]{parent}")
                    if symbol.signature:
                        lines.append(f"      {symbol.signature}")
                    if symbol.doc:
                        lines.append(f"      — {symbol.doc}")
            else:
                lines.append(
                    f"Определение '{name}' не найдено среди разбираемых языков. "
                    "Возможно, символ приходит из внешней библиотеки или объявлен нестандартно — "
                    "попробуй grep_search."
                )

            if args.with_usages:
                usages = find_usages(search_root, workspace, name)
                definition_places = {
                    (outline.rel_path, symbol.line) for outline, symbol in definitions
                }
                filtered = [u for u in usages if (u[0], u[1]) not in definition_places]
                lines.append("")
                if filtered:
                    lines.append(f"Использования ({len(filtered)}{'+' if len(usages) >= 40 else ''}):")
                    lines.extend(f"  {path}:{number}: {text}" for path, number, text in filtered)
                else:
                    lines.append("Использований не найдено.")

            return "\n".join(lines)

        return await asyncio.to_thread(work)


class AstSearchArgs(BaseModel):
    kind: str = Field(
        description=(
            "Вид структурного запроса: decorated_by (с декоратором), subclass_of (наследники), "
            "calls (места вызова), raises (где кидают исключение), imports (кто импортирует), "
            "async_functions (все async), missing_return_type (функции без аннотации возврата)"
        )
    )
    target: str = Field(
        default="",
        description="Имя декоратора/класса/функции/модуля. Не нужно для async_functions и missing_return_type",
    )
    path: str = Field(default=".", description="Где искать (по умолчанию весь проект)")


class AstSearchTool(Tool):
    name = "ast_search"
    description = (
        "Структурный поиск по синтаксическому дереву Python — по СТРУКТУРЕ, а не по имени или тексту. "
        "Отвечает на вопросы вроде «все функции с декоратором @router.get», «все классы-наследники "
        "BaseModel», «где вызывают save()», «функции без аннотации возврата». Точнее grep_search "
        "(понимает @a.b.c, вызовы через атрибут) и работает только для Python."
    )
    Args = AstSearchArgs
    category = "read"
    timeout = 120.0

    async def run(self, args: AstSearchArgs, ctx: ToolContext) -> str:
        kind = args.kind.strip()
        if kind not in QUERY_KINDS:
            raise ToolError(
                f"Неизвестный вид запроса '{kind}'. Доступны: {', '.join(QUERY_KINDS)}."
            )
        needs_target = kind in {"decorated_by", "subclass_of", "calls", "raises", "imports"}
        if needs_target and not args.target.strip():
            raise ToolError(f"Для '{kind}' нужен аргумент target (что искать).")

        base = resolve_path(args.path, settings=ctx.settings, must_exist=True)
        workspace = ctx.settings.workspace

        def work() -> str:
            matches = structural_search(base, workspace, kind, args.target)
            if not matches:
                extra = f" '{args.target}'" if args.target.strip() else ""
                return f"Ничего не найдено по запросу {kind}{extra}."
            title = f"Найдено {len(matches)} по запросу «{kind}"
            title += f" {args.target}»" if args.target.strip() else "»"
            lines = [title + ":"]
            for m in matches:
                row = f"  {m.rel_path}:{m.line}: {m.label}"
                if m.detail:
                    row += f"  [{m.detail}]"
                lines.append(row)
            return "\n".join(lines)

        return await asyncio.to_thread(work)
