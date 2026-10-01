"""Единственное место, где живёт номер версии.

На него смотрят: интерфейс, проверка обновлений и сборка приложения. Если
версия задана в нескольких местах, они рано или поздно разъезжаются, и
обновление начинает предлагаться бесконечно.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _read_version() -> str:
    """Единая версия продукта из файла ``VERSION`` (корень репозитория в dev,
    рядом с exe в сборке). Так номер живёт в ОДНОМ месте на весь проект
    (ПК + Android), а не дублируется по конфигам."""
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "VERSION",   # dev: <repo>/VERSION (pc/core -> pc -> repo)
        here.parents[1] / "VERSION",   # запасной: pc/VERSION, если скопирован
    ]
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        candidates += [exe_dir / "VERSION", exe_dir / "_internal" / "VERSION"]
        # The one-file terminal command (bin/altair) unpacks its own copy here.
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "VERSION")
    for path in candidates:
        try:
            if path.is_file():
                value = path.read_text(encoding="utf-8").strip()
                if value:
                    return value
        except OSError:
            continue
    # VERSION missing means a broken build: say so instead of passing for a real release
    # (a stale real-looking number would also confuse the update check).
    return "0.0.0"


__version__ = _read_version()

#: Формат: MAJOR.MINOR.PATCH, только цифры и точки.
APP_NAME = "Altair"
APP_ID = "LocalAIAgent"  # техническое имя (пути данных, exe) — не трогаем


def version_tuple(value: str) -> tuple[int, ...]:
    """Версия как кортеж чисел для сравнения. Мусорные части отбрасываем."""
    parts: list[int] = []
    for chunk in str(value).strip().lstrip("v").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts or [0])


def is_newer(candidate: str, current: str = __version__) -> bool:
    """Строго новее ли `candidate`. Одинаковые версии обновлением не считаются."""
    return version_tuple(candidate) > version_tuple(current)
