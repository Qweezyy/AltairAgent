"""Reminders, waits and watches of the PC agent: one durable store for everything that has
to wake the agent later.

Kinds:
  * time      — at a moment (`fire_at`), optionally again every `repeat_every` seconds until
                `expires_at`;
  * condition — once, when a PC signal meets a condition (`signal op value`,
                e.g. `network == online`);
  * wait      — a `wait_for` timer: the run sleeps on it in process; the record exists so a
                wait cut off by closing or updating the app still ends and wakes the chat;
  * job       — the end of a background command (`job`), for `watch_background` and
                `wait_for(background=...)`.

The store is one `reminders.json` in the data folder (shared by all chats, like on the
phone). The scheduler (server/reminders.py) fires what is due and hands it to the chat it
belongs to (server/chats.py): a busy chat gets it at the next step, an idle one — even closed
or not on screen — is woken with a new turn. A reminder stays "fired, not delivered" until
the chat has really taken it, so one that fired while the app was closed, or that the app
closed on before it reached the model, is delivered on the next start.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

from core.fs_atomic import atomic_write_text, path_lock
from core.logging_setup import get_logger

logger = get_logger("reminders")

#: Signals the PC can check for conditional reminders.
SIGNALS = ("network", "battery", "charging", "phone_online")
OPERATORS = (">=", "<=", "==", "!=")
KINDS = ("time", "condition", "wait", "job")
MAX_REMINDERS = 500
#: A repeating reminder stops by itself after this long (as recurring tasks do in Claude
#: Code): a forgotten "every 5 minutes" must not wake the agent for months.
REPEAT_TTL = 7 * 24 * 3600
#: The shortest repeat: more often than this is polling, not a reminder.
MIN_REPEAT = 60
#: Fired this much after its time = the app was closed or asleep: the chat is told so.
LATE_AFTER = 90
#: Fired and delivered records are kept this long (for the "what fired" history), then pruned.
KEEP_DONE = 3 * 24 * 3600

#: Waits a live run is sleeping on right now (ids). The scheduler leaves them alone: the run
#: itself goes on when the wait ends. After a restart the set is empty, and an unfinished
#: wait is the scheduler's to fire.
LIVE_WAITS: set[str] = set()


@dataclass(slots=True)
class Reminder:
    id: str
    kind: str  # see KINDS
    note: str
    session_id: str = ""
    created_at: float = field(default_factory=time.time)
    #: time / wait — when to fire (epoch seconds).
    fire_at: float = 0.0
    #: time — fire again every this many seconds (0 = once), until expires_at.
    repeat_every: float = 0.0
    expires_at: float = 0.0
    #: condition — what to check and how.
    signal: str = ""
    op: str = ""
    value: str = ""
    #: job — the background command's name (`value` holds its pid: the same name after a
    #: restart is another job).
    job: str = ""
    #: False = only show it to the user; True = the agent gets a turn to act on it.
    wake: bool = True
    #: active — not fired yet; after firing active=False.
    active: bool = True
    fired_at: float = 0.0
    #: What the scheduler saw when it fired (a job's exit, how late it was).
    outcome: str = ""
    #: The chat has taken the fired event (it is in the chat's history).
    delivered: bool = False
    #: How many times a repeating reminder has fired.
    count: int = 0

    def describe(self) -> str:
        """One line for lists and the system prompt."""
        if self.kind == "time":
            when = datetime.fromtimestamp(self.fire_at).strftime("%Y-%m-%d %H:%M")
            every = f", then every {_span(self.repeat_every)}" if self.repeat_every else ""
            return f"reminder at {when}{every}: {self.note}"
        if self.kind == "wait":
            when = datetime.fromtimestamp(self.fire_at).strftime("%H:%M:%S")
            return f"wait until {when}: {self.note or 'continue the task'}"
        if self.kind == "job":
            return f"when background job '{self.job}' ends: {self.note or 'check its result'}"
        return f"when {self.signal} {self.op} {self.value}: {self.note}"

    def fired_text(self) -> str:
        """What the chat is told when this fires."""
        if self.kind == "time":
            head = "Reminder you set"
        elif self.kind == "wait":
            head = "Your wait is over"
        elif self.kind == "job":
            head = f"Background job '{self.job}' {self.outcome or 'ended'}"
        else:
            head = f"Condition met ({self.signal} {self.op} {self.value})"
        text = f"{head}: {self.note}" if self.note else head
        late = self.fired_at - self.fire_at if self.fire_at else 0.0
        if self.kind in ("time", "wait") and late > LATE_AFTER:
            text += f" (due {_span(late)} ago: the app was closed or asleep then)"
        return text


def _span(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} min"
    if seconds < 36 * 3600:
        return f"{round(seconds / 3600, 1):g} h"
    return f"{round(seconds / 86400, 1):g} days"


_FIELDS = {f.name for f in fields(Reminder)}


class ReminderStore:
    """`reminders.json`: every change is a locked read-modify-write of the file, so the tools
    and the scheduler (and two chats) never overwrite each other's changes."""

    def __init__(self, base_dir: Path) -> None:
        self.path = Path(base_dir) / "reminders.json"

    # --- changes -------------------------------------------------------

    def add(self, reminder: Reminder) -> Reminder:
        def change(items: list[Reminder]) -> None:
            items.append(reminder)
            if len(items) > MAX_REMINDERS:
                # Drop the oldest finished first; active ones only when there is nothing else.
                done = [r for r in items if not r.active and r.delivered]
                for old in done[: len(items) - MAX_REMINDERS]:
                    items.remove(old)
                del items[: max(0, len(items) - MAX_REMINDERS)]

        self._update(change)
        return reminder

    def remove(self, reminder_id: str, session_id: str | None = None) -> bool:
        """Removes one (of this chat, when session_id is given)."""
        found = False

        def change(items: list[Reminder]) -> None:
            nonlocal found
            for r in list(items):
                if r.id == reminder_id and (session_id is None or r.session_id == session_id):
                    items.remove(r)
                    found = True

        self._update(change)
        return found

    def fire_due(
        self,
        now: float,
        signals: dict[str, object] | None = None,
        job_state: Callable[[str, str], tuple[bool, str]] | None = None,
    ) -> list[Reminder]:
        """Marks what is due as fired and returns it. A repeating reminder fires and is
        rescheduled (one fire for any number of missed periods, not a burst)."""
        fired: list[Reminder] = []

        def change(items: list[Reminder]) -> None:
            for r in items:
                if not r.active or r.id in LIVE_WAITS:
                    continue
                if r.expires_at and now >= r.expires_at and not r.repeat_every:
                    # A watch that outlived its limit ends quietly.
                    r.active, r.delivered = False, True
                    continue
                if r.kind in ("time", "wait"):
                    if not r.fire_at or now < r.fire_at:
                        continue
                    outcome = ""
                elif r.kind == "condition":
                    if signals is None or not condition_met(r, signals):
                        continue
                    outcome = ""
                elif r.kind == "job":
                    if job_state is None:
                        continue
                    finished, outcome = job_state(r.job, r.value)
                    if not finished:
                        continue
                else:
                    continue
                if r.kind == "time" and r.repeat_every and (not r.expires_at or now < r.expires_at):
                    # The repeat goes on: the fired copy is a separate record to deliver.
                    copy = Reminder(**{**asdict(r), "id": new_id(), "repeat_every": 0.0,
                                       "active": False, "fired_at": now, "delivered": False})
                    fired.append(copy)
                    r.count += 1
                    steps = int((now - r.fire_at) // r.repeat_every) + 1
                    r.fire_at += steps * r.repeat_every
                    if r.expires_at and r.fire_at >= r.expires_at:
                        r.active = False
                        r.delivered = True
                    continue
                r.active = False
                r.fired_at = now
                r.outcome = outcome
                fired.append(r)
            items.extend(f for f in fired if f not in items)

        if self.path.exists():
            self._update(change)
        return fired

    def mark_delivered(self, ids: set[str] | list[str]) -> None:
        wanted = set(ids)
        if not wanted:
            return

        def change(items: list[Reminder]) -> None:
            for r in items:
                if r.id in wanted:
                    r.delivered = True
                    r.active = False

        self._update(change)

    def prune(self, now: float | None = None) -> None:
        """Forgets delivered records older than KEEP_DONE."""
        now = now or time.time()

        def change(items: list[Reminder]) -> None:
            items[:] = [r for r in items if r.active or not r.delivered
                        or now - (r.fired_at or r.created_at) < KEEP_DONE]

        self._update(change)

    # --- reading -------------------------------------------------------

    def all(self) -> list[Reminder]:
        return self._load()

    def get(self, reminder_id: str) -> Reminder | None:
        return next((r for r in self._load() if r.id == reminder_id), None)

    def active(self, session_id: str | None = None) -> list[Reminder]:
        return [r for r in self._load() if r.active and (session_id is None or r.session_id == session_id)]

    def undelivered(self) -> list[Reminder]:
        """Fired, but not in their chat's history yet."""
        return [r for r in self._load() if not r.active and not r.delivered]

    def prompt_section(self, session_id: str) -> str:
        """For the system prompt: what this chat has scheduled (fired events reach the chat
        as messages, not through the prompt)."""
        active = [r for r in self.active(session_id) if r.kind != "wait"]
        if not active:
            return ""
        lines = ["ACTIVE REMINDERS AND WATCHES (you scheduled these; they wake you when due):"]
        lines += [f"- [{r.id}] {r.describe()}" for r in active]
        return "\n".join(lines)

    # --- disk ----------------------------------------------------------

    def _load(self) -> list[Reminder]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            logger.warning("reminders.json unreadable: %s", exc)
            return []
        items: list[Reminder] = []
        for entry in raw if isinstance(raw, list) else []:
            if not isinstance(entry, dict):
                continue
            try:
                items.append(Reminder(**{k: v for k, v in entry.items() if k in _FIELDS}))
            except (TypeError, ValueError):
                continue
        return items

    def _update(self, change: Callable[[list[Reminder]], None]) -> None:
        with path_lock(self.path):
            items = self._load()
            before = [asdict(r) for r in items]
            change(items)
            after = [asdict(r) for r in items]
            if after == before:
                return  # the scheduler looks every second: nothing changed, nothing written
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(after, ensure_ascii=False, indent=1)
            if not atomic_write_text(self.path, payload):
                logger.warning("reminders.json was not saved")


def condition_met(reminder: Reminder, signals: dict[str, object]) -> bool:
    """Whether the reminder's condition holds for the current signal values."""
    current = signals.get(reminder.signal)
    if current is None:  # the signal is not available on this PC: never fire
        return False
    return compare(current, reminder.op, reminder.value)


def compare(current: object, op: str, threshold: str) -> bool:
    """Numbers compare by value; the rest (online/offline, true/false) by equality."""
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
    return False  # >= / <= mean nothing for non-numeric signals


def new_id() -> str:
    return uuid.uuid4().hex[:8]
