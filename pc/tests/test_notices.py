"""PC notices of what a server did (0.3.0): read from its Journal through the tunnel, once each,
only the news (a task done or failed, a rollback, a restart), and a server gone and back."""

from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI

import server.notices as notices_module
from core.servers.registry import ServerRecord
from server.notices import Notices, notice_of


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def journal_server():
    """A server's Journal API: /api/journal with `since` and `limit`, newest first."""
    remote = FastAPI()
    remote.state.records = [{"seq": 1, "kind": "run.started", "chat": "c0", "data": {}}]

    @remote.get("/api/journal")
    async def journal(limit: int = 100, since: int | None = None) -> dict:
        rows = sorted(remote.state.records, key=lambda r: -r["seq"])
        if since is not None:
            rows = [r for r in rows if r["seq"] > since]
        return {"records": rows[:limit]}

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(remote, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    yield remote, port
    server.should_exit = True
    thread.join(10)


class _Tunnel:
    def __init__(self, port):
        self.record = ServerRecord(id="srv1", name="test-vps", host="203.0.113.7")
        self.state, self.agent, self.error, self.local_port = "online", "ok", "", port


class _App:
    def __init__(self, settings, tunnel):
        class State:
            pass

        class Hub:
            def __init__(self):
                self.sent = []

            async def broadcast(self, payload):
                self.sent.append(payload)

        class Tunnels:
            def all(self):
                return [tunnel]

        self.state = State()
        self.state.settings = settings
        self.state.chats = Hub()
        self.state.tunnels = Tunnels()


async def test_only_news_once_each_and_not_the_history(settings, journal_server):
    remote, port = journal_server
    tunnel = _Tunnel(port)
    app = _App(settings, tunnel)
    n = Notices(app)
    await n.tick()
    assert app.state.chats.sent == []                       # the first look: history, not news
    remote.state.records += [
        {"seq": 2, "kind": "run.finished", "chat": "c7", "data": {"answer": "Deployed the shop."}},
        {"seq": 3, "kind": "tool.remote", "chat": "", "data": {}},                   # not news
        {"seq": 4, "kind": "guardian.update.rolled_back", "chat": "", "data": {"reason": "no answer"}},
    ]
    await n.tick()
    sent = app.state.chats.sent
    assert [x["kind"] for x in sent] == ["run.finished", "guardian.update.rolled_back"]
    assert sent[0]["chat"] == "c7" and sent[0]["body_id"] == "srv1" and "Deployed" in sent[0]["text"]
    assert "test-vps" in sent[0]["title"] and sent[1]["level"] == "error"
    await n.tick()
    assert len(app.state.chats.sent) == 2                   # not again
    again = Notices(app)                                    # after a restart of the PC: still not again
    await again.tick()
    assert len(app.state.chats.sent) == 2


async def test_a_server_gone_for_a_while_and_back(settings, journal_server, monkeypatch):
    remote, port = journal_server
    tunnel = _Tunnel(port)
    app = _App(settings, tunnel)
    n = Notices(app)
    await n.tick()
    tunnel.state = "connecting"
    await n.tick()
    assert app.state.chats.sent == []                       # a short drop is not news
    monkeypatch.setattr(notices_module, "OFFLINE_AFTER_S", 0.0)
    await n.tick()
    await n.tick()
    assert [x["kind"] for x in app.state.chats.sent] == ["body.offline"]
    tunnel.state = "online"
    await n.tick()
    assert [x["kind"] for x in app.state.chats.sent] == ["body.offline", "body.online"]


def test_every_kind_of_news_has_its_words():
    for kind in notices_module.NOTABLE:
        notice = notice_of({"kind": kind, "data": {}, "chat": "c"}, "vps")
        assert notice and notice["title"] and not notice["title"].startswith("ntc."), kind
    assert notice_of({"kind": "tool.remote", "data": {}}, "vps") is None


def test_a_notification_is_plain_text():
    notice = notice_of({"kind": "run.finished", "chat": "c", "data": {
        "answer": "File created: [/work/a.txt](file:/work/a.txt) — `ls` shows **2** files\n\n## Done"}}, "vps")
    assert notice["text"] == "File created: /work/a.txt — ls shows 2 files Done"
