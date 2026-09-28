"""PC reminders: the store, conditions, repeats, the tools and the scheduler's pass."""

from __future__ import annotations

import threading
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

# ---------------------------------------------------------------- comparing


def test_compare_numeric_and_string():
    assert compare(85, ">=", "80") is True
    assert compare(70, ">=", "80") is False
    assert compare(20, "<=", "20") is True
    assert compare("online", "==", "online") is True
    assert compare("offline", "!=", "online") is True
    assert compare("true", "==", "TRUE") is True  # case does not matter
    assert compare("online", ">=", "online") is False  # >= means nothing for strings


def test_condition_met_missing_signal_is_false():
    r = Reminder(id="1", kind="condition", note="x", signal="battery", op=">=", value="80")
    assert condition_met(r, {}) is False  # the signal is not available → never fires
    assert condition_met(r, {"battery": 90}) is True


# ---------------------------------------------------------------- the store


def test_fire_due_marks_and_keeps_on_disk(tmp_path):
    store = ReminderStore(tmp_path)
    past = Reminder(id=new_id(), kind="time", note="past", session_id="s1", fire_at=time.time() - 10)
    future = Reminder(id=new_id(), kind="time", note="future", session_id="s1", fire_at=time.time() + 10_000)
    cond = Reminder(id=new_id(), kind="condition", note="net", session_id="s1", signal="network", op="==", value="online")
    for r in (past, future, cond):
        store.add(r)
    assert len(ReminderStore(tmp_path).all()) == 3  # a fresh store reads everything from disk

    fired = store.fire_due(time.time(), {"network": "online"})
    assert {r.id for r in fired} == {past.id, cond.id}
    assert {r.id for r in ReminderStore(tmp_path).active()} == {future.id}
    # Fired, not delivered yet: the scheduler hands these to their chats (again after a restart).
    assert {r.id for r in ReminderStore(tmp_path).undelivered()} == {past.id, cond.id}
    store.mark_delivered([past.id])
    assert {r.id for r in store.undelivered()} == {cond.id}
    # Fired once only.
    assert store.fire_due(time.time(), {"network": "online"}) == []


def test_conditions_wait_for_signals(tmp_path):
    store = ReminderStore(tmp_path)
    store.add(Reminder(id="c", kind="condition", note="n", signal="network", op="==", value="online"))
    assert store.fire_due(time.time(), None) == []           # a pass without signals skips them
    assert store.fire_due(time.time(), {"network": "offline"}) == []
    assert [r.id for r in store.fire_due(time.time(), {"network": "online"})] == ["c"]


def test_repeating_reminder_fires_once_for_missed_periods_and_expires(tmp_path):
    store = ReminderStore(tmp_path)
    now = time.time()
    store.add(Reminder(id="rep", kind="time", note="stretch", session_id="s", fire_at=now - 3.5 * 600,
                       repeat_every=600, expires_at=now + 900))
    fired = store.fire_due(now)
    assert len(fired) == 1 and fired[0].id != "rep" and fired[0].note == "stretch"  # a copy to deliver
    parent = store.get("rep")
    assert parent.active and parent.count == 1 and now < parent.fire_at <= now + 600
    # Past its expiry the repeat ends by itself.
    fired = store.fire_due(now + 1000)
    assert len(fired) == 1
    assert not store.get("rep").active
    assert store.fire_due(now + 5000) == []


def test_late_fire_says_so(tmp_path):
    r = Reminder(id="x", kind="time", note="call back", fire_at=time.time() - 7200, active=False,
                 fired_at=time.time())
    assert "2 h ago" in r.fired_text() and "call back" in r.fired_text()
    on_time = Reminder(id="y", kind="time", note="n", fire_at=time.time() - 5, fired_at=time.time())
    assert "ago" not in on_time.fired_text()


def test_concurrent_writers_do_not_lose_reminders(tmp_path):
    """The tools, the scheduler and other chats write the same file: none may overwrite
    another's change (the old store saved its own stale copy)."""
    def add(i: int) -> None:
        ReminderStore(tmp_path).add(Reminder(id=f"r{i}", kind="time", note=str(i), fire_at=time.time() + 60))

    threads = [threading.Thread(target=add, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ReminderStore(tmp_path).all()) == 20


def test_prompt_section_lists_this_chat_only(tmp_path):
    store = ReminderStore(tmp_path)
    store.add(Reminder(id="b", kind="condition", note="wait for net", session_id="s1", signal="network", op="==", value="online"))
    store.add(Reminder(id="c", kind="time", note="other chat", session_id="s2", fire_at=time.time() + 60))
    section = store.prompt_section("s1")
    assert "wait for net" in section and "other chat" not in section


def test_remove(tmp_path):
    store = ReminderStore(tmp_path)
    store.add(Reminder(id="x", kind="time", note="n", fire_at=time.time() + 100))
    assert store.remove("x") is True
    assert store.remove("missing") is False


def test_unchanged_pass_does_not_rewrite_the_file(tmp_path):
    store = ReminderStore(tmp_path)
    store.add(Reminder(id="x", kind="time", note="n", fire_at=time.time() + 100))
    before = store.path.stat().st_mtime_ns
    time.sleep(0.02)
    store.fire_due(time.time())
    assert store.path.stat().st_mtime_ns == before  # the scheduler looks every second


# ---------------------------------------------------------------- the tools


def _ctx(settings, sid="sess1") -> ToolContext:
    return ToolContext(settings=settings, run_id=sid)


@pytest.mark.asyncio
async def test_set_reminder_after_minutes(settings):
    out = await SetReminderTool().run(SetReminderTool.Args(note="call", after_minutes=30), _ctx(settings))
    assert "Reminder set" in out
    items = ReminderStore(settings.data_dir).active()
    assert len(items) == 1 and items[0].note == "call" and items[0].session_id == "sess1" and items[0].wake
    assert items[0].fire_at > time.time()


@pytest.mark.asyncio
async def test_set_reminder_repeat_and_quiet(settings):
    out = await SetReminderTool().run(
        SetReminderTool.Args(note="water", after_minutes=1, repeat_minutes=30, repeat_days=2, wake=False), _ctx(settings)
    )
    assert "every 30 min" in out and "user will see it" in out
    r = ReminderStore(settings.data_dir).active()[0]
    assert r.repeat_every == 1800 and not r.wake
    assert 1.9 * 86400 < r.expires_at - time.time() <= 2 * 86400
    bad = await SetReminderTool().run(SetReminderTool.Args(note="x", after_minutes=1, repeat_minutes=0.2), _ctx(settings))
    assert not bad.ok


@pytest.mark.asyncio
async def test_set_reminder_needs_time(settings):
    res = await SetReminderTool().run(SetReminderTool.Args(note="x"), _ctx(settings))
    assert res.ok is False


@pytest.mark.asyncio
async def test_set_reminder_at_hh_mm(settings):
    out = await SetReminderTool().run(SetReminderTool.Args(note="morning", at="08:30"), _ctx(settings))
    assert "08:30" in out


@pytest.mark.asyncio
async def test_watch_condition_and_list_and_cancel(settings):
    ctx = _ctx(settings)
    await WatchConditionTool().run(
        WatchConditionTool.Args(note="phone is back", signal="phone_online", op="==", value="true"), ctx
    )
    listed = await ListRemindersTool().run(ListRemindersTool.Args(), ctx)
    assert "phone is back" in listed
    rid = ReminderStore(settings.data_dir).active()[0].id
    out = await CancelReminderTool().run(CancelReminderTool.Args(id=rid), ctx)
    assert "Cancelled" in out
    assert not ReminderStore(settings.data_dir).active()


def test_watch_condition_rejects_bad_signal():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WatchConditionTool.Args(note="x", signal="temperature", op="==", value="1")


# ---------------------------------------------------------------- the scheduler


@pytest.mark.asyncio
async def test_scheduler_tick_fires_and_hands_to_the_hub(settings, monkeypatch):
    handed: list[list[str]] = []

    class Hub:
        async def dispatch(self, fired):
            handed.append(sorted(r.id for r in fired))

    app = types.SimpleNamespace(state=types.SimpleNamespace(settings=settings, chats=Hub(), connections=set()))
    store = ReminderStore(settings.data_dir)
    store.add(Reminder(id="due1", kind="time", note="now!", session_id="sess-A", fire_at=time.time() - 1))
    store.add(Reminder(id="later", kind="time", note="later", session_id="sess-A", fire_at=time.time() + 9999))

    async def fake_signals(_app):  # no network in tests
        return {"network": "offline", "phone_online": "false"}

    monkeypatch.setattr(sched, "current_signals", fake_signals)

    fired = await sched.tick(app)
    assert {r.id for r in fired} == {"due1"}
    assert handed == [["due1"]]
    assert {r.id for r in ReminderStore(settings.data_dir).active()} == {"later"}
    # Not delivered yet (the hub did not mark it): the next pass hands it again.
    await sched.tick(app)
    assert handed[-1] == ["due1"]


@pytest.mark.asyncio
async def test_phone_online_signal(settings):
    class Conn:
        phone_connected = True

    app = types.SimpleNamespace(state=types.SimpleNamespace(connections={Conn()}))
    assert sched._phone_online(app) == "true"
    app2 = types.SimpleNamespace(state=types.SimpleNamespace(connections=set()))
    assert sched._phone_online(app2) == "false"
