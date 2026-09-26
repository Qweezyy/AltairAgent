"""Вложения к запросу: фото, видео, файлы, папки и архивы.

Как это устроено: пользователь прикладывает путь, а не содержимое. Дальше
файл превращается либо в часть мультимодального сообщения (фото и видео
уходят модели как есть), либо в текстовый блок контекста (документ, папка,
архив), либо просто в ссылку с описанием — если файл слишком велик, чтобы
тащить его в диалог целиком.

Принцип: агент всегда знает полный путь к вложению, поэтому даже когда файл
не влез в контекст, он может открыть его инструментами.
"""

from __future__ import annotations

import base64
import mimetypes
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.logging_setup import get_logger
from core.research.documents import DocumentError, extract_document, is_document

logger = get_logger("attachments")

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
#: Форматы видео, которые принимают провайдеры (OpenRouter: video/mp4|mpeg|mov|webm).
VIDEO_SUFFIXES = {".mp4", ".mpeg", ".mpg", ".mov", ".webm"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".ogg", ".m4a", ".flac"}
ARCHIVE_SUFFIXES = {".zip"}

#: Лимиты: всё уезжает в запрос как base64, который на треть тяжелее файла.
MAX_IMAGE_MB = 20.0
MAX_VIDEO_MB = 50.0
MAX_TEXT_CHARS = 20_000
#: Сколько файлов показываем из папки или архива, прежде чем оборвать список.
MAX_LISTING = 200


@dataclass(slots=True)
class Attachment:
    """Одно вложение и всё, что о нём известно."""

    path: Path
    kind: str  # image | video | audio | document | archive | folder | text | binary
    size: int = 0
    #: Текстовый блок для контекста (пусто у фото и видео).
    text: str = ""
    #: Часть мультимодального сообщения (пусто у текстовых вложений).
    part: dict[str, Any] | None = None
    #: Почему вложение не удалось прочитать целиком.
    note: str = ""

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def is_media(self) -> bool:
        return self.kind in ("image", "video", "audio")


@dataclass(slots=True)
class AttachmentSet:
    """Все вложения одного запроса."""

    items: list[Attachment] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def has_media(self) -> bool:
        return any(item.is_media for item in self.items)

    def parts(self) -> list[dict[str, Any]]:
        """Мультимодальные части сообщения (фото, видео, аудио)."""
        return [item.part for item in self.items if item.part]

    def context(self) -> str:
        """Текстовый блок с содержимым вложений — уходит в сообщение пользователя."""
        if not self.items and not self.errors:
            return ""

        blocks: list[str] = []
        for item in self.items:
            header = f"### {item.name} ({_human_kind(item.kind)}, {_human_size(item.size)})"
            body = item.text or f"Путь: {item.path}"
            if item.note:
                body = f"{body}\n[{item.note}]"
            blocks.append(f"{header}\nПуть: {item.path}\n{body}" if item.text else f"{header}\n{body}")

        if self.errors:
            blocks.append("### Не удалось приложить\n" + "\n".join(f"- {e}" for e in self.errors))

        # Про «уже приложено» сказано явно: иначе агент первым делом пытается
        # открыть файл инструментом, получает отказ песочницы (вложение часто
        # лежит вне рабочей папки) и тратит на это лишний шаг.
        return (
            "## Вложения пользователя\n"
            "Содержимое ниже уже прочитано — заново открывать эти файлы не нужно. "
            "Пути указаны на случай, если понадобится больше, чем показано.\n\n"
            + "\n\n".join(blocks)
        )

    def summary(self) -> str:
        """Короткое описание для журнала и заголовка сессии."""
        if not self.items:
            return ""
        return ", ".join(f"{item.name} ({_human_kind(item.kind)})" for item in self.items)


def collect(paths: list[str]) -> AttachmentSet:
    """Превращает список путей во вложения. Ошибки не роняют остальные файлы."""
    result = AttachmentSet()
    for raw in paths:
        path = Path(str(raw).strip()).expanduser()
        try:
            if not path.exists():
                result.errors.append(f"{path}: файл не найден")
                continue
            result.items.append(_build(path))
        except Exception as exc:  # noqa: BLE001 - одно вложение не должно ломать запрос
            logger.debug("Вложение %s не обработано", path, exc_info=True)
            result.errors.append(f"{path.name}: {exc}")
    return result


# ------------------------------------------------------------------ разбор


def _build(path: Path) -> Attachment:
    if path.is_dir():
        return _folder(path)

    size = path.stat().st_size
    suffix = path.suffix.lower()

    if suffix in IMAGE_SUFFIXES:
        return _media(path, size, "image", MAX_IMAGE_MB, "image_url")
    if suffix in VIDEO_SUFFIXES:
        return _media(path, size, "video", MAX_VIDEO_MB, "video_url")
    if suffix in AUDIO_SUFFIXES:
        return _media(path, size, "audio", MAX_VIDEO_MB, "input_audio")
    if suffix in ARCHIVE_SUFFIXES:
        return _archive(path, size)
    if is_document(path.name):
        return _document(path, size)
    return _plain_file(path, size)


def _media(path: Path, size: int, kind: str, limit_mb: float, part_type: str) -> Attachment:
    """Фото, видео и аудио уходят модели как есть — в виде data-URL."""
    if size > limit_mb * 1024 * 1024:
        return Attachment(
            path=path,
            kind=kind,
            size=size,
            note=(
                f"Файл больше лимита {limit_mb:.0f} МБ и не отправлен модели. "
                "Уменьшите его или сожмите."
            ),
        )

    mime = mimetypes.guess_type(path.name)[0] or f"{kind}/{path.suffix.lstrip('.')}"
    data_url = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"

    if part_type == "input_audio":
        # У аудио формат части другой: провайдеру нужен голый base64 и формат.
        part = {
            "type": "input_audio",
            "input_audio": {
                "data": data_url.split(",", 1)[1],
                "format": path.suffix.lstrip(".").lower(),
            },
        }
    else:
        part = {"type": part_type, part_type: {"url": data_url}}

    return Attachment(path=path, kind=kind, size=size, part=part)


def _document(path: Path, size: int) -> Attachment:
    """PDF, Excel, Word, CSV — разбираем в текст тем же кодом, что и в исследовании."""
    try:
        document = extract_document(path.read_bytes(), path.name, max_chars=MAX_TEXT_CHARS)
    except DocumentError as exc:
        return Attachment(path=path, kind="document", size=size, note=str(exc))

    note = f"показаны первые {MAX_TEXT_CHARS} символов" if document.truncated else ""
    return Attachment(path=path, kind="document", size=size, text=document.text, note=note)


def _archive(path: Path, size: int) -> Attachment:
    """Архив не распаковываем: показываем состав, а нужное агент достанет сам."""
    try:
        with zipfile.ZipFile(path) as bundle:
            names = bundle.namelist()
    except zipfile.BadZipFile as exc:
        return Attachment(path=path, kind="archive", size=size, note=f"архив повреждён: {exc}")

    listing = "\n".join(f"- {name}" for name in names[:MAX_LISTING])
    note = f"всего файлов: {len(names)}" if len(names) > MAX_LISTING else ""
    return Attachment(
        path=path,
        kind="archive",
        size=size,
        text=f"Содержимое архива:\n{listing}",
        note=note,
    )


def _folder(path: Path) -> Attachment:
    """Папка — это список файлов; читать их агент будет инструментами."""
    entries: list[str] = []
    total = 0
    for item in sorted(path.rglob("*")):
        if any(part.startswith(".") or part in ("__pycache__", "node_modules") for part in item.parts):
            continue
        total += 1
        if len(entries) < MAX_LISTING:
            marker = "/" if item.is_dir() else ""
            entries.append(f"- {item.relative_to(path)}{marker}")

    note = f"всего элементов: {total}" if total > len(entries) else ""
    body = "\n".join(entries) or "папка пуста"
    return Attachment(
        path=path,
        kind="folder",
        size=0,
        text=f"Состав папки:\n{body}",
        note=note,
    )


def _plain_file(path: Path, size: int) -> Attachment:
    """Обычный файл: текст читаем, двоичный только называем."""
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, ValueError):
        try:
            text = path.read_text(encoding="cp1251")
        except (UnicodeDecodeError, ValueError, OSError):
            return Attachment(
                path=path,
                kind="binary",
                size=size,
                note="двоичный файл — содержимое не читается как текст",
            )
    except OSError as exc:
        return Attachment(path=path, kind="binary", size=size, note=f"не прочитан: {exc}")

    truncated = len(text) > MAX_TEXT_CHARS
    return Attachment(
        path=path,
        kind="text",
        size=size,
        text=f"```\n{text[:MAX_TEXT_CHARS]}\n```",
        note=f"показаны первые {MAX_TEXT_CHARS} символов из {len(text)}" if truncated else "",
    )


# ------------------------------------------------------------ оформление


def _human_size(size: int) -> str:
    if size <= 0:
        return "—"
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} ГБ"


def _human_kind(kind: str) -> str:
    return {
        "image": "фото",
        "video": "видео",
        "audio": "аудио",
        "document": "документ",
        "archive": "архив",
        "folder": "папка",
        "text": "текст",
        "binary": "файл",
    }.get(kind, kind)
