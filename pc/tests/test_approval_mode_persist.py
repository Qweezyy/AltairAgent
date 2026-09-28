"""Approval modes: every new chat starts in manual; a chat where the user picked another mode
keeps it across chat switches, restarts and updates (it is stored in the chat itself)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from core.agent.session import Session
from core.agent.storage import SessionStore
from core.config_file import config_path


def _app(monkeypatch, settings):
    import core.settings as settings_module
    import server.app as app_module
    import server.ws as ws_module
    from server.app import create_app

    for mod in (settings_module, app_module, ws_module):
        monkeypatch.setattr(mod, "get_settings", lambda s=settings: s)
    return create_app()


def _until(ws, kind: str) -> dict:
    for _ in range(60):
        m = ws.receive_json()
        if m["type"] == kind:
            return m
    raise AssertionError(f"no {kind}")


def test_a_chat_keeps_its_mode_and_new_chats_start_manual(monkeypatch, settings):
    settings.approval_mode = "manual"
    chat_a = Session(title="A")
    chat_a.add_user("hi")
    SessionStore(settings=settings).save(chat_a)
    app = _app(monkeypatch, settings)

    with TestClient(app) as tc:
        with tc.websocket_connect("/ws") as ws:
            assert ws.receive_json()["approval_mode"] == "manual"          # a new chat: manual
            ws.send_json({"type": "load_session", "session_id": chat_a.id})
            assert _until(ws, "session.loaded")["mode"] == "manual"
            ws.send_json({"type": "set_mode", "mode": "bypass"})
            assert _until(ws, "mode.updated")["mode"] == "bypass"
            ws.send_json({"type": "new_session"})
            assert _until(ws, "session.loaded")["mode"] == "manual"        # chat B: manual again
            ws.send_json({"type": "load_session", "session_id": chat_a.id})
            assert _until(ws, "session.loaded")["mode"] == "bypass"        # back in A: its own mode

        with tc.websocket_connect("/ws") as ws:                             # a restart / an update
            ws.receive_json()
            ws.send_json({"type": "load_session", "session_id": chat_a.id})
            assert _until(ws, "session.loaded")["mode"] == "bypass"

    assert SessionStore(settings=settings).load(chat_a.id).approval_mode == "bypass"
    env = config_path(settings)
    assert "bypass" not in (env.read_text(encoding="utf-8") if env.exists() else "")  # no global default


def test_an_unknown_mode_is_refused(monkeypatch, settings):
    with TestClient(_app(monkeypatch, settings)) as tc, tc.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "set_mode", "mode": "anything-goes"})
        ws.send_json({"type": "set_mode", "mode": "manual"})
        assert _until(ws, "mode.updated")["mode"] == "manual"
