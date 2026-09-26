"""Приём перетащенных в чат файлов: сохранение на диск для системы вложений.

Файлы кладутся в папку данных (не в проект), имя обеззараживается, а старые
загрузки вычищаются — иначе папка растёт бесконечно.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from core.logging_setup import get_logger

logger = get_logger("server.uploads")

#: Лимит на один файл. Совпадает с лимитом видео во вложениях.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
#: Загрузки старше этого удаляются при следующей загрузке.
KEEP_SECONDS = 7 * 24 * 3600
_CHUNK = 1024 * 1024


def _fix_mojibake(name: str) -> str:
    """Чинит имя, у которого UTF-8 байты были ошибочно раскодированы как latin-1.

    Multipart-парсер отдаёт `filename="портал.docx"` (UTF-8 в заголовке) как
    latin-1 → «ïîðòàë.docx». Приём: если строка ЦЕЛИКОМ кодируется в latin-1 и
    затем читается как UTF-8 — это была mojibake, возвращаем оригинал. Уже
    корректная кириллица в latin-1 не кодируется (ошибка) → её не трогаем.
    """
    try:
        recovered = name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name
    # Меняем только если реально стало «человеческим» (появились не-ASCII буквы).
    if recovered != name and any(ch.isalpha() and ord(ch) > 127 for ch in recovered):
        return recovered
    return name


def _safe_name(name: str) -> str:
    """Безопасное имя файла: без путей, с сохранением расширения."""
    name = _fix_mojibake(name)
    base = Path(name or "файл").name  # отсекает любые ../ и слэши
    cleaned = "".join(ch if ch.isalnum() or ch in " .-_()" else "_" for ch in base).strip()
    return (cleaned or "файл")[:120]


def _evict_old(folder: Path) -> None:
    now = time.time()
    for item in folder.glob("*"):
        try:
            if item.is_file() and now - item.stat().st_mtime > KEEP_SECONDS:
                item.unlink()
        except OSError:  # pragma: no cover
            continue


async def save_uploads(files: list[Any], data_dir: Path) -> tuple[list[str], list[str]]:
    """Сохраняет загруженные файлы. Возвращает (пути, ошибки)."""
    folder = data_dir / "attachments"
    folder.mkdir(parents=True, exist_ok=True)
    _evict_old(folder)

    paths: list[str] = []
    errors: list[str] = []

    for upload in files:
        name = _safe_name(getattr(upload, "filename", "") or "файл")
        # Префикс не даёт двум одинаковым именам затереть друг друга.
        target = folder / f"{uuid.uuid4().hex[:8]}_{name}"
        total = 0
        try:
            with target.open("wb") as fh:
                while True:
                    chunk = await upload.read(_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise ValueError(f"больше лимита {MAX_UPLOAD_BYTES // 1024 // 1024} МБ")
                    fh.write(chunk)
        except ValueError as exc:
            target.unlink(missing_ok=True)
            errors.append(f"{name}: {exc}")
            continue
        except OSError as exc:
            target.unlink(missing_ok=True)
            errors.append(f"{name}: не удалось сохранить ({exc})")
            continue
        finally:
            await upload.close()

        if total == 0:
            target.unlink(missing_ok=True)
            errors.append(f"{name}: пустой файл")
            continue
        paths.append(str(target))

    return paths, errors
