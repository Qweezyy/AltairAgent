"""Семантическая навигация по коду через language server (LSP).

`code_intel` отвечает на вопросы «где ОПРЕДЕЛЁН символ», «где он используется»,
«какой у него тип/сигнатура» — руками настоящего сервера (pyright для Python;
tsserver/gopls/rust-analyzer, если установлены). Точнее `find_symbol`: сервер
различает одноимённые символы, следует за импортами и понимает типы.

Агент задаёт символ по ИМЕНИ; инструмент сам находит его позицию в файле и
спрашивает сервер по координатам.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from core.lsp.servers import ServerUnavailable, get_lsp_manager
from core.security.paths import resolve_path
from core.tools.base import Tool, ToolContext, ToolResult

_ACTIONS = ("definition", "references", "hover")


def _find_position(text: str, symbol: str, prefer_line: int | None) -> tuple[int, int] | None:
    """Находит позицию (0-based line, char) вхождения `symbol` как целого слова.

    Если задан `prefer_line` (1-based) — ищет на нём; иначе первое вхождение.
    """
    pattern = re.compile(rf"\b{re.escape(symbol)}\b")
    lines = text.splitlines()
    if prefer_line is not None and 1 <= prefer_line <= len(lines):
        match = pattern.search(lines[prefer_line - 1])
        if match:
            return prefer_line - 1, match.start()
    for index, line in enumerate(lines):
        match = pattern.search(line)
        if match:
            return index, match.start()
    return None


def _uri_to_rel(uri: str, workspace: Path) -> str:
    """file:///... -> относительный путь для вывода."""
    from urllib.parse import unquote, urlparse

    parsed = urlparse(uri)
    raw = unquote(parsed.path)
    # На Windows path вида /d:/AI_Agent/... — убираем ведущий слэш.
    if re.match(r"^/[a-zA-Z]:", raw):
        raw = raw[1:]
    try:
        return str(Path(raw).resolve().relative_to(workspace.resolve())).replace("\\", "/")
    except ValueError:
        return raw


class CodeIntelArgs(BaseModel):
    action: str = Field(description="definition (где определён), references (где используется) или hover (тип/сигнатура)")
    symbol: str = Field(description="Имя символа: функции, класса, метода, переменной")
    path: str = Field(description="Файл, где встречается символ")
    line: int | None = Field(default=None, description="Номер строки (1-based) для уточнения вхождения, если символов несколько")


class CodeIntelTool(Tool):
    name = "code_intel"
    description = (
        "Семантическая навигация через language server: action=definition (перейти к определению), "
        "references (все использования), hover (тип и сигнатура). Точнее find_symbol — понимает импорты, "
        "одноимённые символы и типы. Работает для Python (pyright) и языков с установленным сервером "
        "(TypeScript, Go, Rust)."
    )
    Args = CodeIntelArgs
    category = "read"
    timeout = None  # первый запуск сервера может качать node

    async def run(self, args: CodeIntelArgs, ctx: ToolContext) -> ToolResult:
        action = args.action.strip().lower()
        if action not in _ACTIONS:
            return ToolResult.fail(f"action должен быть одним из: {', '.join(_ACTIONS)}.")
        symbol = args.symbol.strip()
        if not symbol:
            return ToolResult.fail("Не указан символ.")

        path = resolve_path(args.path, settings=ctx.settings, must_exist=True, must_be_file=True)
        text = path.read_text(encoding="utf-8", errors="replace")
        position = _find_position(text, symbol, args.line)
        if position is None:
            return ToolResult.fail(
                f"Символ «{symbol}» не найден в {args.path}"
                + (f" на строке {args.line}." if args.line else ".")
            )
        line0, char0 = position
        workspace = ctx.settings.workspace

        try:
            client, spec = await get_lsp_manager().client_for(path, workspace)
        except ServerUnavailable as exc:
            return ToolResult.fail(str(exc))

        if action == "hover":
            info = await client.hover(path, line0, char0)
            if not info:
                return ToolResult(content=f"Сервер {spec.label} не дал сведений о «{symbol}».")
            return ToolResult(content=f"«{symbol}» ({spec.label}):\n{info}")

        locations = (
            await client.definition(path, line0, char0)
            if action == "definition"
            else await client.references(path, line0, char0)
        )
        if not locations:
            what = "определение" if action == "definition" else "использования"
            return ToolResult(content=f"{spec.label}: {what} для «{symbol}» не найдены.")

        title = "Определение" if action == "definition" else f"Использования ({len(locations)})"
        lines = [f"{title} «{symbol}» ({spec.label}):"]
        for loc in locations[:80]:
            rel = _uri_to_rel(loc.get("uri", ""), workspace)
            start = loc.get("range", {}).get("start", {})
            ln = int(start.get("line", 0)) + 1
            col = int(start.get("character", 0)) + 1
            lines.append(f"  {rel}:{ln}:{col}")
        return ToolResult(content="\n".join(lines))
