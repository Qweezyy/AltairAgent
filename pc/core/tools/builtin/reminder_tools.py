"""The PC agent's reminder tools: timers (once or repeating), conditions, list, cancel.

Parity with the phone (set_reminder/watch_condition/list_reminders/cancel_reminder). They live
in the shared reminders.json (core/reminders.py); the server's scheduler fires them and wakes
the chat that set them — even if the user has another chat open, or the app was closed when
the time came (then on the next start).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from core.i18n import tr
from core.reminders import MIN_REPEAT, OPERATORS, REPEAT_TTL, SIGNALS, Reminder, ReminderStore, new_id
from core.tools.base import Tool, ToolContext, ToolResult


def _store(ctx: ToolContext) -> ReminderStore:
    return ReminderStore(ctx.settings.data_dir)


def _parse_at(raw: str) -> float | None:
    """'HH:mm' (the nearest today/tomorrow) or 'yyyy-MM-dd HH:mm' → epoch seconds."""
    text = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    try:
        parsed = datetime.strptime(text, "%H:%M").time()
    except ValueError:
        return None
    now = datetime.now()
    candidate = now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate.timestamp()


class SetReminderArgs(BaseModel):
    note: str = Field(description="What it is for: what you (or the user) should do then")
    after_minutes: float | None = Field(default=None, description="Fire in this many minutes")
    at: str = Field(default="", description="Or at a time: 'HH:mm' (today/tomorrow) or 'yyyy-MM-dd HH:mm'")
    repeat_minutes: float = Field(
        default=0, description="Repeat every this many minutes (min 1) after the first time; 0 = once"
    )
    repeat_days: float = Field(default=7, description="Stop repeating after this many days (max 7)")
    wake: bool = Field(
        default=True,
        description="true: you get a turn when it fires and continue the work; false: only show it to the user",
    )


class SetReminderTool(Tool):
    name = "set_reminder"
    description = (
        "Schedules a reminder: after_minutes OR at ('HH:mm' today/tomorrow or 'yyyy-MM-dd HH:mm'), "
        "optionally repeating (repeat_minutes, stops after repeat_days, max 7). When it fires you are "
        "woken in this chat with the note and continue the work — even if the user switched to another "
        "chat, the window is minimized, or the app was closed then (you are woken on the next start). "
        "wake=false only shows the note to the user. For a pause inside the current task use wait_for."
    )
    Args = SetReminderArgs
    category = "edit"
    timeout = 15.0

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.remind", note=args.note)

    async def run(self, args: SetReminderArgs, ctx: ToolContext) -> str | ToolResult:
        note = args.note.strip()
        if not note:
            return ToolResult.fail("the reminder needs a note")
        if args.after_minutes is not None:
            if args.after_minutes <= 0:
                return ToolResult.fail("after_minutes must be > 0")
            fire_at = time.time() + args.after_minutes * 60
        elif args.at.strip():
            parsed = _parse_at(args.at)
            if parsed is None:
                return ToolResult.fail("could not read 'at': use 'HH:mm' or 'yyyy-MM-dd HH:mm'")
            fire_at = parsed
        else:
            return ToolResult.fail("give after_minutes or at")
        repeat = max(0.0, args.repeat_minutes) * 60
        if repeat and repeat < MIN_REPEAT:
            return ToolResult.fail("repeat_minutes must be at least 1")
        ttl = min(max(args.repeat_days, 0.01) * 86400, REPEAT_TTL)
        reminder = Reminder(
            id=new_id(), kind="time", note=note, session_id=ctx.run_id, fire_at=fire_at,
            repeat_every=repeat, expires_at=time.time() + ttl if repeat else 0.0, wake=args.wake,
        )
        _store(ctx).add(reminder)
        when = datetime.fromtimestamp(fire_at).strftime("%Y-%m-%d %H:%M")
        how = "you will be woken in this chat" if args.wake else "the user will see it"
        every = ""
        if repeat:
            until = datetime.fromtimestamp(reminder.expires_at).strftime("%Y-%m-%d %H:%M")
            every = f", then every {args.repeat_minutes:g} min until {until}"
        return f"Reminder set for {when}{every} (id={reminder.id}); {how}: {note}"


class WatchConditionArgs(BaseModel):
    note: str = Field(description="What to do or say when it fires")
    signal: Literal["network", "battery", "charging", "phone_online"] = Field(
        description="network (online/offline) | battery (0..100) | charging (true/false) | phone_online (bridge)"
    )
    op: Literal[">=", "<=", "==", "!="] = Field(description=">= | <= | == | !=")
    value: str = Field(description="Threshold/value: '80', 'true', 'online'…")
    wake: bool = Field(default=True, description="true: you get a turn when it fires; false: only show it to the user")


class WatchConditionTool(Tool):
    name = "watch_condition"
    description = (
        "A conditional reminder: fires ONCE when a PC signal meets the condition, and wakes you in this "
        "chat. signal: network (online/offline) | battery (0..100, if there is a sensor) | charging "
        "(true/false) | phone_online (is the phone on the bridge). op: >= <= == !=. "
        "Example: \"tell me when the phone connects\" → signal=phone_online, op===, value=true."
    )
    Args = WatchConditionArgs
    category = "edit"
    timeout = 15.0

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.watch", note=args.note, signal=args.signal, op=args.op, value=args.value)

    async def run(self, args: WatchConditionArgs, ctx: ToolContext) -> str | ToolResult:
        note = args.note.strip()
        if not note:
            return ToolResult.fail("the watch needs a note")
        if args.signal not in SIGNALS:
            return ToolResult.fail(f"signal must be one of: {', '.join(SIGNALS)}")
        if args.op not in OPERATORS:
            return ToolResult.fail(f"op must be one of: {', '.join(OPERATORS)}")
        reminder = Reminder(
            id=new_id(), kind="condition", note=note, session_id=ctx.run_id,
            signal=args.signal, op=args.op, value=args.value.strip(), wake=args.wake,
        )
        _store(ctx).add(reminder)
        return f"Watching: when {args.signal} {args.op} {args.value} → \"{note}\" (id={reminder.id})."


class ListRemindersTool(Tool):
    name = "list_reminders"
    description = "Lists the active (not yet fired) reminders, watches and waits, with their ids."
    category = "read"
    timeout = 15.0

    async def run(self, args: BaseModel, ctx: ToolContext) -> str:
        items = _store(ctx).active()
        if not items:
            return "No active reminders."
        here = [r for r in items if r.session_id == ctx.run_id]
        other = len(items) - len(here)
        lines = [f"• [{r.id}] {r.describe()}" for r in here]
        if other:
            lines.append(f"(and {other} in other chats)")
        return "\n".join(lines) if lines else f"None in this chat ({other} in other chats)."


class CancelReminderArgs(BaseModel):
    id: str = Field(description="The reminder's id (from list_reminders)")


class CancelReminderTool(Tool):
    name = "cancel_reminder"
    description = "Cancels (deletes) a reminder, watch or repeating reminder by its id."
    Args = CancelReminderArgs
    category = "edit"
    timeout = 15.0

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.remind_cancel", id=args.id)

    async def run(self, args: CancelReminderArgs, ctx: ToolContext) -> str | ToolResult:
        reminder_id = args.id.strip()
        if not reminder_id:
            return ToolResult.fail("give the id")
        if _store(ctx).remove(reminder_id):
            return f"Cancelled: {reminder_id}"
        return ToolResult.fail(f"reminder {reminder_id} not found")
