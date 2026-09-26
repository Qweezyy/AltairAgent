"""Общие константы и помощники файловой системы.

Живут в utils, а не в инструментах: ими пользуются и `core/tools`, и
`core/codemap`. Если положить их в инструменты, получится циклический импорт
(codemap -> tools -> codemap) — слои должны зависеть только вниз.
"""

from __future__ import annotations

#: Папки, которые не нужно обходить: мусор сборки, зависимости, кеши.
IGNORED_DIRS = {
    ".git", ".svn", ".hg", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".idea", ".vscode", "dist", "build", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".next", ".nuxt", "target", "vendor", "coverage", ".tox", "site-packages",
}


def human_size(num: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if num < 1024 or unit == "ГБ":
            return f"{num:.0f} {unit}" if unit == "Б" else f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} ГБ"
