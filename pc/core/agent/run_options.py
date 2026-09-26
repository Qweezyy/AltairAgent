"""Параметры одного запуска: вложения, веб-поиск, исследование, навыки.

Зачем отдельный объект: пользователь управляет поведением агента из меню
композера, и этих переключателей со временем станет больше. Держать их
россыпью аргументов у `run()` — верный способ однажды забыть один из них.

Правило для новых переключателей: добавьте поле, обработайте его в
`filter_registry` или `notes`, и опишите в меню (static/index.html). Ядро
агента при этом не меняется.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.attachments import AttachmentSet
from core.logging_setup import get_logger
from core.skills.manager import SkillManager
from core.tools.registry import ToolRegistry

logger = get_logger("agent.options")

#: Инструменты, которые ходят в интернет за информацией. Именно их отключает
#: режим «веб-поиск выключен» (http_request и download_file не трогаем: это
#: работа с конкретным адресом по прямой просьбе пользователя).
WEB_TOOLS = ("web_search", "fetch_url", "browse_page", "deep_research")

WEB_MODES = ("auto", "force", "off")


@dataclass(slots=True)
class RunOptions:
    """Что пользователь включил в меню композера перед запуском."""

    attachments: AttachmentSet = field(default_factory=AttachmentSet)
    #: auto — на усмотрение модели; force — искать обязательно; off — не искать.
    web_mode: str = "auto"
    #: Начать с deep_research, а не с обычного поиска.
    deep_research: bool = False
    #: Маршрутизация по моделям на ЭТОТ прогон: True/False перекрывают настройку,
    #: None — берём постоянную настройку model_routing.
    routing: bool | None = None
    #: Навыки, которые пользователь потребовал применить.
    skills: list[str] = field(default_factory=list)

    @classmethod
    def from_message(cls, data: dict[str, Any] | None) -> RunOptions:
        """Разбирает опции из сообщения интерфейса. Мусор молча игнорируется."""
        data = data or {}

        web_mode = str(data.get("web_mode") or "auto").lower()
        if web_mode not in WEB_MODES:
            logger.warning("Неизвестный режим веб-поиска '%s' — использую auto", web_mode)
            web_mode = "auto"

        skills = [str(name).strip() for name in (data.get("skills") or []) if str(name).strip()]

        from core.attachments import collect

        paths = [str(item) for item in (data.get("attachments") or [])]
        attachments = collect(paths) if paths else AttachmentSet()

        routing = data.get("routing")
        options = cls(
            attachments=attachments,
            web_mode=web_mode,
            deep_research=bool(data.get("deep_research")),
            routing=(bool(routing) if routing is not None else None),
            skills=skills,
        )

        # Исследование без интернета невозможно: молча оставить противоречие
        # хуже, чем поправить и сказать об этом в журнале.
        if options.deep_research and options.web_mode == "off":
            logger.info("Глубокое исследование включено — веб-поиск не отключаю")
            options.web_mode = "auto"
        return options

    # ------------------------------------------------------------------

    def filter_registry(self, registry: ToolRegistry) -> ToolRegistry:
        """Реестр инструментов для этого запуска.

        Возвращает исходный реестр, если ничего убирать не нужно — лишний
        клон только запутывает.
        """
        if self.web_mode != "off":
            return registry

        limited = registry.clone()
        for name in WEB_TOOLS:
            limited.remove(name)
        return limited

    def notes(self, skills: SkillManager | None = None) -> list[str]:
        """Указания агенту, вытекающие из выбранных переключателей."""
        notes: list[str] = []

        if self.web_mode == "force":
            notes.append(
                "Пользователь потребовал искать в интернете. Прежде чем отвечать, "
                "выполни поиск (web_search) и опирайся на найденные источники, "
                "даже если кажется, что ответ известен."
            )
        elif self.web_mode == "off":
            notes.append(
                "Пользователь отключил поиск в интернете. Инструменты поиска и чтения "
                "страниц недоступны. Отвечай по своим знаниям и файлам проекта; если "
                "данных не хватает — скажи об этом прямо, не выдумывай."
            )

        if self.deep_research:
            notes.append(
                "Пользователь включил глубокое исследование. Начни с вызова deep_research "
                "по своему вопросу и строй ответ на его отчёте, сохраняя ссылки на источники."
            )

        notes.extend(self._skill_notes(skills))
        return notes

    def _skill_notes(self, skills: SkillManager | None) -> list[str]:
        """Тексты навыков, которые пользователь потребовал применить.

        Навык вставляется целиком, а не упоминанием: если пользователь выбрал
        его вручную, лишний шаг «прочитай навык» — потерянное время.
        """
        if not self.skills or skills is None:
            return []

        notes: list[str] = []
        for name in self.skills:
            try:
                content = skills.read(name)
            except (FileNotFoundError, OSError) as exc:
                notes.append(f"Пользователь выбрал навык '{name}', но прочитать его не вышло: {exc}")
                continue
            notes.append(
                f"Пользователь потребовал применить навык «{name}». Следуй ему:\n\n{content}"
            )
        return notes

    def describe(self) -> str:
        """Короткое описание для журнала."""
        parts = []
        if self.attachments.items:
            parts.append(f"вложения: {len(self.attachments.items)}")
        if self.web_mode != "auto":
            parts.append(f"веб-поиск: {self.web_mode}")
        if self.deep_research:
            parts.append("глубокое исследование")
        if self.skills:
            parts.append(f"навыки: {', '.join(self.skills)}")
        return "; ".join(parts)
