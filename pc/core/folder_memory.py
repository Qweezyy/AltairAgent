"""Пер-папочная память агента: `.agent/memory.md` в рабочей папке.

Смысл — у КАЖДОЙ папки/чата своя память (в отличие от глобальной памяти о
пользователе). Заметки о проекте и извлечённые уроки хранятся рядом с проектом,
человекочитаемым markdown, и АВТОМАТИЧЕСКИ подмешиваются в системный промпт — так
агент всегда «помнит» контекст этой папки, не тратя вызовы на recall.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger("folder_memory")

#: Сколько символов памяти папки максимум кладём в промпт (свежие — в приоритете).
_PROMPT_LIMIT = 4000
#: Потолок размера файла: старое подрезаем, чтобы memory.md не разрастался бесконечно.
_FILE_LIMIT = 20_000
_HEADER = "# Память папки\n\nЗаметки агента об этом проекте/чате: решения, договорённости, уроки.\n"


class FolderMemory:
    """Читает и дополняет `<workspace>/.agent/memory.md`."""

    def __init__(self, workspace: Path | str) -> None:
        self.path = Path(workspace) / ".agent" / "memory.md"

    def read(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def append(self, text: str, category: str = "fact") -> bool:
        """Добавляет заметку (с датой и категорией). Дубликаты пропускает."""
        note = " ".join((text or "").split()).strip()
        if not note:
            return False
        existing = self.read()
        if note in existing:  # грубый дедуп: точная строка уже записана
            return False
        line = f"- [{category}] {datetime.now():%Y-%m-%d}: {note}\n"
        body = existing if existing.strip() else _HEADER
        body = body.rstrip("\n") + "\n" + line
        # Подрезаем старое, если файл слишком разросся (заголовок + свежий хвост).
        if len(body) > _FILE_LIMIT:
            tail = body[-(_FILE_LIMIT - len(_HEADER)) :]
            body = _HEADER + "\n…(старые заметки подрезаны)…\n" + tail[tail.find("\n") + 1 :]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(body, encoding="utf-8")
        except OSError:  # pragma: no cover
            logger.debug("Не удалось записать память папки", exc_info=True)
            return False
        return True

    def bullet_lines(self) -> list[str]:
        """Строки-пункты («- …») файла памяти, по порядку — для просмотра/правки."""
        return [ln for ln in self.read().splitlines() if ln.lstrip().startswith("- ")]

    def write_bullets(self, lines: list[str]) -> bool:
        """Перезаписывает файл: заголовок + переданные пункты (для remove/replace)."""
        body = _HEADER if not lines else _HEADER.rstrip("\n") + "\n" + "\n".join(
            ln.rstrip() for ln in lines
        ) + "\n"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(body, encoding="utf-8")
        except OSError:  # pragma: no cover
            logger.debug("Не удалось перезаписать память папки", exc_info=True)
            return False
        return True

    def prompt_section(self) -> str:
        """Раздел системного промпта с памятью этой папки (или пусто)."""
        content = self.read().strip()
        if not content:
            return ""
        if len(content) > _PROMPT_LIMIT:
            content = "…(most recent part shown)…\n" + content[-_PROMPT_LIMIT:]
        return (
            "<folder_memory>\nmemory.md of this workspace — context about the project:\n"
            + content
            + "\n</folder_memory>"
        )
