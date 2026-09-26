"""Карта кода: структура проекта и поиск определений без чтения файлов целиком."""

from core.codemap.builder import (
    build_project_map,
    find_definitions,
    find_usages,
    iter_source_files,
    language_of,
    outline_file,
)
from core.codemap.model import FileOutline, Symbol

__all__ = [
    "FileOutline",
    "Symbol",
    "build_project_map",
    "find_definitions",
    "find_usages",
    "iter_source_files",
    "language_of",
    "outline_file",
]
