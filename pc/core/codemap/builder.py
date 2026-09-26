"""Построение карты проекта и поиск символов."""

from __future__ import annotations

import re
from pathlib import Path

from core.codemap.generic_outline import LANGUAGE_RULES, outline_generic, supported_suffixes
from core.codemap.model import FileOutline, Symbol
from core.codemap.python_outline import outline_python
from core.utils.fs import IGNORED_DIRS
from core.utils.text import looks_binary, read_text_file

#: Файлы больше этого размера не разбираем: это почти всегда сгенерированный код.
MAX_FILE_BYTES = 800_000


def outline_file(path: Path, rel_path: str | None = None) -> FileOutline:
    """Структура одного файла. Точка расширения для новых языков."""
    rel = rel_path or path.name
    suffix = path.suffix.lower()

    if not path.exists() or not path.is_file():
        return FileOutline(path=path, rel_path=rel, language="unknown", error="файл не найден")
    if looks_binary(path):
        return FileOutline(path=path, rel_path=rel, language="binary", error="бинарный файл")
    if path.stat().st_size > MAX_FILE_BYTES:
        return FileOutline(path=path, rel_path=rel, language="unknown", error="слишком большой файл")

    source = read_text_file(path)
    if suffix in (".py", ".pyi"):
        return outline_python(path, rel, source)
    # Сначала пробуем точный tree-sitter; если грамматики нет — regex-разбор.
    from core.codemap.treesitter_outline import outline_treesitter

    ts_outline = outline_treesitter(path, rel, source)
    if ts_outline is not None:
        return ts_outline
    return outline_generic(path, rel, source)


def _all_supported_suffixes() -> set[str]:
    """Суффиксы regex-разбора плюс те, что добавляет tree-sitter (.java, .cs, .jsx…)."""
    from core.codemap.treesitter_outline import supported_suffixes as ts_suffixes

    return supported_suffixes() | ts_suffixes()


def iter_source_files(base: Path, suffixes: set[str] | None = None, limit: int = 800):
    """Обход исходников проекта с пропуском мусорных папок."""
    allowed = suffixes or _all_supported_suffixes()
    count = 0
    for root, dirs, files in _walk(base):
        for name in sorted(files):
            if Path(name).suffix.lower() not in allowed:
                continue
            yield Path(root) / name
            count += 1
            if count >= limit:
                return
        dirs.sort()


def _walk(base: Path):
    import os

    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
        yield root, dirs, files


def build_project_map(
    base: Path,
    workspace: Path,
    *,
    max_files: int = 200,
    suffixes: set[str] | None = None,
) -> tuple[list[FileOutline], bool]:
    """Карта каталога. Возвращает (структуры файлов, был ли достигнут лимит)."""
    outlines: list[FileOutline] = []
    truncated = False

    for index, file_path in enumerate(iter_source_files(base, suffixes)):
        if index >= max_files:
            truncated = True
            break
        rel = _rel(file_path, workspace)
        outline = outline_file(file_path, rel)
        if not outline.is_empty or outline.error:
            outlines.append(outline)

    outlines.sort(key=lambda o: o.rel_path)
    return outlines, truncated


def _rel(path: Path, workspace: Path) -> str:
    try:
        return str(path.relative_to(workspace)).replace("\\", "/")
    except ValueError:
        return str(path)


def language_of(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".py", ".pyi"):
        return "python"
    return LANGUAGE_RULES.get(suffix, ("text", []))[0]


def find_definitions(
    base: Path, workspace: Path, name: str, *, max_files: int = 800
) -> list[tuple[FileOutline, Symbol]]:
    """Ищет определения символа во всех исходниках."""
    target = name.strip()
    found: list[tuple[FileOutline, Symbol]] = []

    for file_path in iter_source_files(base, limit=max_files):
        outline = outline_file(file_path, _rel(file_path, workspace))
        for symbol in outline.symbols:
            if symbol.name == target:
                found.append((outline, symbol))
    return found


def find_usages(
    base: Path,
    workspace: Path,
    name: str,
    *,
    limit: int = 40,
    max_files: int = 800,
) -> list[tuple[str, int, str]]:
    """Ищет упоминания символа: (файл, строка, текст)."""
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    usages: list[tuple[str, int, str]] = []

    for file_path in iter_source_files(base, limit=max_files):
        rel = _rel(file_path, workspace)
        try:
            with open(file_path, encoding="utf-8", errors="ignore") as handle:
                for number, line in enumerate(handle, start=1):
                    if pattern.search(line):
                        usages.append((rel, number, line.strip()[:200]))
                        if len(usages) >= limit:
                            return usages
        except OSError:
            continue
    return usages
