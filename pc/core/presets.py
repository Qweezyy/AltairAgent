"""Профили-пресеты композера: сценарные связки настроек в один клик.

Каждый раз перенастраивать модель, режим подтверждений, веб-поиск и навыки под
задачу — трение. Пресет собирает всё это под именем («Кодинг», «Учёба», «Быт»),
и применяется одним выбором. Хранятся в папке данных обычным JSON.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core.fs_atomic import safe_replace
from core.logging_setup import get_logger
from core.security.permissions import MODES

logger = get_logger("presets")

WEB_MODES = ("auto", "force", "off")


@dataclass(slots=True)
class Preset:
    name: str
    model: str = ""  # пусто — не менять текущую модель
    approval_mode: str = "manual"
    web_mode: str = "auto"
    deep_research: bool = False
    skills: list[str] = field(default_factory=list)
    builtin: bool = False

    def sanitized(self) -> Preset:
        """Приводит значения к допустимым, чтобы битый пресет не сломал UI."""
        return Preset(
            name=self.name.strip()[:60] or "Пресет",
            model=self.model.strip(),
            approval_mode=self.approval_mode if self.approval_mode in MODES else "manual",
            web_mode=self.web_mode if self.web_mode in WEB_MODES else "auto",
            deep_research=bool(self.deep_research),
            skills=[str(s).strip() for s in self.skills if str(s).strip()],
            builtin=bool(self.builtin),
        )


#: Готовые пресеты при первом запуске. Ссылаются на встроенные навыки.
def _defaults() -> list[Preset]:
    return [
        Preset(
            name="Кодинг",
            approval_mode="accept_edits",
            web_mode="auto",
            skills=["python_expert"],
            builtin=True,
        ),
        Preset(
            name="Учёба",
            approval_mode="manual",
            web_mode="auto",
            skills=["socratic_examiner"],
            builtin=True,
        ),
        Preset(
            name="Быт",
            approval_mode="manual",
            web_mode="auto",
            skills=["daily_life"],
            builtin=True,
        ),
    ]


class PresetStore:
    """Хранилище пресетов на диске (общее для всех чатов)."""

    def __init__(self, base_dir: Path) -> None:
        self.path = base_dir / "presets.json"
        self._presets: list[Preset] = self._load()

    def all(self) -> list[Preset]:
        return list(self._presets)

    def save(self, preset: Preset) -> Preset:
        """Создаёт или обновляет пресет по имени (регистр учитывается)."""
        clean = preset.sanitized()
        clean.builtin = False  # сохранённый пользователем — уже не встроенный
        self._presets = [p for p in self._presets if p.name != clean.name]
        self._presets.append(clean)
        self._persist()
        return clean

    def delete(self, name: str) -> bool:
        before = len(self._presets)
        self._presets = [p for p in self._presets if p.name != name]
        if len(self._presets) != before:
            self._persist()
            return True
        return False

    # ------------------------------------------------------------------

    def _load(self) -> list[Preset]:
        if not self.path.exists():
            presets = _defaults()
            self._presets = presets
            self._persist()
            return presets
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _defaults()
        presets = []
        for item in data.get("presets", []) if isinstance(data, dict) else []:
            try:
                presets.append(Preset(**item).sanitized())
            except TypeError:
                continue
        return presets or _defaults()

    def _persist(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".json.tmp")
            temp.write_text(
                json.dumps({"presets": [asdict(p) for p in self._presets]}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            safe_replace(temp, self.path)
        except OSError:  # pragma: no cover
            logger.debug("Не удалось сохранить пресеты", exc_info=True)
