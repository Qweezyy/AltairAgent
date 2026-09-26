"""Общая настройка для релизных тестов.

Эти тесты НАМЕРЕННО живут вне `pc/tests` и `android/`: их пишет и держит
релиз-линия, а не дев-чаты. Смысл — независимая приёмка перед публикацией,
которую нельзя «подогнать» правкой обычных тестов.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PC_DIR = REPO_ROOT / "pc"

# Чтобы `import core...` работал так же, как при запуске из папки pc/.
if PC_DIR.is_dir():
    sys.path.insert(0, str(PC_DIR))
