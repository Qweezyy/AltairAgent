"""Доступ к файлам рабочих папок для панели превью.

Зачем отдельный модуль: раньше интерфейс пытался достать файл через
`/static/../путь`, статика справедливо блокировала обход каталогов, и превью
показывало только путь вместо содержимого.

Отдаём файлы двумя способами:
  * `read_text_file()` — содержимое для подсветки синтаксиса;
  * сырой файл по маршруту `/files/<путь>` — чтобы HTML открывался в iframe
    и подтягивал свои же css/js по относительным ссылкам.

Безопасность: путь обязан лежать внутри разрешённых корней. Корни — это
рабочие папки (текущая и из сохранённых чатов) плюс EXTRA_ALLOWED_ROOTS.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from core.errors import PathNotAllowed
from core.security.paths import _is_within  # общая нормализация путей Windows
from core.settings import Settings
from core.utils.fs import human_size
from core.utils.text import looks_binary, read_text_file

#: Больше этого в панель превью не грузим — она не файловый менеджер.
MAX_PREVIEW_BYTES = 3 * 1024 * 1024

#: Расширение -> язык для подсветки синтаксиса.
LANGUAGES = {
    ".html": "html", ".htm": "html", ".xml": "xml", ".svg": "xml",
    ".css": "css", ".scss": "scss", ".sass": "scss", ".less": "less",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".vue": "html", ".svelte": "html",
    ".json": "json", ".yml": "yaml", ".yaml": "yaml", ".toml": "ini", ".ini": "ini",
    ".py": "python", ".rb": "ruby", ".php": "php", ".go": "go", ".rs": "rust",
    ".java": "java", ".cs": "csharp", ".c": "c", ".cpp": "cpp", ".h": "cpp",
    ".sh": "bash", ".bat": "dos", ".ps1": "powershell", ".sql": "sql",
    ".md": "markdown", ".markdown": "markdown", ".txt": "plaintext", ".env": "ini",
}

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp", ".avif"}


def preview_roots(settings: Settings, extra: list[str] | None = None) -> list[Path]:
    """Каталоги, файлы из которых разрешено показывать."""
    roots = list(settings.allowed_roots)
    for raw in extra or []:
        candidate = str(raw).strip()
        if not candidate:
            continue
        try:
            resolved = Path(candidate).expanduser().resolve()
        except OSError:
            continue
        if resolved.is_dir():
            roots.append(resolved)
    return roots


def resolve_preview_path(raw_path: str, roots: list[Path]) -> Path:
    """Проверяет путь и возвращает файл. Кидает PathNotAllowed."""
    cleaned = str(raw_path).strip().strip('"').replace("\\", "/")
    if not cleaned:
        raise PathNotAllowed("Путь не указан.")

    try:
        target = Path(cleaned).expanduser().resolve()
    except OSError as exc:
        raise PathNotAllowed(f"Некорректный путь: {exc}") from exc

    if not any(_is_within(target, root) for root in roots):
        raise PathNotAllowed(
            "Файл лежит вне рабочих папок агента, показывать его нельзя."
        )
    if not target.exists() or not target.is_file():
        raise PathNotAllowed(f"Файл не найден: {target}")
    return target


def describe(path: Path) -> dict:
    """Метаданные файла для панели превью."""
    suffix = path.suffix.lower()
    size = path.stat().st_size

    if suffix in IMAGE_SUFFIXES and suffix != ".svg":
        kind = "image"
    elif suffix in (".html", ".htm"):
        kind = "html"
    elif looks_binary(path):
        kind = "binary"
    else:
        kind = "text"

    return {
        "name": path.name,
        "path": str(path),
        "kind": kind,
        "language": LANGUAGES.get(suffix, ""),
        "size": size,
        "size_text": human_size(size),
    }


def load_preview(path: Path) -> dict:
    """Содержимое файла плюс метаданные."""
    info = describe(path)
    if info["kind"] in ("image", "binary"):
        info["content"] = ""
        return info
    if info["size"] > MAX_PREVIEW_BYTES:
        info["kind"] = "too_big"
        info["content"] = ""
        return info

    info["content"] = read_text_file(path)
    return info


def guess_media_type(path: Path) -> str:
    """MIME для сырой отдачи. Без него браузер не отрисует html и не применит css."""
    suffix = path.suffix.lower()
    manual = {
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".css": "text/css",
        ".html": "text/html",
        ".htm": "text/html",
        ".json": "application/json",
        ".svg": "image/svg+xml",
        ".md": "text/plain",
    }
    if suffix in manual:
        return f"{manual[suffix]}; charset=utf-8"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"
