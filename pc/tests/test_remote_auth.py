"""Remote access control: in LAN mode every route needs the bridge token.

Regression: only /ws checked the token, so with the phone bridge on LAN anyone on the
network could read/write settings, fetch workspace files and open /ws/terminal (a shell).
"""

from __future__ import annotations

import asyncio

import pytest

import server.remote_auth as ra


def _run(scope):
    calls: list[str] = []
    sent: list[dict] = []

    async def app(scope, receive, send):
        calls.append(scope["path"])

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    asyncio.run(ra.RemoteAuthMiddleware(app)(scope, receive, send))
    return calls, sent


def _scope(kind="http", host="192.168.1.20", path="/api/settings", query=b"", headers=()):
    return {"type": kind, "client": (host, 50000), "path": path, "query_string": query, "headers": list(headers)}


@pytest.fixture
def token(monkeypatch, settings):
    settings.bridge_token = "s3cret-token"
    monkeypatch.setattr(ra, "get_settings", lambda: settings)
    return settings.bridge_token


def test_loopback_is_trusted(token):
    for host in ("127.0.0.1", "::1"):
        calls, _ = _run(_scope(host=host))
        assert calls == ["/api/settings"]


def test_remote_http_without_token_is_rejected(token):
    calls, sent = _run(_scope())
    assert calls == []
    assert sent[0]["status"] == 401


def test_remote_terminal_without_token_is_rejected(token):
    calls, sent = _run(_scope(kind="websocket", path="/ws/terminal"))
    assert calls == []
    assert sent == [{"type": "websocket.close", "code": 4401}]


def test_remote_with_token_passes(token):
    assert _run(_scope(query=b"token=s3cret-token"))[0] == ["/api/settings"]
    auth = [(b"authorization", b"Bearer s3cret-token")]
    assert _run(_scope(kind="websocket", path="/ws", headers=auth))[0] == ["/ws"]


def test_wrong_token_and_no_configured_token_are_rejected(token, settings):
    assert _run(_scope(query=b"token=nope"))[0] == []
    settings.bridge_token = ""
    assert _run(_scope(query=b"token="))[0] == []  # empty secret never matches


def test_app_installs_the_middleware():
    from server.app import create_app

    app = create_app()
    assert any(m.cls is ra.RemoteAuthMiddleware for m in app.user_middleware)
