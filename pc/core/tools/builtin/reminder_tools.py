"""Инструменты напоминаний ПК-агента: таймеры, условия, список, отмена.

Паритет с телефоном (set_reminder/watch_condition/list_reminders/cancel_reminder).
Хранятся в общем reminders.json; фоновый планировщик сервера (server/reminders.py)
опрашивает их, уведомляет пользователя и подмешивает срабатывания в промпт.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from core.i18n import tr
from core.reminders import OPERATORS, SIGNALS, Reminder, ReminderStore, new_id
from core.tools.base import Tool, ToolContext, ToolResult


def _store(ctx: ToolContext) -> ReminderStore:
    return ReminderStore(ctx.settings.data_dir)


def _parse_at(raw: str) -> float | None:
    """Разбирает 'HH:mm' (ближайшее сегодня/завтра) или 'yyyy-MM-dd HH:mm' → epoch."""
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
    if candidate.timestamp() <= now.timestamp():
        candidate = candidate.replace(day=candidate.day)
        candidate = datetime.fromtimestamp(candidate.timestamp() + 86400)
    return candidate.timestamp()


class SetReminderArgs(BaseModel):
    note: str = Field(description="Что напомнить")
    after_minutes: float | None = Field(default=None, description="Через сколько минут сработать")
    at: str = Field(default="", description="Абсолютное время: 'HH:mm' или 'yyyy-MM-dd HH:mm'")


class SetReminderTool(Tool):
    name = "set_reminder"
    description = (
        "Ставит напоминание на время. Укажи ЛИБО after_minutes (через сколько минут), ЛИБО at "
        "(абсолютно: 'HH:mm' сегодня/завтра или 'yyyy-MM-dd HH:mm'). Пользователь получит "
        "уведомление, а ты — контекст о срабатывании при следующем ответе."
    )
    Args = SetReminderArgs
    category = "edit"
    timeout = 15.0

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.remind", note=args.note)

    async def run(self, args: SetReminderArgs, ctx: ToolContext) -> str | ToolResult:
        note = args.note.strip()
        if not note:
            return ToolResult.fail("нужен текст напоминания (note)")
        if args.after_minutes is not None:
            if args.after_minutes <= 0:
                return ToolResult.fail("after_minutes должно быть > 0")
            fire_at = time.time() + args.after_minutes * 60
        elif args.at.strip():
            parsed = _parse_at(args.at)
            if parsed is None:
                return ToolResult.fail("не понял время 'at': нужно 'HH:mm' или 'yyyy-MM-dd HH:mm'")
            fire_at = parsed
        else:
            return ToolResult.fail("укажи after_minutes или at")
        reminder = Reminder(
            id=new_id(), kind="time", note=note, session_id=ctx.run_id, fire_at=fire_at
        )
        _store(ctx).add(reminder)
        when = datetime.fromtimestamp(fire_at).strftime("%Y-%m-%d %H:%M")
        return f"Напоминание поставлено на {when} (id={reminder.id}): {note}"


class WatchConditionArgs(BaseModel):
    note: str = Field(description="Что сообщить при срабатывании")
    signal: Literal["network", "battery", "charging", "phone_online"] = Field(
        description="network (online/offline) | battery (0..100) | charging (true/false) | phone_online (мост)"
    )
    op: Literal[">=", "<=", "==", "!="] = Field(description=">= | <= | == | !=")
    value: str = Field(description="Порог/значение: '80', 'true', 'online'…")


class WatchConditionTool(Tool):
    name = "watch_condition"
    description = (
        "Условное уведомление: сработает ОДИН раз, когда сигнал ПК выполнит условие. "
        "signal: network (online/offline) | battery (0..100, если есть датчик) | charging "
        "(true/false) | phone_online (подключён ли телефон по мосту). op: >= <= == !=. "
        "Пример: «сообщи, когда телефон подключится» → signal=phone_online, op===, value=true."
    )
    Args = WatchConditionArgs
    category = "edit"
    timeout = 15.0

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.watch", note=args.note, signal=args.signal, op=args.op, value=args.value)

    async def run(self, args: WatchConditionArgs, ctx: ToolContext) -> str | ToolResult:
        note = args.note.strip()
        if not note:
            return ToolResult.fail("нужен текст (note)")
        if args.signal not in SIGNALS:
            return ToolResult.fail(f"signal должен быть один из: {', '.join(SIGNALS)}")
        if args.op not in OPERATORS:
            return ToolResult.fail(f"op должен быть один из: {', '.join(OPERATORS)}")
        reminder = Reminder(
            id=new_id(), kind="condition", note=note, session_id=ctx.run_id,
            signal=args.signal, op=args.op, value=args.value.strip(),
        )
        _store(ctx).add(reminder)
        return f"Слежу: когда {args.signal} {args.op} {args.value} → «{note}» (id={reminder.id})."


class ListRemindersTool(Tool):
    name = "list_reminders"
    description = "Показывает активные (несработавшие) напоминания и условия с их id."
    category = "read"
    timeout = 15.0

    async def run(self, args: BaseModel, ctx: ToolContext) -> str:
        items = _store(ctx).active()
        if not items:
            return "Активных напоминаний нет."
        return "\n".join(f"• [{r.id}] {r.describe()}" for r in items)


class CancelReminderArgs(BaseModel):
    id: str = Field(description="id напоминания (из list_reminders)")


class CancelReminderTool(Tool):
    name = "cancel_reminder"
    description = "Отменяет (удаляет) напоминание/условие по его id."
    Args = CancelReminderArgs
    category = "edit"
    timeout = 15.0

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.remind_cancel", id=args.id)

    async def run(self, args: CancelReminderArgs, ctx: ToolContext) -> str | ToolResult:
        reminder_id = args.id.strip()
        if not reminder_id:
            return ToolResult.fail("нужен id")
        if _store(ctx).remove(reminder_id):
            return f"Отменено: {reminder_id}"
        return ToolResult.fail(f"напоминание {reminder_id} не найдено")
