"""The user's profile: a name the agent calls them by and an avatar."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from core.profile import ProfileStore

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _data_url(raw: bytes, kind: str = "png") -> str:
    return f"data:image/{kind};base64," + base64.b64encode(raw).decode()


def test_the_name_is_cleaned_and_told_to_the_agent(settings):
    store = ProfileStore(settings.data_dir)
    assert store.prompt_section() == ""
    assert store.set_name("  Саша   Кurchinsky  " + "x" * 60)[:15] == "Саша Кurchinsky"
    assert len(store.read()["name"]) == 40
    store.set_name("Qweezyy")
    assert "The user's name: Qweezyy" in store.prompt_section()


def test_only_real_pictures_are_taken(settings):
    store = ProfileStore(settings.data_dir)
    with pytest.raises(ValueError):
        store.set_avatar(_data_url(b"<script>alert(1)</script>"))
    with pytest.raises(ValueError):
        store.set_avatar(_data_url(b"\x89PNG\r\n\x1a\n" + b"\x00" * (3 * 1024 * 1024)))
    store.set_avatar(_data_url(PNG))
    assert store.avatar_path().name == "avatar.png" and store.read()["avatar"]
    store.set_avatar(_data_url(b"\xff\xd8\xff" + b"\x00" * 32, "jpeg"))   # a new one replaces the old
    assert store.avatar_path().name == "avatar.jpg"
    store.clear_avatar()
    assert store.avatar_path() is None


def test_the_profile_api_and_the_prompt(monkeypatch, settings):
    import core.settings as settings_module
    import server.app as app_module
    import server.ws as ws_module
    from server.app import create_app

    for mod in (settings_module, app_module, ws_module):
        monkeypatch.setattr(mod, "get_settings", lambda: settings)
    with TestClient(create_app()) as tc:
        assert tc.get("/api/profile").json() == {"name": "", "avatar": False, "avatar_v": 0}
        assert tc.post("/api/profile", json={"name": "Qweezyy"}).json() == {"ok": True, "name": "Qweezyy"}
        assert tc.post("/api/profile/avatar", json={"data": _data_url(PNG)}).json()["ok"]
        assert tc.get("/api/profile/avatar").content == PNG
        assert tc.post("/api/profile/avatar", json={"data": "data:,nope"}).json()["ok"] is False
        assert tc.delete("/api/profile/avatar").json()["ok"]
        assert tc.get("/api/profile/avatar").status_code == 404

    from core.agent.runner import AgentRunner
    from core.agent.session import Session
    from core.tools.registry import ToolRegistry
    from tests.fakes import ScriptedLLM

    runner = AgentRunner(llm=ScriptedLLM([]), registry=ToolRegistry([]), settings=settings, session=Session())
    assert "The user's name: Qweezyy" in runner._system_prompt("hi")
