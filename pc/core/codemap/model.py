"""Структуры карты кода."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class Symbol:
    """Определение в файле: класс, функция, метод, константа."""

    name: str
    kind: str  # class | function | method | const | interface | type | enum | struct
    line: int
    end_line: int = 0
    signature: str = ""
    parent: str = ""
    doc: str = ""

    @property
    def qualified_name(self) -> str:
        return f"{self.parent}.{self.name}" if self.parent else self.name

    def render(self, indent: str = "  ") -> str:
        prefix = indent * (2 if self.parent else 1)
        body = self.signature or self.name
        tail = f"  # {self.doc}" if self.doc else ""
        return f"{prefix}{body}:{self.line}{tail}"


@dataclass(slots=True)
class FileOutline:
    """Структура одного файла."""

    path: Path
    rel_path: str
    language: str
    lines: int = 0
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    doc: str = ""
    error: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.symbols and not self.imports

    def render(self, *, show_imports: bool = False) -> str:
        head = f"{self.rel_path} ({self.lines} строк, {self.language})"
        if self.error:
            return f"{head}\n  [не разобран: {self.error}]"

        lines = [head]
        if self.doc:
            lines.append(f"  \"\"\"{self.doc}\"\"\"")
        if show_imports and self.imports:
            preview = ", ".join(self.imports[:12])
            more = f" … ещё {len(self.imports) - 12}" if len(self.imports) > 12 else ""
            lines.append(f"  импорты: {preview}{more}")
        lines.extend(symbol.render() for symbol in self.symbols)
        if not self.symbols:
            lines.append("  (определений не найдено)")
        return "\n".join(lines)
