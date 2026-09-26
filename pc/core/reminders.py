"""Напоминания и условные уведомления ПК-агента (паритет с телефоном).

Два вида:
  * time      — сработает в конкретный момент (`fire_at`, epoch-секунды);
  * condition — сработает один раз, когда сигнал ПК выполнит условие
                (`signal op value`, напр. `network == online`).

Хранилище — общий `reminders.json` в папке данных (одно на все чаты), как и на
телефоне. Фоновый планировщик сервера опрашивает его, помечает сработавшие и
(а) шлёт уведомление в подключённый чат, (б) подмешивает «сработавшие события» в
системный промпт этого чата — так у модели появляется контекст срабатывания.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger("reminders")

#: Сигналы, которые ПК умеет проверять для условных уведомлений.
SIGNALS = ("network", "battery", "charging", "phone_online")
OPERATORS = (">=", "<=", "==", "!=")
MAX_REMINDERS = 500


@dataclass(slots=True)
class Reminder:
    id: str
    kind: str  # "time" | "condition"
    note: str
    session_id: str = ""
    created_at: float = field(default_factory=time.time)
    #: time-напоминание — когда сработать (epoch-секунды).
    fire_at: float = 0.0
    #: condition-напоминание — что и как проверять.
    signal: str = ""
    op: str = ""
    value: str = ""
    #: active — ещё не сработало; после срабатывания active=False.
    active: bool = True
    fired_at: float = 0.0
    #: delivered — «сработавшее событие» уже показано модели в промпте.
    delivered: bool = False

    def describe(self) -> str:
        if self.kind == "time":
            when = datetime.fromtimestamp(self.fire_at).strftime("%Y-%m-%d %H:%M")
            return f"напоминание на {when}: {self.note}"
        return f"когда {self.signal} {self.op} {self.value} → {self.note}"


class ReminderStore:
    """Читает и пишет `reminders.json`. Простое, потокобезопасное на уровне файла."""

    def __init__(self, base_dir: Path) -> None:
        self.path = Path(base_dir) / "reminders.json"
        self._items: list[Reminder] = self._load()

    # --- изменение -----------------------------------------------------

    def add(self, reminder: Reminder) -> Reminder:
        self._items.append(reminder)
        if len(self._items) > MAX_REMINDERS:
            self._items = self._items[-MAX_REMINDERS:]
        self._save()
        return reminder

    def remove(self, reminder_id: str) -> bool:
        before = len(self._items)
        self._items = [r for r in self._items if r.id != reminder_id]
        if len(self._items) != before:
            self._save()
            return True
        return False

    def mark_fired(self, ids: set[str], now: float | None = None) -> None:
        now = now or time.time()
        touched = False
        for r in self._items:
            if r.id in ids and r.active:
                r.active = False
                r.fired_at = now
                touched = True
        if touched:
            self._save()

    def mark_delivered(self, session_id: str) -> None:
        """Помечает сработавшие события сессии как показанные модели."""
        touched = False
        for r in self._items:
            if r.session_id == session_id and not r.active and not r.delivered:
                r.delivered = True
                touched = True
        if touched:
            self._save()

    # --- чтение --------------------------------------------------------

    def all(self) -> list[Reminder]:
        return list(self._items)

    def active(self) -> list[Reminder]:
        return [r for r in self._items if r.active]

    def due(self, now: float, signals: dict[str, object]) -> list[Reminder]:
        """Активные напоминания, которые пора зажечь при данных сигналах/времени."""
        fired: list[Reminder] = []
        for r in self._items:
            if not r.active:
                continue
            if r.kind == "time" and r.fire_at and now >= r.fire_at:
                fired.append(r)
            elif r.kind == "condition" and condition_met(r, signals):
                fired.append(r)
        return fired

    def pending_fired(self, session_id: str) -> list[Reminder]:
        """Сработавшие, но ещё не показанные модели события этой сессии."""
        return [
            r for r in self._items
            if r.session_id == session_id and not r.active and not r.delivered
        ]

    def prompt_section(self, session_id: str) -> str:
        """Блок для системного промпта: сработавшие события + активные напоминания."""
        fired = self.pending_fired(session_id)
        active = [r for r in self._items if r.active and r.session_id == session_id]
        if not fired and not active:
            return ""
        lines: list[str] = []
        if fired:
            lines.append("FIRED EVENTS (take them into account now):")
            lines += [f"- {r.describe()}" for r in fired]
        if active:
            lines.append("ACTIVE REMINDERS (you scheduled these):")
            lines += [f"- [{r.id}] {r.describe()}" for r in active]
        return "\n".join(lines)

    # --- диск ----------------------------------------------------------

    def _load(self) -> list[Reminder]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items: list[Reminder] = []
        for entry in raw if isinstance(raw, list) else []:
            try:
                items.append(Reminder(**entry))
            except (TypeError, ValueError):
                continue
        return items

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps([asdict(r) for r in self._items], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:  # pragma: no cover
            logger.debug("Не удалось сохранить reminders.json", exc_info=True)


def condition_met(reminder: Reminder, signals: dict[str, object]) -> bool:
    """Выполнено ли условие напоминания при текущих значениях сигналов."""
    current = signals.get(reminder.signal)
    if current is None:  # сигнал недоступен на этом ПК — не срабатываем
        return False
    return compare(current, reminder.op, reminder.value)


def compare(current: object, op: str, threshold: str) -> bool:
    """Сравнивает текущее значение сигнала с порогом. Числа — по значению,
    остальное (online/offline, true/false) — по равенству."""
    try:
        c = float(current)  # type: ignore[arg-type]
        v = float(threshold)
        return {">=": c >= v, "<=": c <= v, "==": c == v, "!=": c != v}[op]
    except (ValueError, TypeError, KeyError):
        pass
    cs = str(current).strip().lower()
    vs = str(threshold).strip().lower()
    if op == "==":
        return cs == vs
    if op == "!=":
        return cs != vs
    return False  # >=/<= для нечисловых сигналов не имеют смысла


def new_id() -> str:
    return uuid.uuid4().hex[:8]
