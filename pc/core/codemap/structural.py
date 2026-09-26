"""Структурный поиск по синтаксическому дереву Python (идея AST-grep).

`find_symbol` ищет по ИМЕНИ, `grep_search` — по тексту. Здесь — поиск по
СТРУКТУРЕ кода: «все функции с декоратором `@router.get`», «все классы-наследники
`BaseModel`», «все места, где вызывают `save()`», «функции без аннотации возврата».
Такое текстовым grep надёжно не выразить (декоратор бывает `@a.b.c(...)`, вызов —
через атрибут), а модель по AST отвечает точно.

Реализация на stdlib `ast`: ноль зависимостей, работает офлайн. Для не-Python
языков структурный разбор потребовал бы Tree-sitter с компилируемыми грамматиками
— это сознательно отложено, чтобы не раздувать сборку.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from core.codemap.builder import _rel, iter_source_files
from core.utils.text import read_text_file

#: Поддерживаемые виды запросов. Ключ — значение аргумента `kind` инструмента.
QUERY_KINDS = (
    "decorated_by",
    "subclass_of",
    "calls",
    "raises",
    "imports",
    "async_functions",
    "missing_return_type",
)

#: Виды, которым нужен аргумент `target`.
_NEEDS_TARGET = {"decorated_by", "subclass_of", "calls", "raises", "imports"}


@dataclass
class Match:
    rel_path: str
    line: int
    label: str
    detail: str = ""


def _dotted(node: ast.AST) -> str:
    """Собирает точечное имя из Name/Attribute/Call (`app.router.get` и т.п.)."""
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _name_matches(dotted: str, target: str) -> bool:
    """Совпадение по точечному имени.

    Односегментный `target` (`get`) матчит по последнему сегменту — находит и
    `@router.get`, и `@app.get`. Многосегментный (`router.get`) требует, чтобы
    имя оканчивалось на него, — точнее.
    """
    if not dotted:
        return False
    if dotted == target:
        return True
    if "." in target:
        return dotted.endswith("." + target)
    return dotted.split(".")[-1] == target


def _decorators(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> list[str]:
    return [_dotted(d) for d in node.decorator_list]


def _func_flags(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    flags = []
    if isinstance(node, ast.AsyncFunctionDef):
        flags.append("async")
    if node.returns is None:
        flags.append("без аннотации возврата")
    decos = _decorators(node)
    if decos:
        flags.append("@" + ", @".join(decos))
    return "; ".join(flags)


class _Visitor(ast.NodeVisitor):
    """Собирает совпадения одного запроса по одному файлу."""

    def __init__(self, kind: str, target: str, rel_path: str) -> None:
        self.kind = kind
        self.target = target
        self.rel = rel_path
        self.matches: list[Match] = []

    # -- функции и методы --------------------------------------------------

    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if self.kind == "async_functions" and isinstance(node, ast.AsyncFunctionDef):
            self._add(node.lineno, f"async def {node.name}", _func_flags(node))
        elif self.kind == "missing_return_type" and node.returns is None:
            # Пропускаем dunder и свойства-сеттеры: там аннотация обычно излишня.
            if not (node.name.startswith("__") and node.name.endswith("__")):
                self._add(node.lineno, f"def {node.name}", _func_flags(node))
        elif self.kind == "decorated_by":
            if any(_name_matches(d, self.target) for d in _decorators(node)):
                self._add(node.lineno, f"def {node.name}", _func_flags(node))
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node)

    # -- классы ------------------------------------------------------------

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if self.kind == "decorated_by" and any(
            _name_matches(d, self.target) for d in _decorators(node)
        ):
            self._add(node.lineno, f"class {node.name}", "класс")
        elif self.kind == "subclass_of":
            bases = [_dotted(b) for b in node.bases]
            if any(_name_matches(b, self.target) for b in bases):
                self._add(node.lineno, f"class {node.name}", f"наследует {', '.join(bases)}")
        self.generic_visit(node)

    # -- вызовы, raise, импорты -------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        if self.kind == "calls" and _name_matches(_dotted(node.func), self.target):
            self._add(node.lineno, _dotted(node.func) + "(...)", "вызов")
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        if self.kind == "raises" and node.exc is not None:
            name = _dotted(node.exc)
            if _name_matches(name, self.target):
                self._add(node.lineno, f"raise {name}", "")
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        if self.kind == "imports":
            for alias in node.names:
                if _name_matches(alias.name, self.target):
                    self._add(node.lineno, f"import {alias.name}", "")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self.kind == "imports" and node.module:
            names = ", ".join(a.name for a in node.names)
            if _name_matches(node.module, self.target) or any(
                _name_matches(a.name, self.target) for a in node.names
            ):
                self._add(node.lineno, f"from {node.module} import {names}", "")
        self.generic_visit(node)

    def _add(self, line: int, label: str, detail: str) -> None:
        self.matches.append(Match(self.rel, line, label, detail))


def _search_file(path: Path, workspace: Path, kind: str, target: str) -> list[Match]:
    try:
        tree = ast.parse(read_text_file(path))
    except (SyntaxError, ValueError, OSError):
        return []
    visitor = _Visitor(kind, target.strip(), _rel(path, workspace))
    visitor.visit(tree)
    return visitor.matches


def structural_search(
    base: Path,
    workspace: Path,
    kind: str,
    target: str = "",
    *,
    limit: int = 200,
) -> list[Match]:
    """Ищет по .py под `base`. Если `base` — файл, только по нему.

    Возвращает не больше `limit` совпадений.
    """
    if base.is_file():
        return _search_file(base, workspace, kind, target)[:limit]

    results: list[Match] = []
    for path in iter_source_files(base, {".py", ".pyi"}):
        results.extend(_search_file(path, workspace, kind, target))
        if len(results) >= limit:
            return results[:limit]
    return results
