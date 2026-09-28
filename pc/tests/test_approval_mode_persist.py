"""The approval mode the user picks stays the default for new chats, restarts and updates."""

from __future__ import annotations

from fastapi.testclient import TestClient

from core.config_file import config_path
from core.settings import Settings


def _app(monkeypatch, settings):
    import core.settings as settings_module
    import server.app as app_module
    import server.ws as ws_module
    from server.app import create_app

    for mod in (settings_module, app_module, ws_module):
        monkeypatch.setattr(mod, "get_settings", lambda s=settings: s)
    return create_app()


def _pick(tc, mode: str) -> dict:
    with tc.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "set_mode", "mode": mode})
        for _ in range(40):
            m = ws.receive_json()
            if m["type"] == "mode.updated":
                return m
    raise AssertionError("no mode.updated")


def test_the_picked_mode_is_saved_as_the_default(monkeypatch, settings):
    settings.approval_mode = "manual"
    with TestClient(_app(monkeypatch, settings)) as tc:
        assert _pick(tc, "bypass")["mode"] == "bypass"
    env = config_path(settings)
    assert "APPROVAL_MODE=bypass" in env.read_text(encoding="utf-8")
    # What the app reads after a restart or an update (the .env in the app data folder).
    assert Settings(app_path=settings.app_dir, _env_file=str(env)).approval_mode == "bypass"


def test_an_unknown_mode_changes_nothing(monkeypatch, settings):
    settings.approval_mode = "manual"
    with TestClient(_app(monkeypatch, settings)) as tc, tc.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "set_mode", "mode": "anything-goes"})
        ws.send_json({"type": "set_mode", "mode": "manual"})
        for _ in range(40):
            if ws.receive_json()["type"] == "mode.updated":
                break
    env = config_path(settings)
    assert "anything-goes" not in (env.read_text(encoding="utf-8") if env.exists() else "")
