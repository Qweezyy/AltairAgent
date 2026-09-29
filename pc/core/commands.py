"""Quick commands: saved prompt templates called as `/name`.

Typing the same long request ("run the tests and fix what fails…") ten times a day is friction.
A command hides the template behind a short name: `/tests` expands into the full prompt. A
template may hold a placeholder, `{{input}}` (or `{{ввод}}`), for what the user writes after the
command's name.

Stored as plain JSON in the data folder; useful defaults are seeded on the first start. The
built-in commands speak the interface's language: they are stored by a stable key and shown with
the name, description and template of the current language; the user's own commands stay as
written.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from core.fs_atomic import atomic_write_text
from core.logging_setup import get_logger

logger = get_logger("commands")

#: A command's name: letters (Cyrillic too), digits, '-' and '_', no spaces.
_NAME_RE = re.compile(r"^[\w\-]{1,40}$", re.UNICODE)
PLACEHOLDERS = ("{{input}}", "{{ввод}}")
PLACEHOLDER = PLACEHOLDERS[0]

#: The built-in commands in both languages: key → lang → (name, description, template).
BUILTINS: dict[str, dict[str, tuple[str, str, str]]] = {
    "tests": {
        "en": ("tests", "Run the tests and fix failures",
               "Run the tests (run_tests) and the linter (run_lint). If something fails, find the cause, fix it "
               "and run again until green (no more than 3–4 rounds). At the end, briefly: what you fixed and "
               "where things stand now."),
        "ru": ("тесты", "Прогнать тесты и починить падения",
               "Прогони тесты (run_tests) и линтер (run_lint). Если что-то падает — найди причину, исправь и "
               "запускай снова до зелёного (не больше 3–4 кругов). В конце кратко: что чинил и что сейчас со "
               "статусом."),
    },
    "review": {
        "en": ("review", "Review code or changes",
               "Review the current changes (or the given code): find bugs, security problems and places to "
               "simplify. For each point: file, line, the problem and how to fix it. Do not rewrite anything "
               "yet, the list first."),
        "ru": ("ревью", "Ревью кода/изменений",
               "Сделай ревью текущих изменений (или указанного кода): найди баги, проблемы безопасности и места "
               "для упрощения. По каждому пункту — файл, строка, суть проблемы и как исправить. Не переписывай "
               "сразу, сначала список."),
    },
    "explain": {
        "en": ("explain", "Explain simply and in detail", "Explain in detail and in simple words, with examples: {{input}}"),
        "ru": ("объясни", "Объяснить просто и подробно", "Объясни подробно и простыми словами, с примерами: {{ввод}}"),
    },
    "refactor": {
        "en": ("refactor", "Suggest a refactoring",
               "Suggest how to refactor {{input}} without changing its behavior: what to improve and why. The "
               "plan first; make the changes only after I confirm."),
        "ru": ("рефактор", "Предложить рефакторинг",
               "Предложи, как отрефакторить {{ввод}} без изменения поведения: что улучшить и почему. Сначала "
               "план, а правки — только когда я подтвержу."),
    },
    "commit": {
        "en": ("commit", "A commit message for the changes",
               "Write a meaningful commit message for the current changes in the Conventional Commits style. Do "
               "not commit yourself, give only the message."),
        "ru": ("коммит", "Сообщение коммита по изменениям",
               "Сформируй осмысленное сообщение коммита по текущим изменениям в стиле Conventional Commits. Сам "
               "не коммить — дай только текст сообщения."),
    },
    "document": {
        "en": ("document", "Write documentation",
               "Write or update the documentation for {{input}}: what it does, how to use it, arguments and "
               "return values, edge cases. Brief and to the point."),
        "ru": ("документируй", "Написать документацию",
               "Напиши или обнови документацию для {{ввод}}: что делает, как использовать, аргументы и "
               "возвращаемое, пограничные случаи. Кратко и по делу."),
    },
}

#: Any language's name of a built-in → its key (also recognises builtins stored before keys).
_BUILTIN_BY_NAME = {texts[0]: key for key, langs in BUILTINS.items() for texts in langs.values()}


@dataclass(slots=True)
class Command:
    name: str
    template: str
    description: str = ""
    builtin: bool = False
    #: The built-in's stable key ("" for the user's own commands).
    key: str = ""

    def expand(self, argument: str = "") -> str:
        """The template with the argument in its placeholder, or appended."""
        argument = argument.strip()
        for placeholder in PLACEHOLDERS:
            if placeholder in self.template:
                return self.template.replace(placeholder, argument)
        return f"{self.template}\n\n{argument}".strip() if argument else self.template

    def localized(self, lang: str) -> Command:
        """A built-in in the given language (the user's own commands as they are)."""
        texts = BUILTINS.get(self.key, {}).get(lang) if self.builtin else None
        if not texts:
            return self
        name, description, template = texts
        return Command(name=name, template=template, description=description, builtin=True, key=self.key)


def _defaults() -> list[Command]:
    return [Command(name=langs["en"][0], template=langs["en"][2], description=langs["en"][1], builtin=True, key=key)
            for key, langs in BUILTINS.items()]


class CommandStore:
    """The quick commands on disk (shared by all chats)."""

    def __init__(self, base_dir: Path) -> None:
        self.path = base_dir / "commands.json"
        self._commands: list[Command] = self._load()

    def all(self, lang: str | None = None) -> list[Command]:
        """The commands, the built-ins in `lang` when it is given."""
        return [c.localized(lang) for c in self._commands] if lang else list(self._commands)

    def save(self, command: Command) -> Command:
        """Creates or updates a command by name. Raises ValueError on a bad name."""
        name = command.name.strip().lstrip("/")
        if not _NAME_RE.match(name):
            raise ValueError("A command's name: letters, digits, '-' and '_', no spaces, up to 40 characters.")
        if not command.template.strip():
            raise ValueError("A command needs a template.")

        clean = Command(
            name=name,
            template=command.template.strip(),
            description=command.description.strip()[:120],
            builtin=False,
        )
        self._commands = [c for c in self._commands if not self._matches(c, name)]
        self._commands.append(clean)
        self._persist()
        return clean

    def delete(self, name: str) -> bool:
        name = name.lstrip("/")
        before = len(self._commands)
        self._commands = [c for c in self._commands if not self._matches(c, name)]
        if len(self._commands) != before:
            self._persist()
            return True
        return False

    def get(self, name: str, lang: str | None = None) -> Command | None:
        """By its name in any language (a built-in answers to both of its names)."""
        name = name.lstrip("/")
        found = next((c for c in self._commands if self._matches(c, name)), None)
        return found.localized(lang) if found and lang else found

    @staticmethod
    def _matches(command: Command, name: str) -> bool:
        if command.name == name:
            return True
        return command.builtin and _BUILTIN_BY_NAME.get(name) == command.key and bool(command.key)

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
                command = Command(**item)
            except TypeError:
                continue
            if command.builtin and not command.key:
                # Stored before the built-ins had keys: recognised by their (Russian) name.
                command.key = _BUILTIN_BY_NAME.get(command.name, "")
            commands.append(command)
        return commands or _defaults()

    def _persist(self) -> None:
        try:
            atomic_write_text(
                self.path, json.dumps({"commands": [asdict(c) for c in self._commands]}, ensure_ascii=False, indent=2)
            )
        except OSError:  # pragma: no cover
            logger.debug("quick commands were not saved", exc_info=True)
