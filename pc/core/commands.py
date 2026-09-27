"""Быстрые команды: сохранённые шаблоны промптов, вызываемые через «/имя».

Печатать один и тот же длинный запрос («прогони тесты и почини падения…») по
десять раз в день — трение. Команда прячет шаблон за коротким именем: `/тесты`
разворачивается в полный промпт. В шаблоне может быть плейсхолдер `{{ввод}}` —
туда подставляется то, что пользователь дописал после имени команды.

Хранятся в папке данных обычным JSON; при первом запуске сидятся полезные
дефолты.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from core.fs_atomic import atomic_write_text
from core.logging_setup import get_logger

logger = get_logger("commands")

#: Имя команды: буквы (вкл. кириллицу), цифры, дефис и подчёркивание, без пробелов.
_NAME_RE = re.compile(r"^[\w\-]{1,40}$", re.UNICODE)
PLACEHOLDER = "{{ввод}}"


@dataclass(slots=True)
class Command:
    name: str
    template: str
    description: str = ""
    builtin: bool = False

    def expand(self, argument: str = "") -> str:
        """Разворачивает шаблон. Аргумент идёт в плейсхолдер или дописывается."""
        argument = argument.strip()
        if PLACEHOLDER in self.template:
            return self.template.replace(PLACEHOLDER, argument)
        return f"{self.template}\n\n{argument}".strip() if argument else self.template


def _defaults() -> list[Command]:
    return [
        Command(
            name="тесты",
            description="Прогнать тесты и починить падения",
            template=(
                "Прогони тесты (run_tests) и линтер (run_lint). Если что-то падает — "
                "найди причину, исправь и запускай снова до зелёного (не больше 3–4 кругов). "
                "В конце кратко: что чинил и что сейчас со статусом."
            ),
            builtin=True,
        ),
        Command(
            name="ревью",
            description="Ревью кода/изменений",
            template=(
                "Сделай ревью текущих изменений (или указанного кода): найди баги, проблемы "
                "безопасности и места для упрощения. По каждому пункту — файл, строка, суть "
                "проблемы и как исправить. Не переписывай сразу, сначала список."
            ),
            builtin=True,
        ),
        Command(
            name="объясни",
            description="Объяснить просто и подробно",
            template="Объясни подробно и простыми словами, с примерами: {{ввод}}",
            builtin=True,
        ),
        Command(
            name="рефактор",
            description="Предложить рефакторинг",
            template=(
                "Предложи, как отрефакторить {{ввод}} без изменения поведения: что улучшить "
                "и почему. Сначала план, а правки — только когда я подтвержу."
            ),
            builtin=True,
        ),
        Command(
            name="коммит",
            description="Сообщение коммита по изменениям",
            template=(
                "Сформируй осмысленное сообщение коммита по текущим изменениям в стиле "
                "Conventional Commits. Сам не коммить — дай только текст сообщения."
            ),
            builtin=True,
        ),
        Command(
            name="документируй",
            description="Написать документацию",
            template=(
                "Напиши или обнови документацию для {{ввод}}: что делает, как использовать, "
                "аргументы и возвращаемое, пограничные случаи. Кратко и по делу."
            ),
            builtin=True,
        ),
    ]


class CommandStore:
    """Хранилище быстрых команд на диске (общее для всех чатов)."""

    def __init__(self, base_dir: Path) -> None:
        self.path = base_dir / "commands.json"
        self._commands: list[Command] = self._load()

    def all(self) -> list[Command]:
        return list(self._commands)

    def save(self, command: Command) -> Command:
        """Создаёт или обновляет команду по имени. Кидает ValueError на кривом имени."""
        name = command.name.strip().lstrip("/")
        if not _NAME_RE.match(name):
            raise ValueError("Имя команды: буквы, цифры, '-' и '_', без пробелов, до 40 символов.")
        if not command.template.strip():
            raise ValueError("У команды должен быть шаблон.")

        clean = Command(
            name=name,
            template=command.template.strip(),
            description=command.description.strip()[:120],
            builtin=False,
        )
        self._commands = [c for c in self._commands if c.name != name]
        self._commands.append(clean)
        self._persist()
        return clean

    def delete(self, name: str) -> bool:
        name = name.lstrip("/")
        before = len(self._commands)
        self._commands = [c for c in self._commands if c.name != name]
        if len(self._commands) != before:
            self._persist()
            return True
        return False

    def get(self, name: str) -> Command | None:
        name = name.lstrip("/")
        return next((c for c in self._commands if c.name == name), None)

    # ------------------------------------------------------------------

    def _load(self) -> list[Command]:
        if not self.path.exists():
            commands = _defaults()
            self._commands = commands
            self._persist()
            return commands
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _defaults()
        commands = []
        for item in data.get("commands", []) if isinstance(data, dict) else []:
            try:
                commands.append(Command(**item))
            except TypeError:
                continue
        return commands or _defaults()

    def _persist(self) -> None:
        try:
            atomic_write_text(
                self.path, json.dumps({"commands": [asdict(c) for c in self._commands]}, ensure_ascii=False, indent=2)
            )
        except OSError:  # pragma: no cover
            logger.debug("Не удалось сохранить команды", exc_info=True)
