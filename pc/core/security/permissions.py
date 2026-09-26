"""Режимы разрешений и запомненные решения пользователя.

Две независимые вещи:

  * **режим** решает, что происходит с инструментом по умолчанию — выполнить,
    спросить или запретить;
  * **запомненные разрешения** («всегда разрешать») перекрывают вопрос для
    конкретного инструмента: в текущем проекте или во всех сразу.

Режимы повторяют раскладку Claude Code, потому что она проверена практикой:
авто для повседневной работы, ручной для чувствительной, «правки без вопросов»
для итераций по коду, планирование для разбора чужого проекта и полное
отключение вопросов для песочницы.

Отличие одно и оно намеренное: в Claude Code решения в режиме «авто» принимает
вторая модель-классификатор. Здесь это правила (`Tool.auto_verdict`) — без
задержки на лишний запрос, без трат и с предсказуемым результатом.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from core.fs_atomic import safe_replace
from core.logging_setup import get_logger
from core.settings import Settings, get_settings

logger = get_logger("security.permissions")

#: Что делать с вызовом инструмента.
Decision = Literal["allow", "ask", "block"]

#: Категория инструмента: определяет, насколько он опасен.
Category = Literal["read", "edit", "execute", "network"]

MODES: dict[str, dict[str, object]] = {
    "auto": {
        "title": "Авто",
        "hint": "Решает по правилам: безопасное делает, спорное спрашивает",
        "allow": {"read"},
        "block": set(),
        # Особый режим: категории мало, решение принимает сам инструмент —
        # см. Tool.auto_verdict(). У Claude Code это делает вторая модель,
        # у нас — правила: без задержек, без трат и предсказуемо.
        "classify": True,
    },
    "manual": {
        "title": "Ручной",
        "hint": "Спрашивать перед любым изменением",
        "allow": {"read"},
        "block": set(),
    },
    "accept_edits": {
        "title": "Правки без вопросов",
        "hint": "Файлы правит сам, команды и сеть — спрашивает",
        "allow": {"read", "edit"},
        "block": set(),
    },
    "plan": {
        "title": "Планирование",
        "hint": "Только изучает проект, ничего не меняет",
        "allow": {"read"},
        "block": {"edit", "execute", "network"},
    },
    "bypass": {
        "title": "Без подтверждений",
        "hint": "Выполняет всё сразу — для песочницы и доверенных папок",
        "allow": {"read", "edit", "execute", "network"},
        "block": set(),
    },
}

#: Старые значения из .env, чтобы обновление не сломало настройки.
#: Старое значение auto означало «ничего не спрашивать» — это нынешний bypass.
LEGACY_MODES = {"auto": "bypass", "dangerous": "manual", "all": "manual"}


def mode_catalog() -> list[dict[str, str]]:
    """Список режимов для интерфейса."""
    return [
        {"id": key, "title": str(value["title"]), "hint": str(value["hint"])}
        for key, value in MODES.items()
    ]


def needs_classifier(mode: str) -> bool:
    """Режим, где решение принимает сам инструмент (Tool.auto_verdict)."""
    return bool((MODES.get(mode) or {}).get("classify"))


def decide(category: str, mode: str) -> Decision:
    """Что делать с инструментом этой категории в этом режиме."""
    rules = MODES.get(mode) or MODES["manual"]
    if category in rules["allow"]:  # type: ignore[operator]
        return "allow"
    if category in rules["block"]:  # type: ignore[operator]
        return "block"
    return "ask"


def block_reason(category: str, mode: str) -> str:
    if mode == "plan":
        return (
            "Включён режим планирования: изменять файлы, запускать команды и "
            "обращаться к сети нельзя. Изучи проект и предложи план — пользователь "
            "переключит режим, когда согласится."
        )
    return "Инструмент недоступен в текущем режиме разрешений."


@dataclass(slots=True)
class PermissionStore:
    """Запомненные решения «всегда разрешать».

    Хранится в папке приложения, а не проекта: список разрешений — это
    настройка пользователя, а не часть чужого репозитория.
    """

    settings: Settings | None = None
    _lock: threading.Lock = threading.Lock()

    @property
    def path(self) -> Path:
        settings = self.settings or get_settings()
        return settings.app_dir / "storage" / "permissions.json"

    def _load(self) -> dict:
        path = self.path
        if not path.exists():
            return {"global": [], "projects": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Не удалось прочитать %s: %s", path, exc)
            return {"global": [], "projects": {}}
        data.setdefault("global", [])
        data.setdefault("projects", {})
        return data

    def _save(self, data: dict) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            safe_replace(temp, path)
        except OSError as exc:  # pragma: no cover
            logger.warning("Не удалось сохранить разрешения: %s", exc)

    @staticmethod
    def _key(workspace: str | Path) -> str:
        return os.path.normcase(str(workspace))

    def is_allowed(self, tool: str, workspace: str | Path) -> bool:
        with self._lock:
            data = self._load()
        if tool in data["global"]:
            return True
        return tool in data["projects"].get(self._key(workspace), [])

    def allow_global(self, tool: str) -> None:
        with self._lock:
            data = self._load()
            if tool not in data["global"]:
                data["global"].append(tool)
            self._save(data)
        logger.info("Инструмент '%s' разрешён во всех проектах", tool)

    def allow_project(self, tool: str, workspace: str | Path) -> None:
        key = self._key(workspace)
        with self._lock:
            data = self._load()
            tools = data["projects"].setdefault(key, [])
            if tool not in tools:
                tools.append(tool)
            self._save(data)
        logger.info("Инструмент '%s' разрешён в проекте %s", tool, workspace)

    def revoke_all(self) -> None:
        """Сбрасывает все запомненные разрешения."""
        with self._lock:
            self._save({"global": [], "projects": {}})

    def summary(self, workspace: str | Path) -> dict[str, list[str]]:
        with self._lock:
            data = self._load()
        return {
            "global": sorted(data["global"]),
            "project": sorted(data["projects"].get(self._key(workspace), [])),
        }
