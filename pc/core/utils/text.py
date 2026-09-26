"""Работа с текстом и кодировками (боль Windows)."""

from __future__ import annotations

import locale

#: Порядок попыток декодирования вывода консоли/файлов на Windows.
_ENCODINGS = ("utf-8", "utf-8-sig", "cp1251", "cp866", "latin-1")


def decode_bytes(data: bytes | None) -> str:
    """Декодирует байты, перебирая типичные для Windows кодировки."""
    if not data:
        return ""
    candidates = list(_ENCODINGS)
    preferred = locale.getpreferredencoding(False)
    if preferred and preferred.lower() not in candidates:
        candidates.insert(1, preferred)
    for enc in candidates:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def read_text_file(path, max_bytes: int | None = None) -> str:
    """Читает текстовый файл, устойчиво к кодировкам."""
    with open(path, "rb") as fh:
        raw = fh.read() if max_bytes is None else fh.read(max_bytes)
    return decode_bytes(raw)


def looks_binary(path) -> bool:
    """Грубая эвристика: есть ли в первых 4 КБ нулевые байты."""
    try:
        with open(path, "rb") as fh:
            return b"\0" in fh.read(4096)
    except OSError:
        return False
