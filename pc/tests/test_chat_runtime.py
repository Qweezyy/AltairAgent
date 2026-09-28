"""Chats live apart from their windows, and reminders/waits wake them wherever they are.

Covers what used to go wrong or not exist:
- switching chats mid-run wrote the run into the chat on screen and lost it from its own;
- a fired reminder reached the model only when the user next wrote in that chat, and only
  while the app ran; a wait or a watch died with the task or the app.
"""

from __future__ import annotations

import asyncio
import threading
import time
import types

import pytest
from fastapi.testclient import TestClient

import server.chats as chats_module
import server.reminders as sched
from core.agent.run_state import RunStateStore
from core.agent.session import Session
from core.agent.storage import SessionStore
from core.llm.base import AssistantTurn
from core.reminders import LIVE_WAITS, Reminder, ReminderStore, new_id
from core.tools.base import Tool
from core.tools.builtin.background_tools import WaitForTool
from core.tools.registry import ToolRegistry
from server.chats import WAKE_HEAD, ChatHub
from tests.fakes import ScriptedLLM, tool_call


class _Viewer:
    """A window's socket as the hub sees it."""

    phone_connected = False

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.outbox = asyncio.Queue()

    async def send(self, payload: dict) -> None:
        self.sent.append(payload)


class _GateLLM(ScriptedLLM):
    """Answers only when the gate opens (a threading.Event: the app runs in another thread)."""

    def __init__(self, turns, gate: threading.Event) -> None:
        super().__init__(turns)
        self.gate = gate

    async def complete(self, messages, **kw):
        await asyncio.to_thread(self.gate.wait, 10)
        return await super().complete(messages, **kw)


class _Danger(Tool):
    name = "danger_tool"
    description = "Does something that needs approval."
    category = "execute"
    dangerous = True

    async def run(self, args, ctx):
        return "done the dangerous thing"


@pytest.fixture()
def env(monkeypatch, settings):
    import core.settings as settings_module

    settings.approval_mode = "bypass"
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)
    LIVE_WAITS.clear()
    return settings


def _use(monkeypatch, llm) -> None:
    monkeypatch.setattr(chats_module, "build_llm_client", lambda model=None, **kw: llm)


def _app(hub, settings):
    return types.SimpleNamespace(state=types.SimpleNamespace(settings=settings, chats=hub, connections=hub.connections))


async def _stored_chat(hub, registry, settings, llm_answers=("hello",)):
    """A chat with one finished exchange, stored and not open anywhere."""
    chat = hub.new_chat(registry, Session(workspace=str(settings.workspace)), settings)
    chat.launch("first task", None)
    await chat.run_task
    hub._forget_if_idle(chat)
    assert chat.session.id not in hub.chats
    return chat.session.id


async def _settle(hub, session_id, timeout=5.0):
    """Waits until the chat's woken run has finished."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        chat = hub.chats.get(session_id)
        if chat is not None and chat.run_task is not None:
            await asyncio.gather(chat.run_task, return_exceptions=True)
            return chat
        await asyncio.sleep(0.02)
    raise AssertionError("the chat was not woken")


# ---------------------------------------------------------------- waking a chat


async def test_a_reminder_wakes_a_chat_nobody_has_open(env, monkeypatch):
    llm = ScriptedLLM([AssistantTurn(content="hello"), AssistantTurn(content="the build is green")])
    _use(monkeypatch, llm)
    registry = ToolRegistry([])
    hub = ChatHub(registry)
    other_window = _Viewer()
    hub.connections.add(other_window)
    sid = await _stored_chat(hub, registry, env)

    rid = new_id()
    ReminderStore(env.data_dir).add(Reminder(id=rid, kind="time", note="check the build", session_id=sid,
                                             fire_at=time.time() - 1))
    await sched.tick(_app(hub, env))
    chat = await _settle(hub, sid)

    last = llm.calls[-1]["messages"]
    woke = next(m for m in last if m.get("role") == "user" and WAKE_HEAD in str(m.get("content")))
    assert "check the build" in woke["content"]
    stored = SessionStore(settings=env).load(sid)
    assert any(e.get("kind") == "wake" and "check the build" in e["text"] for e in stored.timeline)
    assert any(e.get("kind") == "answer" and e["text"] == "the build is green" for e in stored.timeline)
    assert ReminderStore(env.data_dir).get(rid).delivered
    # The wake-up is not the user's message: "retry"/"rewind" by turn number skip it.
    wake_msgs = [m for m in stored.messages if m.get("role") == "user" and m.get("_wake")]
    assert len(wake_msgs) == 1 and "_wake" not in stored.view()[-2]
    assert stored.rewind_to_user_turn(1) is False
    # The window showing another chat got a notice with the chat's name.
    notice = next(p for p in other_window.sent if p["type"] == "reminder.fired")
    assert notice["session_id"] == sid and notice["here"] is False
    # Delivered once: the next pass does not wake the chat again.
    calls = len(llm.calls)
    await sched.tick(_app(hub, env))
    await asyncio.sleep(0.1)
    assert len(llm.calls) == calls and not chat.running


async def test_what_fired_while_the_app_was_closed_arrives_on_start(env, monkeypatch):
    llm = ScriptedLLM([AssistantTurn(content="hello"), AssistantTurn(content="on it")])
    _use(monkeypatch, llm)
    registry = ToolRegistry([])
    hub = ChatHub(registry)
    sid = await _stored_chat(hub, registry, env)
    store = ReminderStore(env.data_dir)
    # One came due while the app was closed; another fired but the app closed before the
    # chat took it; one is still ahead and must stay scheduled.
    store.add(Reminder(id="late", kind="time", note="send the report", session_id=sid, fire_at=time.time() - 3 * 3600))
    store.add(Reminder(id="lost", kind="time", note="ping the team", session_id=sid, fire_at=time.time() - 60,
                       active=False, fired_at=time.time() - 50))
    store.add(Reminder(id="ahead", kind="time", note="tomorrow", session_id=sid, fire_at=time.time() + 86400))

    fresh_hub = ChatHub(registry)  # "the next start"
    await sched.tick(_app(fresh_hub, env))
    await _settle(fresh_hub, sid)

    woke = str(llm.calls[-1]["messages"][-1]["content"])
    assert "send the report" in woke and "3 h ago" in woke and "ping the team" in woke
    assert len(llm.calls) == 2                                   # one wake for both, not two
    assert store.get("late").delivered and store.get("lost").delivered
    assert store.get("ahead").active


async def test_a_quiet_reminder_is_shown_not_acted_on(env, monkeypatch):
    llm = ScriptedLLM([AssistantTurn(content="hello")])
    _use(monkeypatch, llm)
    registry = ToolRegistry([])
    hub = ChatHub(registry)
    sid = await _stored_chat(hub, registry, env)
    ReminderStore(env.data_dir).add(Reminder(id="q", kind="time", note="drink water", session_id=sid,
                                             fire_at=time.time() - 1, wake=False))
    await sched.tick(_app(hub, env))
    await asyncio.sleep(0.1)
    assert len(llm.calls) == 1                                   # no model turn
    stored = SessionStore(settings=env).load(sid)
    assert any(e.get("kind") == "wake" and e.get("quiet") for e in stored.timeline)
    assert any("drink water" in str(m.get("content")) for m in stored.messages)  # the model will know
    assert ReminderStore(env.data_dir).get("q").delivered


async def test_a_busy_chat_gets_it_at_the_next_step(env, monkeypatch):
    llm = ScriptedLLM([
        AssistantTurn(tool_calls=[tool_call("wait_for", seconds=1.0, reason="the deploy")]),
        AssistantTurn(content="noted the reminder"),
    ])
    _use(monkeypatch, llm)
    registry = ToolRegistry([WaitForTool()])
    hub = ChatHub(registry)
    viewer = _Viewer()
    chat = hub.new_chat(registry, Session(workspace=str(env.workspace)), env)
    hub.attach(chat, viewer)
    chat.launch("deploy and wait", None)
    for _ in range(100):
        if LIVE_WAITS:
            break
        await asyncio.sleep(0.02)
    ReminderStore(env.data_dir).add(Reminder(id="b", kind="time", note="standup in 5 min",
                                             session_id=chat.session.id, fire_at=time.time() - 1))
    fired = await sched.tick(_app(hub, env))
    assert [r.id for r in fired] == ["b"]                        # the live wait itself is left alone
    await chat.run_task

    second = llm.calls[1]["messages"]
    assert any("[Background notice]" in str(m.get("content")) and "standup" in str(m.get("content")) for m in second)
    assert len(llm.calls) == 2                                   # no separate wake run
    assert ReminderStore(env.data_dir).get("b").delivered
    assert ReminderStore(env.data_dir).active() == []           # the wait's record is gone too


async def test_a_notice_the_run_ended_before_taking_wakes_the_chat_after(env, monkeypatch):
    gate = threading.Event()
    llm = _GateLLM([AssistantTurn(content="first answer"), AssistantTurn(content="handled it")], gate)
    _use(monkeypatch, llm)
    registry = ToolRegistry([])
    hub = ChatHub(registry)
    chat = hub.new_chat(registry, Session(workspace=str(env.workspace)), env)
    hub.attach(chat, _Viewer())
    chat.launch("answer me", None)
    await asyncio.sleep(0.1)                                     # the model is thinking: past the step boundary
    ReminderStore(env.data_dir).add(Reminder(id="n", kind="time", note="call Anna", session_id=chat.session.id,
                                             fire_at=time.time() - 1))
    await sched.tick(_app(hub, env))
    assert chat._scratch.get("notifications")
    gate.set()
    await asyncio.gather(chat.run_task)
    await asyncio.sleep(0.05)
    await sched.tick(_app(hub, env))                             # back in the pool → a wake
    await asyncio.gather(chat.run_task)
    assert "call Anna" in str(llm.calls[-1]["messages"][-1]["content"])
    assert ReminderStore(env.data_dir).get("n").delivered


# ---------------------------------------------------------------- closing the app


async def test_a_wait_cut_off_by_closing_the_app_ends_after_the_restart(env, monkeypatch):
    llm = ScriptedLLM([
        AssistantTurn(tool_calls=[tool_call("wait_for", seconds=600, reason="the nightly build")]),
        AssistantTurn(content="checked the nightly build"),
    ])
    _use(monkeypatch, llm)
    registry = ToolRegistry([WaitForTool()])
    hub = ChatHub(registry)
    chat = hub.new_chat(registry, Session(workspace=str(env.workspace)), env)
    sid = chat.session.id
    chat.launch("wait for the nightly build, then check it", None)
    for _ in range(100):
        if LIVE_WAITS:
            break
        await asyncio.sleep(0.02)
    await hub.shutdown()                                         # the app closes

    stored = SessionStore(settings=env).load(sid)
    result = next(m for m in stored.messages if m.get("role") == "tool")
    assert "The app was closed" in result["content"]            # not "stopped by the user"
    store = ReminderStore(env.data_dir)
    wait = store.active()[0]
    assert wait.kind == "wait" and wait.note == "the nightly build"
    assert RunStateStore(env.data_dir).interrupted(sid)          # the task can be continued

    # The next start, after the wait's time has passed.
    store.remove(wait.id)
    wait.fire_at = time.time() - 5
    store.add(wait)
    fresh_hub = ChatHub(registry)
    await sched.tick(_app(fresh_hub, env))
    await _settle(fresh_hub, sid)
    woke = str(llm.calls[-1]["messages"][-1]["content"])
    assert "Your wait is over: the nightly build" in woke
    assert not RunStateStore(env.data_dir).interrupted(sid)      # the wake continued it: no second offer
    assert store.get(wait.id).delivered


def test_a_history_cut_mid_step_is_repaired():
    s = Session()
    s.add_user("go")
    s.messages.append({"role": "assistant", "content": None, "tool_calls": [
        {"id": "a", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
        {"id": "b", "type": "function", "function": {"name": "grep_search", "arguments": "{}"}}]})
    s.add_tool_result("a", "read_file", "text")                # the crash came during the second call
    assert s.answer_open_calls("[No result]") == 1
    roles = [(m.get("role"), m.get("tool_call_id")) for m in s.messages[-3:]]
    assert roles == [("assistant", None), ("tool", "a"), ("tool", "b")]
    assert s.answer_open_calls("[No result]") == 0


# ---------------------------------------------------------------- approvals without a window


async def test_an_approval_waits_for_a_window_and_is_shown_when_one_opens(env, monkeypatch):
    env.approval_mode = "manual"
    llm = ScriptedLLM([AssistantTurn(tool_calls=[tool_call("danger_tool")]), AssistantTurn(content="done")])
    _use(monkeypatch, llm)
    registry = ToolRegistry([_Danger()])
    hub = ChatHub(registry)
    watcher = _Viewer()
    hub.connections.add(watcher)
    chat = hub.new_chat(registry, Session(workspace=str(env.workspace)), env)
    chat.launch("do it", None)
    for _ in range(100):
        if chat.pending_approvals:
            break
        await asyncio.sleep(0.02)
    assert chat.pending_approvals
    assert any(p["type"] == "reminder.fired" and p.get("attention") for p in watcher.sent)  # "needs you"

    window = _Viewer()
    hub.attach(chat, window)
    await chat.replay_to(window)
    card = next(p for p in window.sent if p["type"] == "approval.requested")
    chat.resolve_approval({"request_id": card["request_id"], "scope": "once"})
    await chat.run_task
    assert any(m.get("content") == "done the dangerous thing" for m in chat.session.messages)


# ---------------------------------------------------------------- through the real socket


@pytest.fixture()
def gated_app(monkeypatch, settings):
    import core.settings as settings_module
    import server.app as app_module
    import server.ws as ws_module
    from server.app import create_app

    settings.approval_mode = "bypass"
    for mod in (settings_module, app_module, ws_module):
        monkeypatch.setattr(mod, "get_settings", lambda: settings)
    gate = threading.Event()
    llm = _GateLLM([AssistantTurn(content="answer for A"), AssistantTurn(content="answer for B")], gate)
    _use(monkeypatch, llm)
    return create_app(), gate, settings


def _until(ws, kind, limit=200):
    seen = []
    for _ in range(limit):
        m = ws.receive_json()
        seen.append(m)
        if m["type"] == kind:
            return m, seen
    raise AssertionError([m["type"] for m in seen])


def test_switching_chats_mid_run_keeps_the_run_in_its_own_chat(gated_app):
    app, gate, settings = gated_app
    with TestClient(app) as tc, tc.websocket_connect("/ws") as ws:
        ready = ws.receive_json()
        a_id = ready["session_id"]
        ws.send_json({"type": "run", "task": "a long task in A"})
        _until(ws, "run.started")
        ws.send_json({"type": "new_session"})
        loaded, _ = _until(ws, "session.loaded")
        b_id = loaded["session"]["id"]
        assert b_id != a_id and loaded["running"] is False

        gate.set()                                                # A finishes while B is on screen
        activity, seen = _until(ws, "chat.activity")
        while activity.get("running") or activity["session_id"] != a_id:
            activity, more = _until(ws, "chat.activity")
            seen += more
        assert not any(m["type"] == "run.finished" for m in seen)  # A's run did not play in B

        ws.send_json({"type": "load_session", "session_id": a_id})
        back, _ = _until(ws, "session.loaded")
        answers = [e for e in back["session"]["timeline"] if e.get("kind") == "answer"]
        assert [e["text"] for e in answers] == ["answer for A"]
        assert back["running"] is False
    # B was never used: nothing was written into it, and A's history is whole on disk.
    store = SessionStore(settings=settings)
    assert store.load(b_id) is None
    assert any(e.get("kind") == "answer" for e in store.load(a_id).timeline)


def test_reopening_a_chat_that_runs_in_the_background_follows_it_live(gated_app):
    app, gate, _settings = gated_app
    with TestClient(app) as tc, tc.websocket_connect("/ws") as ws:
        a_id = ws.receive_json()["session_id"]
        ws.send_json({"type": "run", "task": "a long task in A"})
        _until(ws, "run.started")
        ws.send_json({"type": "new_session"})
        _until(ws, "session.loaded")
        ws.send_json({"type": "load_session", "session_id": a_id})
        back, _ = _until(ws, "session.loaded")
        assert back["running"] is True
        state, _ = _until(ws, "state")
        assert state["state"] == "running"
        gate.set()
        finished, _ = _until(ws, "run.finished")                 # the live run reaches this window again
        assert finished["text"] == "answer for A"
        activity, _ = _until(ws, "chat.activity")               # the list learns it has finished
        assert activity == {**activity, "session_id": a_id, "running": False}
        listed = tc.get("/api/sessions").json()["sessions"]
        assert any(s["id"] == a_id and s["running"] is False for s in listed)
