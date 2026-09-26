"""Напоминания ПК: хранилище, условия, инструменты, планировщик."""

from __future__ import annotations

import time
import types

import pytest

import server.reminders as sched
from core.reminders import Reminder, ReminderStore, compare, condition_met, new_id
from core.tools.base import ToolContext
from core.tools.builtin.reminder_tools import (
    CancelReminderTool,
    ListRemindersTool,
    SetReminderTool,
    WatchConditionTool,
)

# ---------------------------------------------------------------- сравнение


def test_compare_numeric_and_string():
    assert compare(85, ">=", "80") is True
    assert compare(70, ">=", "80") is False
    assert compare(20, "<=", "20") is True
    assert compare("online", "==", "online") is True
    assert compare("offline", "!=", "online") is True
    assert compare("true", "==", "TRUE") is True  # без учёта регистра
    assert compare("online", ">=", "online") is False  # >= для строк — бессмысленно


def test_condition_met_missing_signal_is_false():
    r = Reminder(id="1", kind="condition", note="x", signal="battery", op=">=", value="80")
    assert condition_met(r, {}) is False  # сигнал недоступен → не срабатывает
    assert condition_met(r, {"battery": 90}) is True


# ---------------------------------------------------------------- хранилище


def test_store_roundtrip_and_due(tmp_path):
    store = ReminderStore(tmp_path)
    past = Reminder(id=new_id(), kind="time", note="прошлое", session_id="s1", fire_at=time.time() - 10)
    future = Reminder(id=new_id(), kind="time", note="будущее", session_id="s1", fire_at=time.time() + 10_000)
    cond = Reminder(id=new_id(), kind="condition", note="сеть", session_id="s1", signal="network", op="==", value="online")
    store.add(past)
    store.add(future)
    store.add(cond)

    # Переоткрытие читает всё с диска.
    assert len(ReminderStore(tmp_path).all()) == 3

    due = store.due(time.time(), {"network": "online"})
    ids = {r.id for r in due}
    assert past.id in ids and cond.id in ids and future.id not in ids

    store.mark_fired({r.id for r in due})
    assert {r.id for r in store.active()} == {future.id}
    # Сработавшее осело на диске.
    assert {r.id for r in ReminderStore(tmp_path).active()} == {future.id}


def test_store_prompt_section_and_delivery(tmp_path):
    store = ReminderStore(tmp_path)
    fired = Reminder(id="a", kind="time", note="выпить воды", session_id="s1", fire_at=1.0, active=False, fired_at=2.0)
    active = Reminder(id="b", kind="condition", note="ждём сеть", session_id="s1", signal="network", op="==", value="online")
    other = Reminder(id="c", kind="time", note="чужой чат", session_id="s2", fire_at=1.0, active=False)
    for r in (fired, active, other):
        store.add(r)

    section = store.prompt_section("s1")
    assert "FIRED EVENTS" in section and "выпить воды" in section
    assert "ACTIVE REMINDERS" in section and "ждём сеть" in section
    assert "чужой чат" not in section  # только своя сессия

    store.mark_delivered("s1")
    # После доставки сработавшее из промпта уходит, активное остаётся.
    section2 = ReminderStore(tmp_path).prompt_section("s1")
    assert "FIRED EVENTS" not in section2
    assert "ждём сеть" in section2


def test_remove(tmp_path):
    store = ReminderStore(tmp_path)
    store.add(Reminder(id="x", kind="time", note="n", fire_at=time.time() + 100))
    assert store.remove("x") is True
    assert store.remove("нет") is False


# ---------------------------------------------------------------- инструменты


def _ctx(settings, sid="sess1") -> ToolContext:
    return ToolContext(settings=settings, run_id=sid)


@pytest.mark.asyncio
async def test_set_reminder_after_minutes(settings):
    ctx = _ctx(settings)
    out = await SetReminderTool().run(SetReminderTool.Args(note="позвонить", after_minutes=30), ctx)
    assert "Напоминание поставлено" in out
    items = ReminderStore(settings.data_dir).active()
    assert len(items) == 1 and items[0].note == "позвонить" and items[0].session_id == "sess1"
    assert items[0].fire_at > time.time()


@pytest.mark.asyncio
async def test_set_reminder_needs_time(settings):
    res = await SetReminderTool().run(SetReminderTool.Args(note="x"), _ctx(settings))
    assert res.ok is False


@pytest.mark.asyncio
async def test_set_reminder_at_hh_mm(settings):
    out = await SetReminderTool().run(SetReminderTool.Args(note="утро", at="08:30"), _ctx(settings))
    assert "08:30" in out


@pytest.mark.asyncio
async def test_watch_condition_and_list_and_cancel(settings):
    ctx = _ctx(settings)
    await WatchConditionTool().run(
        WatchConditionTool.Args(note="телефон на связи", signal="phone_online", op="==", value="true"), ctx
    )
    listed = await ListRemindersTool().run(ListRemindersTool.Args(), ctx)
    assert "телефон на связи" in listed
    # id из списка → отмена.
    rid = ReminderStore(settings.data_dir).active()[0].id
    out = await CancelReminderTool().run(CancelReminderTool.Args(id=rid), ctx)
    assert "Отменено" in out
    assert not ReminderStore(settings.data_dir).active()


def test_watch_condition_rejects_bad_signal():
    # pydantic Literal не пропустит неизвестный сигнал.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WatchConditionTool.Args(note="x", signal="temperature", op="==", value="1")


# ---------------------------------------------------------------- планировщик


@pytest.mark.asyncio
async def test_scheduler_tick_fires_and_notifies(settings, monkeypatch):
    class FakeConn:
        def __init__(self, sid):
            self.session = types.SimpleNamespace(id=sid)
            self.phone_connected = False
            self.sent = []

        async def send(self, payload):
            self.sent.append(payload)

    conn = FakeConn("sess-A")
    other = FakeConn("sess-B")
    app = types.SimpleNamespace(
        state=types.SimpleNamespace(settings=settings, connections={conn, other})
    )

    store = ReminderStore(settings.data_dir)
    store.add(Reminder(id="due1", kind="time", note="пора!", session_id="sess-A", fire_at=time.time() - 1))
    store.add(Reminder(id="later", kind="time", note="потом", session_id="sess-A", fire_at=time.time() + 9999))

    # Не ходим в сеть в тесте.
    async def fake_signals(_app):
        return {"network": "offline", "phone_online": "false"}

    monkeypatch.setattr(sched, "current_signals", fake_signals)

    due = await sched.tick(app)
    assert {r.id for r in due} == {"due1"}
    # Уведомление ушло только в нужный чат.
    assert conn.sent and conn.sent[0]["id"] == "due1" and conn.sent[0]["type"] == "reminder.fired"
    assert not other.sent
    # На диске напоминание помечено сработавшим.
    assert {r.id for r in ReminderStore(settings.data_dir).active()} == {"later"}


@pytest.mark.asyncio
async def test_phone_online_signal(settings):
    class Conn:
        phone_connected = True

    app = types.SimpleNamespace(state=types.SimpleNamespace(connections={Conn()}))
    assert sched._phone_online(app) == "true"
    app2 = types.SimpleNamespace(state=types.SimpleNamespace(connections=set()))
    assert sched._phone_online(app2) == "false"
