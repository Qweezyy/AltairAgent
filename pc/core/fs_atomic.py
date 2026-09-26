"""Атомарная запись файла с надёжным фолбэком.

На части Windows-конфигураций папка данных приложения (``%LOCALAPPDATA%``) —
это junction на другой том. Тогда ``os.replace`` внутри одной папки всё равно
падает с ``WinError 17`` («невозможно переместить файл на другой диск»), и любое
сохранение состояния приложения (сессии, память, команды, пресеты, разрешения,
настройки, resume-состояние прогона) ломается в собранном exe.

``safe_replace`` пробует атомарную замену, а при отказе — прямую перезапись:
не атомарно, но для мелких конфигов/состояний надёжно и без потери данных.
"""

from __future__ import annotations

import os
from pathlib import Path


def safe_replace(temp: Path, path: Path) -> None:
    """Заменить ``path`` содержимым ``temp``.

    Сначала атомарно (``os.replace``); если ОС не позволяет (cross-device на
    junction-папке) — копируем байты напрямую и убираем временный файл.
    """
    try:
        os.replace(temp, path)
        return
    except OSError:
        pass
    try:
        path.write_bytes(Path(temp).read_bytes())
    finally:
        try:
            Path(temp).unlink()
        except OSError:
            pass
