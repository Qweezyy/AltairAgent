"""Разбор Python через штатный модуль `ast`.

Для Python не нужен tree-sitter: встроенный парсер даёт точный результат,
не тянет бинарных зависимостей и всегда соответствует версии интерпретатора.
"""

from __future__ import annotations

import ast
from pathlib import Path

from core.codemap.model import FileOutline, Symbol


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = node.args
    parts: list[str] = []

    positional = [*getattr(args, "posonlyargs", []), *args.args]
    defaults_offset = len(positional) - len(args.defaults)
    for index, arg in enumerate(positional):
        text = arg.arg
        if arg.annotation is not None:
            text += f": {ast.unparse(arg.annotation)}"
        if index >= defaults_offset:
            text += " = ..."
        parts.append(text)

    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    elif args.kwonlyargs:
        parts.append("*")

    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=False):
        text = arg.arg
        if arg.annotation is not None:
            text += f": {ast.unparse(arg.annotation)}"
        if default is not None:
            text += " = ..."
        parts.append(text)

    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")

    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return f"{prefix} {node.name}({', '.join(parts)}){returns}"


def _short_doc(node: ast.AST, limit: int = 90) -> str:
    try:
        doc = ast.get_docstring(node)  # type: ignore[arg-type]
    except TypeError:
        return ""
    if not doc:
        return ""
    first = doc.strip().splitlines()[0].strip()
    return first[:limit] + ("…" if len(first) > limit else "")


def _end_line(node: ast.AST, fallback: int) -> int:
    return getattr(node, "end_lineno", None) or fallback


def outline_python(path: Path, rel_path: str, source: str) -> FileOutline:
    outline = FileOutline(
        path=path,
        rel_path=rel_path,
        language="python",
        lines=len(source.splitlines()),
    )

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        outline.error = f"синтаксическая ошибка в строке {exc.lineno}: {exc.msg}"
        return outline

    outline.doc = _short_doc(tree)

    for node in tree.body:
        if isinstance(node, ast.Import):
            outline.imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or "."
            outline.imports.extend(f"{module}.{alias.name}" for alias in node.names)

        elif isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(base) for base in node.bases)
            outline.symbols.append(
                Symbol(
                    name=node.name,
                    kind="class",
                    line=node.lineno,
                    end_line=_end_line(node, node.lineno),
                    signature=f"class {node.name}({bases})" if bases else f"class {node.name}",
                    doc=_short_doc(node),
                )
            )
            for child in node.body:
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    outline.symbols.append(
                        Symbol(
                            name=child.name,
                            kind="method",
                            line=child.lineno,
                            end_line=_end_line(child, child.lineno),
                            signature=_signature(child),
                            parent=node.name,
                            doc=_short_doc(child),
                        )
                    )

        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            outline.symbols.append(
                Symbol(
                    name=node.name,
                    kind="function",
                    line=node.lineno,
                    end_line=_end_line(node, node.lineno),
                    signature=_signature(node),
                    doc=_short_doc(node),
                )
            )

        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                # Константы уровня модуля: обычно это конфигурация, её полезно видеть.
                if isinstance(target, ast.Name) and target.id.isupper():
                    outline.symbols.append(
                        Symbol(
                            name=target.id,
                            kind="const",
                            line=node.lineno,
                            end_line=_end_line(node, node.lineno),
                            signature=target.id,
                        )
                    )

    outline.symbols.sort(key=lambda s: s.line)
    return outline
