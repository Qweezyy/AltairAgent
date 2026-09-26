"""Структуры данных unified diff.

Намеренно не используем `difflib`/`patch` из стандартной библиотеки: они умеют
только строгое применение по номерам строк. Модель почти всегда ошибается в
номерах и счётчиках хунков, поэтому нам нужен свой применятель с допуском.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class LineOp(str, Enum):
    """Тип строки внутри хунка."""

    CONTEXT = " "
    ADD = "+"
    REMOVE = "-"


@dataclass(slots=True)
class PatchLine:
    op: LineOp
    text: str


@dataclass(slots=True)
class Hunk:
    """Один блок изменений `@@ -a,b +c,d @@`."""

    old_start: int
    new_start: int
    lines: list[PatchLine] = field(default_factory=list)
    header: str = ""
    #: V4A-style scope anchors (`@@ class Foo` / `@@     def bar`): lines to locate, in order,
    #: before matching the context. They pin a hunk whose context repeats elsewhere in the file.
    anchors: list[str] = field(default_factory=list)

    @property
    def old_block(self) -> list[str]:
        """Строки, которые должны быть в файле до применения."""
        return [ln.text for ln in self.lines if ln.op in (LineOp.CONTEXT, LineOp.REMOVE)]

    @property
    def new_block(self) -> list[str]:
        """Строки, которые окажутся в файле после применения."""
        return [ln.text for ln in self.lines if ln.op in (LineOp.CONTEXT, LineOp.ADD)]

    @property
    def added(self) -> int:
        return sum(1 for ln in self.lines if ln.op is LineOp.ADD)

    @property
    def removed(self) -> int:
        return sum(1 for ln in self.lines if ln.op is LineOp.REMOVE)

    @property
    def is_pure_insert(self) -> bool:
        """Хунк без контекста и удалений — чистая вставка."""
        return not self.old_block


class FileAction(str, Enum):
    MODIFY = "modify"
    CREATE = "create"
    DELETE = "delete"
    RENAME = "rename"


@dataclass(slots=True)
class FilePatch:
    """Изменения одного файла."""

    path: str
    action: FileAction = FileAction.MODIFY
    old_path: str | None = None
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def added(self) -> int:
        return sum(h.added for h in self.hunks)

    @property
    def removed(self) -> int:
        return sum(h.removed for h in self.hunks)


@dataclass(slots=True)
class HunkResult:
    """Что случилось с конкретным хунком."""

    index: int
    applied: bool
    #: На сколько строк сместился хунк относительно заявленного номера.
    offset: int = 0
    #: Как совпал контекст: exact | offset | whitespace | search
    strategy: str = "exact"
    error: str = ""


@dataclass(slots=True)
class FileResult:
    path: str
    action: FileAction
    applied: bool
    hunks: list[HunkResult] = field(default_factory=list)
    added: int = 0
    removed: int = 0
    error: str = ""
    #: Новое содержимое файла (заполняется даже в режиме проверки).
    new_content: str | None = None

    @property
    def failed_hunks(self) -> list[HunkResult]:
        return [h for h in self.hunks if not h.applied]
