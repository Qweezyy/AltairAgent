"""Карта кода и поиск символов."""

from __future__ import annotations

from core.codemap import build_project_map, find_definitions, find_usages, outline_file
from core.tools.builtin.codemap_tools import CodeMapTool, FindSymbolTool

PY_SOURCE = '''"""Модуль расчётов."""

import os
from pathlib import Path

MAX_SIZE = 100


class Calculator:
    """Считает."""

    def add(self, a: int, b: int = 0) -> int:
        """Складывает."""
        return a + b

    async def fetch(self, url: str, *, timeout: float = 1.0) -> str:
        return url


def helper(items: list[str], **kwargs) -> None:
    pass
'''

TS_SOURCE = """import { useState } from 'react';

export interface User {
  id: number;
}

export type Role = 'admin' | 'user';

export class UserService {
  getUser(id: number) { return id; }
}

export async function loadUsers(): Promise<User[]> {
  return [];
}

export const MAX_USERS = 50;
const handler = async (event) => { console.log(event); };
"""


# --------------------------------------------------------------- python


def test_python_outline_extracts_all_kinds(settings, tmp_path):
    path = tmp_path / "calc.py"
    path.write_text(PY_SOURCE, encoding="utf-8")

    outline = outline_file(path, "calc.py")

    assert outline.language == "python"
    assert outline.doc == "Модуль расчётов."
    assert "os" in outline.imports and "pathlib.Path" in outline.imports

    by_name = {s.name: s for s in outline.symbols}
    assert by_name["MAX_SIZE"].kind == "const"
    assert by_name["Calculator"].kind == "class"
    assert by_name["add"].kind == "method" and by_name["add"].parent == "Calculator"
    assert by_name["helper"].kind == "function"


def test_python_signatures_are_readable(settings, tmp_path):
    path = tmp_path / "calc.py"
    path.write_text(PY_SOURCE, encoding="utf-8")
    by_name = {s.name: s for s in outline_file(path, "calc.py").symbols}

    assert by_name["add"].signature == "def add(self, a: int, b: int = ...) -> int"
    assert by_name["fetch"].signature.startswith("async def fetch(self, url: str, *, timeout")
    assert by_name["helper"].signature == "def helper(items: list[str], **kwargs) -> None"
    assert by_name["add"].doc == "Складывает."


def test_broken_python_reports_syntax_error(tmp_path):
    path = tmp_path / "broken.py"
    path.write_text("def oops(\n", encoding="utf-8")

    outline = outline_file(path, "broken.py")

    assert "синтаксическая ошибка" in outline.error
    assert "не разобран" in outline.render()


# ----------------------------------------------------------- другие языки


def test_typescript_outline(tmp_path):
    path = tmp_path / "service.ts"
    path.write_text(TS_SOURCE, encoding="utf-8")

    outline = outline_file(path, "service.ts")
    kinds = {s.name: s.kind for s in outline.symbols}

    assert outline.language == "typescript"
    assert kinds["User"] == "interface"
    assert kinds["Role"] == "type"
    assert kinds["UserService"] == "class"
    assert kinds["loadUsers"] == "function"
    assert kinds["MAX_USERS"] == "const"
    assert "react" in outline.imports


def test_go_outline(tmp_path):
    path = tmp_path / "main.go"
    path.write_text(
        "package main\n\ntype Server struct {\n}\n\nfunc (s *Server) Start() error {\n return nil\n}\n\nfunc main() {\n}\n",
        encoding="utf-8",
    )
    outline = outline_file(path, "main.go")
    names = {s.name for s in outline.symbols}
    assert {"Server", "Start", "main"} <= names


def test_binary_and_missing_files_are_handled(tmp_path):
    binary = tmp_path / "img.bin"
    binary.write_bytes(b"\x00\x01\x02")
    assert "бинарный" in outline_file(binary, "img.bin").error
    assert "не найден" in outline_file(tmp_path / "nope.py", "nope.py").error


# ------------------------------------------------------------ карта и поиск


def _make_project(root):
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "calc.py").write_text(PY_SOURCE, encoding="utf-8")
    (root / "app.py").write_text(
        "from pkg.calc import Calculator\n\n\ndef run():\n    return Calculator().add(1, 2)\n",
        encoding="utf-8",
    )
    (root / "node_modules").mkdir(exist_ok=True)
    (root / "node_modules" / "junk.py").write_text("def junk(): pass\n", encoding="utf-8")


def test_project_map_skips_junk_dirs(settings):
    root = settings.workspace
    _make_project(root)

    outlines, truncated = build_project_map(root, root)
    paths = {o.rel_path for o in outlines}

    assert "app.py" in paths and "pkg/calc.py" in paths
    assert not any("node_modules" in p for p in paths)
    assert truncated is False


def test_find_definition_and_usages(settings):
    root = settings.workspace
    _make_project(root)

    definitions = find_definitions(root, root, "Calculator")
    assert len(definitions) == 1
    assert definitions[0][0].rel_path == "pkg/calc.py"

    usages = find_usages(root, root, "Calculator")
    assert any(path == "app.py" for path, _, _ in usages)


# ------------------------------------------------------------- инструменты


async def test_code_map_tool_on_directory(ctx):
    _make_project(ctx.settings.workspace)

    result = await CodeMapTool().invoke({"path": "."}, ctx)

    assert result.ok
    assert "Карта кода" in result.content
    assert "class Calculator" in result.content
    assert "node_modules" not in result.content


async def test_code_map_tool_on_single_file(ctx):
    _make_project(ctx.settings.workspace)

    result = await CodeMapTool().invoke({"path": "pkg/calc.py", "show_imports": True}, ctx)

    assert result.ok
    assert "импорты:" in result.content
    assert "def helper" in result.content


async def test_code_map_is_much_cheaper_than_reading(ctx):
    """Смысл карты — экономия контекста, это должно быть измеримо."""
    root = ctx.settings.workspace
    # Реалистичный размер файла: тело функций много длиннее сигнатур.
    filler = "\n".join(f"    # строка реализации {i}" for i in range(80))
    body = f"{PY_SOURCE}\n\n{filler}"
    for index in range(12):
        (root / f"module_{index}.py").write_text(body, encoding="utf-8")

    result = await CodeMapTool().invoke({"path": "."}, ctx)
    full_size = sum((root / f"module_{i}.py").stat().st_size for i in range(12))

    assert result.ok
    assert all(f"module_{i}.py" in result.content for i in range(12))
    assert len(result.content) < full_size / 3, "карта должна быть в разы компактнее файлов"


async def test_find_symbol_tool_reports_definition_and_usages(ctx):
    _make_project(ctx.settings.workspace)

    result = await FindSymbolTool().invoke({"name": "Calculator"}, ctx)

    assert result.ok
    assert "pkg/calc.py:9" in result.content
    assert "Использования" in result.content
    assert "app.py" in result.content


async def test_find_symbol_explains_when_nothing_found(ctx):
    _make_project(ctx.settings.workspace)

    result = await FindSymbolTool().invoke({"name": "НетТакогоСимвола"}, ctx)

    assert result.ok
    assert "не найдено" in result.content
    assert "grep_search" in result.content  # подсказка, что делать дальше
