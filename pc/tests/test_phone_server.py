"""The phone and a server (0.3.0 stage 6), end to end: the server's TLS door with a pinned
certificate, pairing by a one-time code, signing in with the phone's own key, the news, the stop
button, a socket; and the way through the PC, with the PC's token kept on the PC.

The "phone" here is what the Android app has to do (pc/server/PHONE_SERVER_SPEC.md), in Python."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import socket
import ssl

import httpx
import pytest
import websockets
from fastapi import Request, WebSocket

from core.bodies import Identity, signed_message


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def server_app(monkeypatch, settings):
    """A server body with the real app, its door on a free port."""
    import core.settings as settings_module
    import server.app as app_module
    import server.bodies as bodies_module
    import server.remote_auth as remote_auth
    import server.ws as ws_module
    from server.app import create_app

    settings.body_kind = "server"
    settings.remote_port = _free_port()
    for mod in (settings_module, app_module, ws_module, remote_auth):
        monkeypatch.setattr(mod, "get_settings", lambda: settings)

    async def no_tunnels(app):
        app.state.tunnels = None

    monkeypatch.setattr(bodies_module, "start_tunnels", no_tunnels)
    return create_app()


def _pinned_fetch_fingerprint(port: int) -> str:
    """What a phone does before trusting the door: the certificate's SHA-256, compared to the QR."""
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", port), timeout=10) as raw:
        with ctx.wrap_socket(raw, server_hostname="altair") as tls:
            return hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()


def _insecure_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE   # trust comes from the pinned hash
    return ctx


async def test_the_phone_pairs_signs_in_and_uses_the_servers_door(server_app, settings, tmp_path):
    app = server_app
    async with app.router.lifespan_context(app):
        local = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://srv")
        status = (await local.post("/api/remote-access", json={"enabled": True})).json()
        assert status["enabled"] and len(status["fingerprint"]) == 64 and not status["error"]
        assert "REMOTE_ACCESS=true" in (settings.app_dir / ".env").read_text(encoding="utf-8")
        code = (await local.post("/api/bodies/pair-code")).json()["code"]
        port = settings.remote_port
        base = f"https://127.0.0.1:{port}"

        fp = await asyncio.to_thread(_pinned_fetch_fingerprint, port)
        assert fp == status["fingerprint"]                         # the pin from the QR holds

        phone = Identity(tmp_path / "phone", "phone", "Pixel")
        async with httpx.AsyncClient(base_url=base, verify=_insecure_ctx(), timeout=20) as net:
            # Without a session nothing but health and the pairing paths answers.
            assert (await net.get("/api/notices")).status_code == 401
            assert (await net.get("/api/health")).status_code == 200
            assert (await net.post("/api/bodies/pair", json={"code": "WRONGCOD", "card": phone.card()})).status_code == 401
            paired = (await net.post("/api/bodies/pair", json={"code": code, "card": phone.card()})).json()
            assert paired["ok"] and paired["body"]["kind"] == "server"
            assert (await net.post("/api/bodies/pair", json={"code": code, "card": phone.card()})).status_code == 401

            ch = (await net.post("/api/bodies/challenge", json={"id": phone.id})).json()
            sig = phone.answer(ch["nonce"], ch["verifier"])
            token = (await net.post("/api/bodies/login", json={"id": phone.id, "nonce": ch["nonce"],
                                                              "signature": sig})).json()["token"]
            auth = {"Authorization": f"Bearer {token}"}
            first = (await net.get("/api/notices", headers=auth)).json()
            assert first == {"notices": [], "next": first["next"]}
            assert (await net.post("/api/runs/stop", headers=auth)).json() == {"ok": True, "stopped": []}
            # The PC-only parts stay closed to the phone, even signed in.
            assert (await net.post("/api/bodies/pair-code", headers=auth)).status_code == 403
            assert (await net.post("/api/remote-access", json={"enabled": False}, headers=auth)).status_code == 403
            assert (await net.post("/api/tools/run", json={"tool": "read_file", "args": {}}, headers=auth)).status_code == 403

        async with websockets.connect(f"wss://127.0.0.1:{port}/ws?token={token}", ssl=_insecure_ctx()) as ws:
            ready = json.loads(await asyncio.wait_for(ws.recv(), 20))
            while ready.get("type") != "ready":
                ready = json.loads(await asyncio.wait_for(ws.recv(), 20))
            assert ready["type"] == "ready"
        with pytest.raises(websockets.exceptions.InvalidStatus):
            async with websockets.connect(f"wss://127.0.0.1:{port}/ws", ssl=_insecure_ctx()):
                pass
        await local.post("/api/remote-access", json={"enabled": False})
        await local.aclose()


async def test_pairing_locks_after_wrong_codes(server_app, settings, tmp_path):
    from core.bodies import PAIR_MAX_FAILURES

    async with server_app.router.lifespan_context(server_app):
        local = httpx.AsyncClient(transport=httpx.ASGITransport(app=server_app), base_url="http://srv")
        code = (await local.post("/api/bodies/pair-code")).json()["code"]
        phone = Identity(tmp_path / "phone", "phone", "Pixel")
        for _ in range(PAIR_MAX_FAILURES):
            assert (await local.post("/api/bodies/pair", json={"code": "ZZZZZZZZ", "card": phone.card()})).status_code == 401
        # Locked: even the right code waits out the window.
        assert (await local.post("/api/bodies/pair", json={"code": code, "card": phone.card()})).status_code == 401
        await local.aclose()


async def test_news_since_a_mark_and_an_approval_waiting_is_news(server_app, settings):
    from core.journal import get_journal

    async with server_app.router.lifespan_context(server_app):
        local = httpx.AsyncClient(transport=httpx.ASGITransport(app=server_app), base_url="http://srv")
        mark = (await local.get("/api/notices")).json()["next"]
        journal = get_journal(settings.data_dir / "journal")
        journal.append("approval.waiting", chat="c1", name="execute_command", reason="Run `rm -rf build`")
        journal.append("tool.remote", tool="x")
        journal.append("run.finished", chat="c2", answer="**Deployed.**")
        got = (await local.get(f"/api/notices?since={mark}")).json()
        assert [n["kind"] for n in got["notices"]] == ["approval.waiting", "run.finished"]
        assert got["notices"][0]["chat"] == "c1" and "rm -rf build" in got["notices"][0]["text"]
        assert got["notices"][1]["text"] == "Deployed." and got["next"] > mark
        again = (await local.get(f"/api/notices?since={got['next']}")).json()
        assert again["notices"] == []
        await local.aclose()


# ------------------------------------------------------------------ through the PC


async def test_the_phone_reaches_a_server_through_the_pc_and_the_pcs_token_stays_there(monkeypatch, settings):
    """The phone has the PC's bridge token; through the PC it uses a server's API and socket.
    The token is not passed on; another body's key does not open this way."""
    import threading
    import time

    import uvicorn
    from fastapi import FastAPI

    import core.settings as settings_module
    import server.app as app_module
    import server.bodies as bodies_module
    import server.remote_auth as remote_auth
    import server.ws as ws_module
    from core.servers.registry import ServerRecord
    from server.app import create_app

    remote = FastAPI()
    seen: dict = {}

    @remote.get("/api/echo")
    async def echo(request: Request) -> dict:
        seen["query"], seen["auth"] = str(request.url.query), request.headers.get("authorization")
        return {"ok": True}

    @remote.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        seen["ws_query"] = str(websocket.url.query)
        await websocket.accept()
        await websocket.send_json({"type": "ready"})

    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(remote, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.02)

    settings.bridge_token = "phone-secret-token"
    for mod in (settings_module, app_module, ws_module, remote_auth):
        monkeypatch.setattr(mod, "get_settings", lambda: settings)

    class Tunnel:
        state, agent, error, local_port = "online", "ok", "", port
        record = ServerRecord(id="srv1", name="vps", host="203.0.113.7")

        def public(self):
            return {"state": "online", "agent": "ok", "error": "", "status": {}, "last_seen": 1.0}

    class Tunnels:
        def get(self, sid):
            return Tunnel() if sid == "srv1" else None

        def all(self):
            return [Tunnel()]

        async def sync(self):
            return None

        async def stop(self):
            return None

    async def fake(app):
        app.state.tunnels = Tunnels()

    monkeypatch.setattr(bodies_module, "start_tunnels", fake)
    pc = create_app()
    try:
        async with pc.router.lifespan_context(pc):
            phone = httpx.ASGITransport(app=pc, client=("192.168.1.50", 5555))
            async with httpx.AsyncClient(transport=phone, base_url="http://pc") as net:
                r = await net.get("/b/srv1/api/echo?x=1&token=phone-secret-token")
                assert r.status_code == 200 and seen["query"] == "x=1" and seen["auth"] is None
                r = await net.get("/b/srv1/api/echo", headers={"Authorization": "Bearer phone-secret-token"})
                assert r.status_code == 200 and seen["auth"] is None              # stays on the PC
                assert (await net.get("/api/bodies?token=phone-secret-token")).status_code == 200
                assert (await net.get("/b/srv1/api/echo")).status_code == 401      # no token: no way
                assert (await net.post("/api/servers/srv1/remote-access?token=phone-secret-token",
                                       json={"enabled": True})).status_code == 403  # the owner's PC only
    finally:
        srv.should_exit = True
        thread.join(10)


async def test_the_pc_makes_the_phones_qr_for_a_server(monkeypatch, settings):
    import threading
    import time
    from urllib.parse import parse_qs, urlparse

    import uvicorn
    from fastapi import FastAPI

    import core.settings as settings_module
    import server.app as app_module
    import server.bodies as bodies_module
    import server.remote_auth as remote_auth
    import server.ws as ws_module
    from core.servers.registry import ServerRecord
    from server.app import create_app

    remote = FastAPI()

    @remote.post("/api/bodies/pair-code")
    async def pair_code() -> dict:
        return {"code": "ABCD2345", "expires_in": 600, "body": {"id": "b-srv"},
                "remote": {"enabled": True, "port": 8443, "fingerprint": "f" * 64, "error": ""}}

    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(remote, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.02)
    settings.bridge_token, settings.bridge_lan = "tok", True
    for mod in (settings_module, app_module, ws_module, remote_auth):
        monkeypatch.setattr(mod, "get_settings", lambda: settings)

    class Tunnel:
        state, agent, error, local_port = "online", "ok", "", port
        record = ServerRecord(id="srv1", name="vps", host="203.0.113.7", body_id="b-srv", mode="owner")

    class Tunnels:
        def get(self, sid):
            return Tunnel() if sid == "srv1" else None

        def all(self):
            return []

        async def sync(self):
            return None

        async def stop(self):
            return None

    async def fake(app):
        app.state.tunnels = Tunnels()

    monkeypatch.setattr(bodies_module, "start_tunnels", fake)
    pc = create_app()
    try:
        async with pc.router.lifespan_context(pc):
            class Lan:
                port = 52476

                def listening(self):
                    return ["192.168.8.42"]

                async def close(self):
                    return None

            pc.state.lan = Lan()
            local = httpx.AsyncClient(transport=httpx.ASGITransport(app=pc), base_url="http://pc")
            d = (await local.get("/api/servers/srv1/phone")).json()
            assert d["ok"] and d["relay"] and d["direct"] and d["code"] == "ABCD2345" and "<svg" in (d["qr_svg"] or "<svg")
            link = urlparse(d["link"])
            q = {k: v[0] for k, v in parse_qs(link.query).items()}
            assert link.scheme == "altair" and link.netloc == "body"
            assert q == {"n": "vps", "s": "srv1", "b": "b-srv", "c": "ABCD2345", "u": "http://192.168.8.42:52476",
                         "t": "tok", "d": "https://203.0.113.7:8443", "fp": "f" * 64}
            # A sandboxed server has no direct door (its port is the server's own).
            Tunnel.record.mode = "sandbox"
            r = (await local.post("/api/servers/srv1/remote-access", json={"enabled": True})).json()
            assert r["ok"] is False and "sandbox" in r["error"].lower()
            await local.aclose()
    finally:
        srv.should_exit = True
        thread.join(10)


def test_the_spec_vectors_hold():
    """The vectors the Android app pins in its own tests (PHONE_SERVER_SPEC.md §9)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.bodies import body_id

    assert body_id(bytes(range(1, 33))) == "riFsLvUkejeCwTXv"
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    assert base64.b64encode(pub).decode() == "A6EHv/POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg="
    assert body_id(pub) == "Vkdap1RjR0wChd9d"
    sig = base64.b64encode(key.sign(signed_message("NONCE123", "riFsLvUkejeCwTXv"))).decode()
    assert sig == "HT1CqEH/n4RH36kQmXLrIuoO4vqO2IOd8EIAaJc4SaTf8VoisqjMln6NdR+LfizRB2WFEfHKWMjSW48yw+cHBg=="
    spec = (__import__("pathlib").Path(__file__).resolve().parents[1] / "server" / "PHONE_SERVER_SPEC.md").read_text(encoding="utf-8")
    assert "riFsLvUkejeCwTXv" in spec and sig in spec


def test_the_phones_address_is_the_physical_adapter_not_a_virtual_switch(monkeypatch):
    """Found live: of 192.168.8.42 (Wi-Fi) and 192.168.144.1 (a Hyper-V switch) the QR carried
    the virtual one — equal ranks, the order decided. With a VPN the default route is the tunnel."""
    import core.pairing as pairing

    monkeypatch.setattr(pairing, "physical_ip", lambda: "192.168.8.42")
    monkeypatch.setattr(pairing, "route_ip", lambda: "198.18.0.1")
    url = pairing.bridge_base_url(port=52476, listening=["192.168.144.1", "192.168.8.42", "100.102.19.29"])
    assert url == "http://192.168.8.42:52476"
    monkeypatch.setattr(pairing, "physical_ip", lambda: "")
    monkeypatch.setattr(pairing, "route_ip", lambda: "192.168.8.42")
    assert pairing.bridge_base_url(port=1, listening=["192.168.144.1", "192.168.8.42"]) == "http://192.168.8.42:1"
