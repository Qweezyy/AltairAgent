"""The bodies in the window: the registry, the live status and the way into a server's agent.

    GET  /api/body/status            this body: what it is and its load (asked by the PC every 15 s)
    GET  /api/bodies                 this PC and every server: online or not, load, labels
    POST /api/bodies/{id}/labels     the owner's labels for a body
    POST /api/bodies/{id}/reconnect  try the server's tunnel again now
    *    /b/{id}/{path}              the server's own API, through its tunnel (HTTP and WebSocket)

The proxy is how the window shows a server's chats, terminal and files with the very same panels:
it sends what it would send to /api/… and /ws… to /b/<server>/… instead (static/bodies.js). On the
server the request arrives from its own 127.0.0.1, through SSH with this PC's key. Only the window on
this PC may use it — the phone bridge (LAN) gets 403.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from core.i18n import tr
from core.logging_setup import get_logger

logger = get_logger("server.bodies")

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}
#: Hop-by-hop headers and ones httpx sets itself: not passed through.
_SKIP_REQUEST = {"host", "connection", "keep-alive", "transfer-encoding", "upgrade", "content-length",
                 "accept-encoding", "te", "trailer", "proxy-authorization", "proxy-connection"}
_SKIP_RESPONSE = {"connection", "keep-alive", "transfer-encoding", "content-encoding", "content-length", "te",
                  "trailer", "upgrade"}


def _local(host: str | None) -> bool:
    return (host or "") in _LOOPBACK


def _self_card(app: FastAPI) -> dict[str, Any]:
    from core.bodies import get_gate

    settings = app.state.settings
    return get_gate(settings.data_dir / "identity", settings.body_kind).identity.card()


def install(app: FastAPI) -> None:
    app.state.http_proxy = None

    def proxy_client() -> httpx.AsyncClient:
        if app.state.http_proxy is None:
            # No timeout on reading: an export or a long answer streams for as long as it takes.
            # Idle connections go before uvicorn's own 5 s keep-alive ends them: otherwise a
            # request can go out on a connection the server is closing at that moment.
            app.state.http_proxy = httpx.AsyncClient(timeout=httpx.Timeout(30, read=None),
                                                     limits=httpx.Limits(keepalive_expiry=3))
        return app.state.http_proxy

    def port_of(bid: str) -> int:
        tunnels = getattr(app.state, "tunnels", None)
        tunnel = tunnels.get(bid) if tunnels is not None else None
        if tunnel is None:
            raise HTTPException(status_code=404, detail="no such body")
        if tunnel.state != "online" or not tunnel.local_port:
            raise HTTPException(status_code=503, detail=tr("body.offline", name=tunnel.record.name or tunnel.record.host))
        return tunnel.local_port

    def running_tasks() -> int:
        chats = getattr(app.state, "chats", None)
        return len(chats.running_ids()) if chats is not None else 0

    @app.get("/api/body/status")
    async def body_status() -> dict:
        from core.body_status import status

        card = await asyncio.to_thread(_self_card, app)
        return await asyncio.to_thread(status, card, app.state.settings.data_dir, running_tasks())

    @app.get("/api/bodies")
    async def bodies_list(request: Request) -> dict:
        if not _local(request.client.host if request.client else ""):
            raise HTTPException(status_code=403, detail=tr("api.local_only"))
        from core.body_labels import BodyLabels
        from core.body_status import status
        from core.servers.registry import ServerStore

        settings = app.state.settings
        labels = await asyncio.to_thread(BodyLabels(settings.data_dir).all)
        card = await asyncio.to_thread(_self_card, app)
        me = await asyncio.to_thread(status, card, settings.data_dir, running_tasks())
        bodies: list[dict[str, Any]] = [{
            "id": card["id"], "kind": settings.body_kind, "name": card.get("name") or "", "self": True,
            "state": "online", "agent": "ok", "status": me, "labels": labels.get(card["id"], []),
        }]
        tunnels = getattr(app.state, "tunnels", None)
        for record in await asyncio.to_thread(ServerStore(settings.data_dir).all):
            tunnel = tunnels.get(record.id) if tunnels is not None else None
            live = tunnel.public() if tunnel is not None else {"state": "offline", "agent": "", "error": "",
                                                               "status": None, "last_seen": 0.0}
            bodies.append({
                "id": record.id, "body_id": record.body_id, "kind": "server", "self": False,
                "name": record.name or record.host, "host": record.host, "mode": record.mode,
                "version": record.version, "system": record.system, "arch": record.arch,
                "labels": labels.get(record.id, []), **{k: v for k, v in live.items() if k != "port"},
            })
        from dataclasses import asdict

        from core.bodies import get_gate

        gate = get_gate(settings.data_dir / "identity", settings.body_kind)
        trusted = await asyncio.to_thread(gate.trust.all)
        return {"bodies": bodies, "self": card,
                "trusted": [{**asdict(b), "revoked": b.revoked} for b in trusted]}

    @app.post("/api/bodies/{bid}/labels")
    async def bodies_labels(request: Request, bid: str, payload: dict) -> dict:
        if not _local(request.client.host if request.client else ""):
            raise HTTPException(status_code=403, detail=tr("api.local_only"))
        from core.body_labels import BodyLabels

        raw = payload.get("labels") or []
        if not isinstance(raw, list):
            raise HTTPException(status_code=400, detail="labels: a list of strings")
        saved = await asyncio.to_thread(BodyLabels(app.state.settings.data_dir).set, bid, raw)
        return {"ok": True, "labels": saved}

    @app.post("/api/bodies/{bid}/reconnect")
    async def bodies_reconnect(request: Request, bid: str) -> dict:
        if not _local(request.client.host if request.client else ""):
            raise HTTPException(status_code=403, detail=tr("api.local_only"))
        tunnels = getattr(app.state, "tunnels", None)
        tunnel = tunnels.get(bid) if tunnels is not None else None
        if tunnel is None:
            raise HTTPException(status_code=404, detail="no such body")
        tunnel.reconnect_now()
        return {"ok": True}

    @app.api_route("/b/{bid}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def body_proxy(request: Request, bid: str, path: str) -> Response:
        if not _local(request.client.host if request.client else ""):
            raise HTTPException(status_code=403, detail=tr("api.local_only"))
        port = port_of(bid)
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _SKIP_REQUEST}
        target = httpx.URL(f"http://127.0.0.1:{port}/{path}", query=request.url.query.encode())
        content = await request.body()
        # A read can be sent again if the connection broke under it; a change is never repeated.
        tries = 2 if request.method in ("GET", "HEAD") else 1
        for attempt in range(tries):
            upstream = proxy_client().build_request(request.method, target, headers=headers, content=content)
            try:
                reply = await proxy_client().send(upstream, stream=True)
                break
            except httpx.TransportError as exc:
                if attempt + 1 < tries:
                    continue
                logger.info("body proxy %s %s: %r", request.method, path, exc)
                raise HTTPException(status_code=502, detail=tr("body.unreachable", why=str(exc) or type(exc).__name__)) from exc
        out_headers = {k: v for k, v in reply.headers.items() if k.lower() not in _SKIP_RESPONSE}
        return StreamingResponse(reply.aiter_raw(), status_code=reply.status_code, headers=out_headers,
                                 background=BackgroundTask(reply.aclose))

    @app.websocket("/b/{bid}/{path:path}")
    async def body_proxy_ws(websocket: WebSocket, bid: str, path: str) -> None:
        if not _local(websocket.client.host if websocket.client else ""):
            await websocket.close(code=4403)
            return
        try:
            port = port_of(bid)
        except HTTPException:
            await websocket.close(code=4503)
            return
        from websockets.asyncio.client import connect
        from websockets.exceptions import WebSocketException

        query = websocket.url.query
        url = f"ws://127.0.0.1:{port}/{path}" + (f"?{query}" if query else "")
        try:
            upstream = await connect(url, max_size=None, open_timeout=15)
        except (OSError, WebSocketException, TimeoutError) as exc:
            logger.info("body ws %s: %s", url, exc)
            await websocket.close(code=4502)
            return
        await websocket.accept()

        async def down() -> None:     # server → window
            async for message in upstream:
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_text(message)

        async def up() -> None:       # window → server
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                if message.get("bytes") is not None:
                    await upstream.send(message["bytes"])
                elif message.get("text") is not None:
                    await upstream.send(message["text"])

        tasks = [asyncio.create_task(down()), asyncio.create_task(up())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            raise
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, WebSocketException, OSError):
                    await task
            await upstream.close()
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await websocket.close()


async def start_tunnels(app: FastAPI) -> None:
    """Tunnels to every server added, kept in step with the list (server/app.py calls it)."""
    from core.servers.registry import ServerStore
    from core.servers.tunnel import TunnelManager

    app.state.tunnels = TunnelManager(ServerStore(app.state.settings.data_dir))
    await app.state.tunnels.sync()


async def stop_tunnels(app: FastAPI) -> None:
    tunnels = getattr(app.state, "tunnels", None)
    if tunnels is not None:
        await tunnels.stop()
    client = getattr(app.state, "http_proxy", None)
    if client is not None:
        await client.aclose()
        app.state.http_proxy = None
