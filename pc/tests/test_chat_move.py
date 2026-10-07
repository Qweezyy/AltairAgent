"""Moving a chat to a server (0.3.0 stage 3): the history, the reminders and the folder go there,
the chat goes on there, and here it is in the trash (restorable).

This PC is the real app; the server is a second real HTTP server with its own store, reminders and
folders; the folder travels through a scripted SSH connection that really unpacks the archive.
"""

from __future__ import annotations

import shlex
import socket
import tarfile
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.agent.session import Session
from core.agent.storage import SessionStore
from core.reminders import Reminder, ReminderStore
from core.servers.registry import ServerRecord, ServerStore
from core.settings import Settings
from tests.test_server_install import GOOD_PROBE


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeHub:
    """The server's chats: records what it was asked to go on with."""

    def __init__(self) -> None:
        self.chats: dict = {}
        self.launched: list[tuple[str, str]] = []

    async def open(self, sid):
        hub = self

        class Chat:
            running = False

            def launch(self, text, model):
                hub.launched.append((sid, text))

        return Chat(), ""


@pytest.fixture()
def server_side(tmp_path):
    from server import chat_move

    app_dir, work = tmp_path / "srv-app", tmp_path / "srv-work"
    app_dir.mkdir()
    work.mkdir()
    settings = Settings(_env_file=None, openrouter_api_key="k", app_path=app_dir, workspace_path=work)
    app = FastAPI()
    app.state.settings = settings
    app.state.store = SessionStore(settings=settings)
    app.state.chats = FakeHub()
    chat_move.install(app)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    end = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < end
        time.sleep(0.02)

    class Side:
        pass

    side = Side()
    side.app, side.port, side.settings, side.work, side.root = app, port, settings, work, tmp_path / "srv-root"
    yield side
    server.should_exit = True
    thread.join(10)


class DiskRemote:
    """SSH to the server, done on disk: the server's / is side.root."""

    def __init__(self, side, login) -> None:
        self.side = side
        self.sudo = ""
        self.host_key = self.host_key_fingerprint = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def _local(self, path: str) -> Path:
        p = Path(path)
        return p if p.drive else self.side.root / path.lstrip("/")      # a test folder is a real one

    async def put(self, local, remote_path, on_progress=None):
        target = self._local(remote_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(local).read_bytes())  # noqa: ASYNC240 - a test double, tiny files

    async def run(self, command, *, root=False, timeout=0, stdin=None):
        from core.servers.remote import RunResult

        if 'echo "arch=$(uname -m)"' in command:
            return RunResult(0, GOOD_PROBE, "")
        words = shlex.split(command.split(";")[0])
        folder = words[words.index("-C") + 1]
        archive = words[words.index("-xzf") + 1]
        dest = self._local(folder)
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(self._local(archive)) as tar:
            tar.extractall(dest, filter="data")
        self._local(archive).unlink()
        return RunResult(0, "", "")


@pytest.fixture()
def pc(monkeypatch, settings, server_side):
    import core.servers.remote as remote_module
    import core.settings as settings_module
    import server.app as app_module
    import server.bodies as bodies_module
    import server.ws as ws_module
    from server.app import create_app

    for mod in (settings_module, app_module, ws_module):
        monkeypatch.setattr(mod, "get_settings", lambda: settings)
    monkeypatch.setattr(remote_module, "SSHRemote", lambda login: DiskRemote(server_side, login))
    record = ServerRecord(id="srv1", name="test-vps", host="203.0.113.7", port=22, user="root", mode="owner")
    ServerStore(settings.data_dir).save(record)

    class Tunnel:
        state, agent, error, local_port = "online", "ok", "", server_side.port
        status = {"workspace": server_side.work.as_posix()}

        def __init__(self):
            self.record = record

    class Tunnels:
        def get(self, sid):
            return Tunnel() if sid == "srv1" else None

        def all(self):
            return [Tunnel()]

        async def sync(self):
            return None

        async def stop(self):
            return None

    async def fake_tunnels(app):
        app.state.tunnels = Tunnels()

    monkeypatch.setattr(bodies_module, "start_tunnels", fake_tunnels)
    return create_app()


def _chat(settings, folder: Path) -> Session:
    session = Session(title="Build the parser", workspace=str(folder))
    session.messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "build the parser"},
                        {"role": "assistant", "content": "half done"}]
    SessionStore(settings=settings).save(session)
    return session


def test_a_chat_moves_with_its_folder_and_goes_on_there(pc, settings, server_side, tmp_path):
    project = tmp_path / "parser"
    (project / "src").mkdir(parents=True)
    (project / "src" / "parse.py").write_text("print('ok')\n", encoding="utf-8")
    (project / "node_modules" / "big").mkdir(parents=True)
    (project / "node_modules" / "big" / "x.js").write_text("x", encoding="utf-8")
    session = _chat(settings, project)
    reminders = ReminderStore(settings.data_dir)
    reminders.add(Reminder(id="r1", kind="time", note="check the build", session_id=session.id, fire_at=time.time() + 3600))
    reminders.add(Reminder(id="r2", kind="job", note="watch npm", session_id=session.id, job="npm", value="123"))

    with TestClient(pc) as client:
        r = client.post(f"/api/sessions/{session.id}/move",
                        json={"body": "srv1", "copy_folder": True, "continue": True}).json()
        assert r["ok"], r
        assert Path(r["folder"]) == server_side.work / "parser"
        assert r["copied"]["skipped"] == ["node_modules"] and r["copied"]["bytes"] > 0
        assert r["continued"] and r["reminders"] == 1

        # Here: in the trash, restorable; gone from the list; its time reminder went along.
        assert session.id not in [s["id"] for s in client.get("/api/sessions").json()["sessions"]]
        assert session.id in [s["id"] for s in client.get("/api/trash/sessions").json()["sessions"]]
        # All its reminders left: the timer went there; the watch of a PC job has no chat here
        # to wake any more (found in the full suite: it fired and brought the chat back).
        assert [x for x in reminders.all() if x.session_id == session.id] == [] and r["dropped_watches"] == ["npm"]

    # There: the files (without node_modules), the history, the reminder, and it went on.
    moved = server_side.work / "parser"
    assert (moved / "src" / "parse.py").read_text(encoding="utf-8") == "print('ok')\n"
    assert not (moved / "node_modules").exists()
    there = SessionStore(settings=server_side.settings).load(session.id)
    assert there is not None and there.title == "Build the parser" and there.messages[-1]["content"] == "half done"
    assert there.approval_mode == ""                           # the server's own mode applies
    assert [r.note for r in ReminderStore(server_side.settings.data_dir).active(session.id)] == ["check the build"]
    assert server_side.app.state.chats.launched and "Continue the task" in server_side.app.state.chats.launched[0][1]


def test_a_chat_without_its_folder_lands_in_the_servers_default_one(pc, settings, server_side, tmp_path):
    session = _chat(settings, tmp_path)
    with TestClient(pc) as client:
        r = client.post(f"/api/sessions/{session.id}/move", json={"body": "srv1"}).json()
    assert r["ok"] and not r["copied"] and not r["continued"]
    assert Path(r["folder"]) == server_side.work
    assert server_side.app.state.chats.launched == []


def test_moving_to_an_unknown_server_or_from_the_lan_is_refused(pc, settings, tmp_path):
    import httpx

    session = _chat(settings, tmp_path)
    with TestClient(pc) as client:
        assert client.post(f"/api/sessions/{session.id}/move", json={"body": "nope"}).status_code == 404
        assert client.post("/api/sessions/zzz/move", json={"body": "srv1"}).status_code == 404

    async def from_lan():
        async with pc.router.lifespan_context(pc):
            lan = httpx.ASGITransport(app=pc, client=("192.168.1.50", 5555))
            async with httpx.AsyncClient(transport=lan, base_url="http://pc") as phone:
                r = await phone.post(f"/api/sessions/{session.id}/move", json={"body": "srv1"})
                assert r.status_code in (401, 403)

    import asyncio

    asyncio.run(from_lan())
    assert SessionStore(settings=settings).load(session.id) is not None     # still here


async def test_a_deleted_chat_is_not_brought_back_by_a_late_save(settings, tmp_path, monkeypatch):
    """The race behind it: a reminder loads the chat a moment before it is deleted (or moved),
    then stores it — the file was back. The hub remembers the chats that went."""
    import core.settings as settings_module
    import server.chats as chats_module
    from server.chats import ChatHub

    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)
    monkeypatch.setattr(chats_module.settings_module, "get_settings", lambda: settings)
    session = _chat(settings, tmp_path)
    hub = ChatHub()
    chat, _ = await hub.open(session.id)              # loaded by "a reminder"
    assert chat is not None
    hub.forget(session.id)                            # deleted / moved now
    await SessionStore(settings=settings).async_delete(session.id)
    await chat._save_session()                        # the late save
    assert SessionStore(settings=settings).load(session.id) is None
    assert (await hub.open(session.id))[0] is None

    assert await SessionStore(settings=settings).async_restore(session.id)
    hub.gone.discard(session.id)                      # what the restore endpoint does
    assert (await hub.open(session.id))[0] is not None
