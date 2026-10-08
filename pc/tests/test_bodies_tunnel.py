"""The bodies (0.3.0 stage 2): the tunnel to a server keeps itself up, the window reaches the
server's own API through it (HTTP and WebSocket), the registry shows every body with its load.

The tunnel runs against a scripted SSH connection whose drop the test controls; the proxy against
a real second HTTP server on a local port standing for the server's agent behind the tunnel.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from core.body_labels import BodyLabels, clean
from core.servers.registry import ServerRecord, ServerStore
from core.servers.remote import RemoteError
from core.servers.tunnel import Tunnel, TunnelManager


def _record(sid: str = "srv1", host: str = "203.0.113.7") -> ServerRecord:
    return ServerRecord(id=sid, name="vps", host=host, port=22, user="root", host_key="ssh-ed25519 AAAA",
                        host_key_fingerprint="SHA256:x", body_id="b-" + sid)


class Link:
    """One scripted SSH connection: forwards a port, and drops when the test says so."""

    def __init__(self, world: World, login) -> None:
        self.world, self.login = world, login
        self.dropped = asyncio.Event()

    async def __aenter__(self):
        self.world.logins.append(self.login)
        if self.world.refuse:
            raise RemoteError(self.world.refuse)
        self.world.links.append(self)
        return self

    async def __aexit__(self, *exc):
        return None

    async def forward_local(self, remote_port: int, local_port: int = 0) -> int:
        self.world.forwards.append((remote_port, local_port))
        return local_port or 40123

    async def wait_closed(self) -> None:
        await self.dropped.wait()


class World:
    def __init__(self) -> None:
        self.logins: list = []
        self.links: list[Link] = []
        self.forwards: list[tuple[int, int]] = []
        self.refuse = ""
        self.agent_up = True
        self.beats = 0

    def factory(self, login) -> Link:
        return Link(self, login)

    async def fetch(self, port: int) -> dict:
        self.beats += 1
        if not self.agent_up:
            raise httpx.ConnectError("refused")
        return {"id": "b-srv1", "load": {"cpu_pct": 3.0}, "port_seen": port}


async def _until(check, timeout: float = 3.0) -> None:
    end = time.monotonic() + timeout
    while not check():
        if time.monotonic() > end:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


@pytest.fixture()
def store(tmp_path):
    s = ServerStore(tmp_path)
    s.save(_record())
    return s


async def test_the_tunnel_comes_up_drops_and_comes_back_on_the_same_port(store):
    world = World()
    tunnel = Tunnel(store, _record(), world.factory, world.fetch, heartbeat_s=0.02, backoff=(0.05,))
    task = asyncio.create_task(tunnel.run())
    try:
        await _until(lambda: tunnel.state == "online" and tunnel.agent == "ok")
        assert tunnel.local_port == 40123 and world.forwards[0] == (8137, 0)
        assert tunnel.status["port_seen"] == 40123 and tunnel.online_since > 0
        # The PC's key and the pinned host key, never a password.
        login = world.logins[0]
        assert login.key_path.endswith("srv1") and not login.password and login.host_key == "ssh-ed25519 AAAA"

        world.links[-1].dropped.set()                   # the laptop slept / the server rebooted
        await _until(lambda: len(world.links) == 2 and tunnel.state == "online")
        assert world.forwards[1] == (8137, 40123)       # the same local port again
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_an_agent_that_is_down_is_told_apart_from_a_dead_tunnel(store):
    world = World()
    world.agent_up = False
    tunnel = Tunnel(store, _record(), world.factory, world.fetch, heartbeat_s=0.02, backoff=(0.05,))
    task = asyncio.create_task(tunnel.run())
    try:
        await _until(lambda: tunnel.state == "online" and tunnel.agent == "down")
        assert len(world.links) == 1                     # SSH stays: systemd restarts the agent
        world.agent_up = True
        await _until(lambda: tunnel.agent == "ok")
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_a_refused_connection_waits_and_reconnect_now_skips_the_wait(store):
    world = World()
    world.refuse = "the server refused the login"
    tunnel = Tunnel(store, _record(), world.factory, world.fetch, heartbeat_s=0.02, backoff=(30.0,))
    task = asyncio.create_task(tunnel.run())
    try:
        await _until(lambda: tunnel.state == "offline")
        assert tunnel.error == "the server refused the login" and tunnel.agent == ""
        await asyncio.sleep(0.1)
        assert len(world.logins) == 1                    # waiting out the 30 s pause
        world.refuse = ""
        tunnel.reconnect_now()
        await _until(lambda: tunnel.state == "online")
        assert tunnel.error == "" and tunnel.attempts == 0
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_the_manager_follows_the_server_list(store):
    world = World()
    manager = TunnelManager(store, world.factory, world.fetch, heartbeat_s=0.02, backoff=(0.05,))
    try:
        await manager.sync()
        first = manager.get("srv1")
        await _until(lambda: first.state == "online")

        record = _record()
        record.name = "renamed"
        store.save(record)
        await manager.sync()
        assert manager.get("srv1") is first and first.record.name == "renamed"   # no reconnect

        store.save(_record(host="198.51.100.9"))      # moved to a new address: a new tunnel
        await manager.sync()
        assert manager.get("srv1") is not first
        await _until(lambda: manager.get("srv1").state == "online")
        assert world.logins[-1].host == "198.51.100.9"

        store.remove("srv1")
        await manager.sync()
        assert manager.get("srv1") is None and manager.all() == []
    finally:
        await manager.stop()


def test_labels_are_trimmed_unique_and_kept(tmp_path):
    assert clean(["  builds ", "Builds", "", "prod  line", "x" * 50]) == ["builds", "prod line", "x" * 32]
    labels = BodyLabels(tmp_path)
    assert labels.set("srv1", ["builds", "prod"]) == ["builds", "prod"]
    assert BodyLabels(tmp_path).get("srv1") == ["builds", "prod"]
    labels.set("srv1", [])
    assert labels.all() == {}


# ------------------------------------------------------------------ the window's way into a server


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


server_agent_app: list = [None]


@pytest.fixture()
def server_agent():
    """A real HTTP server on 127.0.0.1 standing for the agent at the far end of the tunnel."""
    remote = FastAPI()

    @remote.get("/api/echo")
    async def echo(q: str = "") -> dict:
        return {"q": q, "where": "server"}

    @remote.post("/api/echo")
    async def echo_post(payload: dict) -> dict:
        return {"got": payload}

    @remote.get("/api/stream")
    async def stream():
        from fastapi.responses import StreamingResponse

        async def chunks():
            for i in range(3):
                yield f"part{i};".encode()

        return StreamingResponse(chunks(), media_type="text/plain", headers={"X-From": "server"})

    @remote.post("/api/settings")
    async def settings_post(payload: dict) -> dict:
        remote.state.settings_posts = [*getattr(remote.state, "settings_posts", []), payload]
        return {"ok": True}

    @remote.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json({"hello": websocket.query_params.get("token", "")})
        while True:
            message = await websocket.receive_text()
            await websocket.send_text("server got " + message)

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(remote, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    end = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < end and thread.is_alive()
        time.sleep(0.02)
    server_agent_app[0] = remote
    yield port
    server.should_exit = True
    thread.join(timeout=10)


class _OnlineTunnel:
    def __init__(self, record: ServerRecord, port: int, state: str = "online") -> None:
        self.record, self.local_port, self.state = record, port, state
        self.kicked = False

    def public(self) -> dict:
        return {"state": self.state, "agent": "ok", "error": "", "port": self.local_port,
                "status": {"load": {"cpu_pct": 12.5}}, "last_seen": 1.0, "online_since": 1.0}

    def reconnect_now(self) -> None:
        self.kicked = True


class _Tunnels:
    def __init__(self, *tunnels: _OnlineTunnel) -> None:
        self.by_id = {t.record.id: t for t in tunnels}

    def get(self, sid):
        return self.by_id.get(sid)

    async def sync(self):
        return None

    async def stop(self):
        return None


@pytest.fixture()
def pc_app(monkeypatch, settings):
    import core.settings as settings_module
    import server.app as app_module
    import server.bodies as bodies_module
    import server.ws as ws_module
    from server.app import create_app

    for mod in (settings_module, app_module, ws_module):
        monkeypatch.setattr(mod, "get_settings", lambda: settings)

    async def no_tunnels(app):
        app.state.tunnels = None

    monkeypatch.setattr(bodies_module, "start_tunnels", no_tunnels)
    return create_app()


def test_the_window_reaches_the_servers_api_and_socket_through_the_tunnel(pc_app, server_agent):
    with TestClient(pc_app) as client:
        pc_app.state.tunnels = _Tunnels(_OnlineTunnel(_record(), server_agent),
                                        _OnlineTunnel(_record("srv2"), 1, state="connecting"))
        r = client.get("/b/srv1/api/echo?q=hi")
        assert r.status_code == 200 and r.json() == {"q": "hi", "where": "server"}
        assert client.post("/b/srv1/api/echo", json={"a": 1}).json() == {"got": {"a": 1}}
        streamed = client.get("/b/srv1/api/stream")
        assert streamed.text == "part0;part1;part2;" and streamed.headers["x-from"] == "server"
        assert client.get("/b/srv1/api/nothing").status_code == 404      # the server's own 404

        with client.websocket_connect("/b/srv1/ws?token=abc") as ws:
            assert ws.receive_json() == {"hello": "abc"}
            ws.send_text("ping")
            assert ws.receive_text() == "server got ping"

        # A server whose tunnel is not up: a clear 503, not a hang; an unknown one: 404.
        offline = client.get("/b/srv2/api/echo")
        assert offline.status_code == 503 and "vps" in offline.json()["detail"]
        assert client.get("/b/nope/api/echo").status_code == 404


async def test_the_phone_bridge_cannot_use_the_way_into_a_server(pc_app, server_agent):
    async with pc_app.router.lifespan_context(pc_app):
        pc_app.state.tunnels = _Tunnels(_OnlineTunnel(_record(), server_agent))
        lan = httpx.ASGITransport(app=pc_app, client=("192.168.1.50", 5555))
        async with httpx.AsyncClient(transport=lan, base_url="http://pc") as phone:
            for path in ("/b/srv1/api/echo", "/api/bodies"):
                assert (await phone.get(path)).status_code in (401, 403)


def test_the_registry_lists_this_pc_and_the_servers_with_their_load(pc_app, settings, server_agent):
    ServerStore(settings.data_dir).save(_record())
    with TestClient(pc_app) as client:
        tunnel = _OnlineTunnel(_record(), server_agent)
        pc_app.state.tunnels = _Tunnels(tunnel)
        assert client.post("/api/bodies/srv1/labels", json={"labels": ["builds", " builds "]}).json() == {
            "ok": True, "labels": ["builds"]}
        bodies = client.get("/api/bodies").json()["bodies"]
        me, server = bodies
        assert me["self"] and me["kind"] == "pc" and me["state"] == "online"
        assert me["status"]["cpus"] >= 1 and me["status"]["mem_mb"] > 0 and "cpu_pct" in me["status"]["load"]
        assert "public_key" not in me["status"]
        assert server["id"] == "srv1" and server["kind"] == "server" and server["state"] == "online"
        assert server["labels"] == ["builds"] and server["status"]["load"]["cpu_pct"] == 12.5
        assert "port" not in server
        assert client.post("/api/bodies/srv1/reconnect").json() == {"ok": True} and tunnel.kicked

        status = client.get("/api/body/status").json()
        assert status["id"] == me["id"] and status["load"]["tasks"] == 0 and status["version"]


def test_a_read_is_tried_again_when_the_connection_breaks_under_it(pc_app, server_agent, monkeypatch):
    """Seen live: one 502 among many reads — a pooled connection the server was closing."""
    with TestClient(pc_app) as client:
        pc_app.state.tunnels = _Tunnels(_OnlineTunnel(_record(), server_agent))
        client.get("/b/srv1/api/echo")                        # the proxy's client exists now
        real_send = pc_app.state.http_proxy.send
        calls = {"n": 0}

        async def flaky(request, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
            return await real_send(request, **kw)

        monkeypatch.setattr(pc_app.state.http_proxy, "send", flaky)
        assert client.get("/b/srv1/api/echo?q=again").json()["q"] == "again" and calls["n"] == 2

        calls["n"] = 0                                         # a change is not sent twice
        assert client.post("/b/srv1/api/echo", json={"a": 1}).status_code == 502 and calls["n"] == 1


def test_the_servers_mode_is_changed_there_at_once(pc_app, settings, server_agent):
    """Settings → Servers → the mode chip: the server's own setting changes through its tunnel."""
    from core.servers.registry import ServerStore

    record = _record()
    record.mode = "owner"
    ServerStore(settings.data_dir).save(record)
    with TestClient(pc_app) as client:
        pc_app.state.tunnels = _Tunnels(_OnlineTunnel(record, server_agent))
        r = client.post("/api/servers/srv1/mode", json={"mode": "autopilot"}).json()
        assert r == {"ok": True, "mode": "autopilot"}
        assert server_agent_app[0].state.settings_posts[-1] == {"approval_mode": "autopilot"}
        assert ServerStore(settings.data_dir).get("srv1").mode == "autopilot"
        client.post("/api/servers/srv1/mode", json={"mode": "careful"})
        assert server_agent_app[0].state.settings_posts[-1] == {"approval_mode": "manual"}
        assert client.post("/api/servers/srv1/mode", json={"mode": "god"}).status_code == 400
        kinds = [x["kind"] for x in client.get("/api/journal?kind=server.").json()["records"]]
        assert kinds[:2] == ["server.mode", "server.mode"]


def test_the_sandbox_is_not_switched_on_the_fly(pc_app, settings, server_agent):
    """Going into or out of the sandbox changes how the agent runs: a reinstall, said plainly."""
    from core.servers.registry import ServerStore

    record = _record()
    record.mode = "owner"
    ServerStore(settings.data_dir).save(record)
    with TestClient(pc_app) as client:
        pc_app.state.tunnels = _Tunnels(_OnlineTunnel(record, server_agent))
        r = client.post("/api/servers/srv1/mode", json={"mode": "sandbox"}).json()
        assert r["ok"] is False and "install it again" in r["error"]
        assert ServerStore(settings.data_dir).get("srv1").mode == "owner"


async def test_the_sandboxs_own_gateway_counts_as_this_machine(pc_app, settings, monkeypatch):
    """Found live: in the container the PC's tunnel arrives from the Docker network's gateway, and
    every request but /api/health was refused. LOCAL_ALIASES makes that address this machine;
    any other address is still a stranger."""
    import core.settings as settings_module

    async with pc_app.router.lifespan_context(pc_app):
        def get(host):
            transport = httpx.ASGITransport(app=pc_app, client=(host, 40000))
            return httpx.AsyncClient(transport=transport, base_url="http://agent")

        async with get("172.18.0.1") as gateway, get("172.18.0.5") as neighbour:
            assert (await gateway.get("/api/bodies")).status_code in (401, 403)     # not set: a stranger
            import server.remote_auth as remote_auth

            settings.local_aliases = "172.18.0.1"
            monkeypatch.setattr(settings_module, "get_settings", lambda: settings)
            monkeypatch.setattr(remote_auth, "get_settings", lambda: settings)
            assert (await gateway.get("/api/bodies")).status_code == 200
            assert (await gateway.get("/api/body/status")).status_code == 200
            assert (await neighbour.get("/api/bodies")).status_code in (401, 403)   # anyone else: still not
