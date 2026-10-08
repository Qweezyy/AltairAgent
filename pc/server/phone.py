"""What a body offers the phone (0.3.0 stage 6): pairing by a one-time code, its own TLS door,
the news, and a stop for everything.

On every body:
    POST /api/bodies/pair-code   (this machine only) a one-time code for a new body, 10 minutes
    POST /api/bodies/pair        (open, the code is the key) {code, card}: the card is trusted
    GET  /api/notices?since=N    the news since Journal record N (a phone polling in the background)
    POST /api/runs/stop          stop every task running here (the phone's stop button)
    GET/POST /api/remote-access  the TLS door (POST from this machine only)
On the PC (for a server added there, through its tunnel):
    POST /api/servers/{id}/remote-access   turn the server's door on or off
    GET  /api/servers/{id}/phone           the QR and link that pair the phone with that server

docs: pc/server/PHONE_SERVER_SPEC.md (the contract for the Android app).
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Request

from core.i18n import tr
from core.logging_setup import get_logger

logger = get_logger("server.phone")


def _auth(request: Request) -> str:
    return str(request.scope.get("altair_auth") or "")


def _local(request: Request) -> None:
    if _auth(request) != "loopback":
        raise HTTPException(status_code=403, detail=tr("api.local_only"))


def body_link(*, name: str, server_id: str, body_id: str, code: str, relay: dict[str, str] | None,
              direct: dict[str, Any] | None) -> str:
    """altair://body?…: everything the phone needs to reach a server and be trusted there."""
    parts = [("n", name), ("s", server_id), ("b", body_id), ("c", code)]
    if relay:
        parts += [("u", relay["url"]), ("t", relay["token"])]
    if direct:
        parts += [("d", direct["url"]), ("fp", direct["fingerprint"])]
    return "altair://body?" + "&".join(f"{k}={quote(str(v), safe='')}" for k, v in parts if v)


def install(app: FastAPI) -> None:
    from server.remote_access import RemoteAccess

    app.state.remote = RemoteAccess(app)

    def card() -> dict[str, Any]:
        from core.bodies import get_gate

        settings = app.state.settings
        return get_gate(settings.data_dir / "identity", settings.body_kind).identity.card()

    # ------------------------------------------------------------------ pairing

    @app.post("/api/bodies/pair-code")
    async def pair_code(request: Request) -> dict:
        _local(request)
        from core.bodies import PAIR_CODE_SECONDS, get_gate

        settings = app.state.settings
        code = get_gate(settings.data_dir / "identity", settings.body_kind).new_pair_code()
        return {"code": code, "expires_in": PAIR_CODE_SECONDS, "body": await asyncio.to_thread(card),
                "remote": app.state.remote.status()}

    @app.post("/api/bodies/pair")
    async def pair(payload: dict) -> dict:
        """A new body (the phone) with the one-time code it got from the PC's QR."""
        from core.bodies import get_gate

        settings = app.state.settings
        incoming = payload.get("card")
        if not isinstance(incoming, dict):
            raise HTTPException(status_code=400, detail="card: the body's card (id, name, kind, public_key)")
        try:
            body = await asyncio.to_thread(get_gate(settings.data_dir / "identity", settings.body_kind).pair,
                                           str(payload.get("code") or ""), incoming)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if body is None:
            raise HTTPException(status_code=401, detail="wrong or used code (or too many tries: wait 10 minutes)")
        if getattr(settings, "journal", True):
            from core.journal import get_journal

            await asyncio.to_thread(get_journal(settings.data_dir / "journal").append, "body.paired",
                                    body=body.id, name=body.name, body_kind=body.kind)
        return {"ok": True, "body": await asyncio.to_thread(card)}

    # ------------------------------------------------------------------ news and the stop button

    @app.get("/api/notices")
    async def notices(since: int | None = None, limit: int = 100) -> dict:
        """The news of this body after Journal record `since`, oldest first; `next` is what to ask
        with next time. Without `since`: no news, only where the Journal is now (a first look)."""
        from core.journal import get_journal
        from server.notices import notice_of

        settings = app.state.settings
        journal = get_journal(settings.data_dir / "journal")
        if since is None:
            latest = await asyncio.to_thread(journal.read, limit=1)
            return {"notices": [], "next": int(latest[0]["seq"]) if latest else 0}
        records = await asyncio.to_thread(journal.read, since=since, limit=max(1, min(limit, 500)))
        name = (await asyncio.to_thread(card)).get("name") or ""
        out = [n for r in sorted(records, key=lambda r: int(r["seq"])) if (n := notice_of(r, name)) is not None]
        return {"notices": out, "next": max([int(r["seq"]) for r in records] + [since])}

    @app.post("/api/runs/stop")
    async def stop_all() -> dict:
        """Every task running on this body stops (the phone's stop button)."""
        chats = getattr(app.state, "chats", None)
        stopped = []
        for chat in list(getattr(chats, "chats", {}).values()) if chats is not None else []:
            if chat.running:
                await chat.stop()
                stopped.append(chat.session.id)
        if stopped and getattr(app.state.settings, "journal", True):
            from core.journal import get_journal

            await asyncio.to_thread(get_journal(app.state.settings.data_dir / "journal").append, "runs.stopped",
                                    chats=stopped)
        return {"ok": True, "stopped": stopped}

    # ------------------------------------------------------------------ the TLS door

    @app.get("/api/remote-access")
    async def remote_status(request: Request) -> dict:
        _local(request)
        return app.state.remote.status()

    @app.post("/api/remote-access")
    async def remote_set(request: Request, payload: dict) -> dict:
        _local(request)
        from core.config_file import write_values

        enabled = bool(payload.get("enabled"))
        # Kept in .env (it comes back after a restart) and applied to the running settings as is.
        await asyncio.to_thread(write_values, {"REMOTE_ACCESS": "true" if enabled else "false"}, app.state.settings)
        app.state.settings = app.state.settings.model_copy(update={"remote_access": enabled})
        return await app.state.remote.apply(enabled)

    # ------------------------------------------------------------------ on the PC, for a server

    def tunnel_of(sid: str) -> Any:
        tunnels = getattr(app.state, "tunnels", None)
        tunnel = tunnels.get(sid) if tunnels is not None else None
        if tunnel is None:
            raise HTTPException(status_code=404, detail="no such server")
        if tunnel.state != "online" or not tunnel.local_port:
            raise HTTPException(status_code=503, detail=tr("body.offline", name=tunnel.record.name or tunnel.record.host))
        return tunnel

    @app.post("/api/servers/{sid}/remote-access")
    async def server_remote(request: Request, sid: str, payload: dict) -> dict:
        _local(request)
        tunnel = tunnel_of(sid)
        if tunnel.record.mode == "sandbox" and payload.get("enabled"):
            return {"ok": False, "error": tr("phone.sandbox_direct")}
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.post(f"http://127.0.0.1:{tunnel.local_port}/api/remote-access",
                                json={"enabled": bool(payload.get("enabled"))})
        if r.status_code != 200:
            return {"ok": False, "error": r.text[:300]}
        status = r.json()
        return {"ok": not status.get("error"), **status}

    @app.get("/api/servers/{sid}/phone")
    async def server_phone(request: Request, sid: str) -> dict:
        """The QR the phone scans to reach this server and be trusted there: through this PC (the
        bridge it already knows) and, when the server's door is on, directly with its pinned
        certificate."""
        _local(request)
        tunnel = tunnel_of(sid)
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.post(f"http://127.0.0.1:{tunnel.local_port}/api/bodies/pair-code")
        if r.status_code != 200:
            return {"ok": False, "error": r.text[:300]}
        data = r.json()
        relay = None
        settings = app.state.settings
        lan = getattr(app.state, "lan", None)
        if settings.bridge_lan and settings.bridge_token and lan is not None and lan.listening():
            from core.pairing import bridge_base_url

            # The PC's bridge as the phone's own QR gives it: the phone keeps it as its way in.
            url = bridge_base_url(settings, port=lan.port, listening=lan.listening())
            relay = {"url": url, "token": settings.bridge_token}
        remote = data.get("remote") or {}
        direct = ({"url": f"https://{tunnel.record.host}:{remote.get('port')}", "fingerprint": remote.get("fingerprint")}
                  if remote.get("enabled") else None)
        record = tunnel.record
        link = body_link(name=record.name or record.host, server_id=record.id, body_id=record.body_id or data["body"]["id"],
                         code=data["code"], relay=relay, direct=direct)
        from core.pairing import qr_svg

        return {"ok": True, "link": link, "qr_svg": await asyncio.to_thread(qr_svg, link),
                "expires_in": data.get("expires_in"), "relay": bool(relay), "direct": bool(direct),
                "code": data["code"]}


async def start_remote(app: FastAPI) -> None:
    if getattr(app.state.settings, "remote_access", False) and hasattr(app.state, "remote"):
        await app.state.remote.apply(True)


async def stop_remote(app: FastAPI) -> None:
    remote = getattr(app.state, "remote", None)
    if remote is not None:
        await remote.close()
