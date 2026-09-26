"""Access control for connections that do not come from this machine.

With the phone bridge on LAN the server listens on 0.0.0.0. Checking the bridge token
only on /ws left every other route open to anyone on the network: settings (read and
write), workspace files, opening programs, and /ws/terminal — a shell on this PC. So
the check sits in front of the whole app: loopback is trusted (it is the app's own
window), everything else must present the bridge token.
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any
from urllib.parse import parse_qs

from core.settings import get_settings

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

#: "testclient" is Starlette's TestClient; a real peer is always an IP address.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def supplied_token(scope: Scope) -> str:
    """Token from ?token= or an `Authorization: Bearer` header."""
    query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
    if query.get("token"):
        return query["token"][0]
    for name, value in scope.get("headers") or ():
        if name.lower() == b"authorization":
            text = value.decode("latin-1")
            if text.lower().startswith("bearer "):
                return text[7:].strip()
    return ""


def is_authorized(scope: Scope) -> bool:
    client = scope.get("client")
    host = (client[0] if client else "") or ""
    if host in LOOPBACK_HOSTS:
        return True
    token = get_settings().bridge_token.strip()
    if not token:
        return False  # remote access stays closed until a secret is set
    given = supplied_token(scope)
    return bool(given) and hmac.compare_digest(given, token)


class RemoteAuthMiddleware:
    """Rejects non-loopback HTTP and WebSocket requests without the bridge token."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and not is_authorized(scope):
            if scope["type"] == "http":
                await send({
                    "type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8")],
                })
                await send({"type": "http.response.body", "body": b"Unauthorized"})
            else:
                # Closing before accept rejects the handshake (HTTP 403).
                await send({"type": "websocket.close", "code": 4401})
            return
        await self.app(scope, receive, send)
