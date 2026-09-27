"""Chat titles written by the model, renaming, search in chat contents, per-model API keys."""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from core.agent.session import Session
from core.agent.storage import SessionStore
from core.agent.titler import clean_title, fallback_title, generate_title
from core.llm.base import AssistantTurn
from tests.fakes import ScriptedLLM

pytest.importorskip("httpx")

# ------------------------------------------------------------------ titler


def test_clean_title_strips_labels_quotes_and_period():
    assert clean_title('Title: "Погода в Москве".') == "Погода в Москве"
    assert clean_title("**Refactor the parser**\nextra line") == "Refactor the parser"
    assert clean_title("«Список покупок»") == "Список покупок"
    assert clean_title("") == ""
    long = clean_title("word " * 40)
    assert len(long) <= 60 and long.endswith("…")


def test_fallback_title_is_the_first_line():
    assert fallback_title("привет\nвторая") == "привет"
    assert fallback_title("x" * 80) == "x" * 50 + "..."


class _TitleLLM:
    def __init__(self, reply: str = "", fail: bool = False, delay: float = 0.0) -> None:
        self.reply, self.fail, self.delay = reply, fail, delay
        self.prompts: list[str] = []
        self.closed = False

    async def complete(self, messages, **kw):
        self.prompts.append(messages[-1]["content"])
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("502")
        return AssistantTurn(content=self.reply)

    async def aclose(self):
        self.closed = True


async def test_generate_title_asks_the_model_and_closes_the_client():
    llm = _TitleLLM("Title: Погода в Москве.")
    title = await generate_title("какая погода в москве?", lambda **kw: llm, {"model": "m"})
    assert title == "Погода в Москве"
    assert "какая погода в москве?" in llm.prompts[0] and llm.closed


async def test_generate_title_returns_none_on_failure_and_timeout():
    assert await generate_title("q", lambda **kw: _TitleLLM(fail=True), {}) is None
    assert await generate_title("q", lambda **kw: _TitleLLM("x", delay=5), {}, timeout=0.05) is None

    def broken(**kw):
        raise RuntimeError("no key")

    assert await generate_title("q", broken, {}) is None


# ------------------------------------------------------------------ the server flow


class _ChatLLM(ScriptedLLM):
    """Answers title requests itself (slowly) and the chat from the script."""

    def __init__(self, turns, title: str | None, title_delay: float) -> None:
        super().__init__(turns)
        self.title, self.title_delay = title, title_delay

    async def complete(self, messages, **kw):
        if str(messages[-1].get("content", "")).startswith("Write a short title"):
            await asyncio.sleep(self.title_delay)
            if self.title is None:
                raise RuntimeError("title model down")
            return AssistantTurn(content=self.title)
        return await super().complete(messages, **kw)

    async def aclose(self):
        return None


def _app(monkeypatch, settings, llm):
    import core.settings as settings_module
    import server.app as app_module
    import server.ws as ws_module
    from server.app import create_app

    s = settings.model_copy(update={"chat_titles": True, "health_gate": False, "verification_gate": False})
    for mod in (settings_module, app_module, ws_module):
        monkeypatch.setattr(mod, "get_settings", lambda s=s: s)
    monkeypatch.setattr(ws_module, "build_llm_client", lambda model=None, **kw: llm)
    return create_app(), s


def _collect(ws, until: set[str], limit: int = 150) -> list[dict]:
    out = []
    for _ in range(limit):
        m = ws.receive_json()
        out.append(m)
        if m["type"] in until:
            return out
    raise AssertionError([m["type"] for m in out])


def _stored(settings, session_id: str, timeout: float = 3.0) -> Session | None:
    store = SessionStore(settings=settings)
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = store.load(session_id)
        if s is not None:
            return s
        time.sleep(0.02)
    return None


def test_model_title_arrives_is_stored_and_stays_out_of_the_answer(monkeypatch, settings):
    llm = _ChatLLM([AssistantTurn(content="Сейчас +12, облачно.")], title="Погода в Москве", title_delay=0.3)
    app, s = _app(monkeypatch, settings, llm)
    with TestClient(app) as tc, tc.websocket_connect("/ws") as ws:
        ready = ws.receive_json()
        ws.send_json({"type": "run", "task": "какая погода в москве?"})
        events = _collect(ws, {"run.finished"})
        titles = [e for e in events if e["type"] == "session.title"]
        if not titles:  # the title may come after the (instant) scripted answer
            titles = [e for e in _collect(ws, {"session.title"}) if e["type"] == "session.title"]
        final = next(e for e in events if e["type"] == "run.finished")
    session_id = ready["session"]["id"] if "session" in ready else titles[0]["session_id"]
    assert titles[0]["title"] == "Погода в Москве" and titles[0]["session_id"] == session_id
    assert not titles[0].get("renamed")
    assert "Погода в Москве" not in final["text"]
    stored = _stored(s, session_id)
    assert stored is not None
    deadline = time.time() + 3
    while stored.title != "Погода в Москве" and time.time() < deadline:
        time.sleep(0.05)
        stored = _stored(s, session_id)
    assert stored.title == "Погода в Москве"


def test_chat_is_stored_as_new_chat_before_the_title_is_ready(monkeypatch, settings):
    """The chat is saved as soon as the user's message is in: it is in the list with the
    placeholder title while the model is still thinking of one."""
    llm = _ChatLLM([AssistantTurn(content="готово")], title="Длинное раздумье", title_delay=1.5)
    app, s = _app(monkeypatch, settings, llm)
    with TestClient(app) as tc, tc.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "run", "task": "сделай что-нибудь"})
        _collect(ws, {"run.finished"})
        listed = tc.get("/api/sessions").json()["sessions"]
        assert listed  # in the rail before the answer, not only after it
        title = next(e for e in _collect(ws, {"session.title"}) if e["type"] == "session.title")
        assert title["title"] == "Длинное раздумье"
    assert _stored(s, title["session_id"]).title == "Длинное раздумье"


def test_title_falls_back_to_the_first_line_when_the_model_fails(monkeypatch, settings):
    llm = _ChatLLM([AssistantTurn(content="ок")], title=None, title_delay=0.0)
    app, _ = _app(monkeypatch, settings, llm)
    with TestClient(app) as tc, tc.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "run", "task": "почини сборку\nподробности ниже"})
        events = _collect(ws, {"run.finished"})
        titles = [e for e in events if e["type"] == "session.title"] or \
            [e for e in _collect(ws, {"session.title"}) if e["type"] == "session.title"]
    assert titles[0]["title"] == "почини сборку"


def test_no_title_request_when_titles_are_off(monkeypatch, settings):
    llm = _ChatLLM([AssistantTurn(content="ок")], title="Не должно быть", title_delay=0.0)
    app, s = _app(monkeypatch, settings, llm)
    s.chat_titles = False
    with TestClient(app) as tc, tc.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "run", "task": "первая строка"})
        events = _collect(ws, {"run.finished"})
    assert not [e for e in events if e["type"] == "session.title"]
    assert not any(str(c["messages"][-1].get("content", "")).startswith("Write a short title") for c in llm.calls)


def test_rename_current_and_other_chat(monkeypatch, settings):
    llm = _ChatLLM([AssistantTurn(content="ок")], title="Модельное", title_delay=1.0)
    app, s = _app(monkeypatch, settings, llm)
    other = Session(title="Старый чат")
    other.add_user("старое сообщение")
    SessionStore(settings=s).save(other)
    with TestClient(app) as tc, tc.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "run", "task": "новая задача"})
        started = _collect(ws, {"run.finished"})
        # The user renames the chat before the model's title comes: the user's name wins.
        ws.send_json({"type": "rename_session", "session_id": other.id, "title": "  Переименован  "})
        ren_other = next(e for e in _collect(ws, {"session.title"}) if e["type"] == "session.title")
        listed = tc.get("/api/sessions").json()["sessions"]
        current_id = next(x["id"] for x in listed if x["id"] != other.id)
        ws.send_json({"type": "rename_session", "session_id": current_id, "title": "Мой чат"})
        ren_cur = next(e for e in _collect(ws, {"session.title"}) if e["type"] == "session.title")
        time.sleep(1.3)  # past the model's title delay: it must not overwrite the user's name
    assert started
    assert ren_other == {"type": "session.title", "session_id": other.id, "title": "Переименован", "renamed": True}
    assert ren_cur["renamed"] and ren_cur["title"] == "Мой чат"
    assert _stored(s, other.id).title == "Переименован"
    assert _stored(s, current_id).title == "Мой чат"


def test_search_returns_one_row_per_chat_with_the_matching_fragment(monkeypatch, settings):
    app, s = _app(monkeypatch, settings, _ChatLLM([], title=None, title_delay=0))
    store = SessionStore(settings=s)
    a = Session(title="Рецепты")
    a.append_timeline({"kind": "user", "text": "как испечь хлеб"})
    a.append_timeline({"kind": "answer", "text": "Про муку и воду.\n\n" * 20 + "Нужна закваска. " * 3})
    b = Session(title="Закваска отдельно")
    b.append_timeline({"kind": "user", "text": "ничего общего"})
    c = Session(title="Другое")
    c.append_timeline({"kind": "user", "text": "погода"})
    for x in (a, b, c):
        store.save(x)
    with TestClient(app) as tc:
        res = tc.get("/api/sessions/search", params={"q": "закваска"}).json()["results"]
        empty = tc.get("/api/sessions/search", params={"q": " "}).json()["results"]
    ids = [r["id"] for r in res]
    assert a.id in ids and b.id in ids and c.id not in ids
    assert len(ids) == len(set(ids))
    row_a = next(r for r in res if r["id"] == a.id)
    assert "закваска" in row_a["snippet"].lower() and row_a["role"] == "agent"
    assert row_a["snippet"].lower().find("закваска") < 45  # visible in the rail's two lines
    assert empty == []


# ------------------------------------------------------------------ per-model keys


def _providers(settings):
    from core import providers

    providers.save([
        {"name": "Gate", "base_url": "https://gate.example/v1", "api_key": "prov-key",
         "models": [{"id": "shared-model"}, {"id": "own-model", "api_key": "model-key"}, {"id": ""}]},
        {"name": "Other", "base_url": "https://other.example/v1", "api_key": "other-key",
         "models": [{"id": "shared-model", "api_key": "other-model-key"}]},
    ], settings)
    return providers


def test_credentials_prefer_the_models_own_key_and_the_active_endpoint(settings):
    providers = _providers(settings)
    assert all(m["id"] for p in providers.load(settings) for m in p["models"])  # empty ids dropped
    assert providers.credentials_for("own-model", settings=settings) == {
        "base_url": "https://gate.example/v1", "api_key": "model-key"}
    s = settings.model_copy(update={"llm_base_url": "https://gate.example/v1/"})
    assert providers.credentials_for("shared-model", settings=s)["api_key"] == "prov-key"
    assert providers.credentials_for("shared-model", "https://other.example/v1", s)["api_key"] == "other-model-key"
    assert providers.credentials_for("unknown", settings=s) == {}


def test_build_llm_client_uses_the_models_key_everywhere(settings):
    from core.llm.openai_client import build_llm_client

    _providers(settings)
    s = settings.model_copy(update={"llm_base_url": "https://gate.example/v1", "llm_api_key": "global-key"})
    own = build_llm_client("own-model", s)
    assert own._client.api_key == "model-key" and own.base_url == "https://gate.example/v1"
    # A routing tier's stale copy of the key loses to the list's current key.
    tier = build_llm_client("own-model", s, base_url="https://gate.example/v1", api_key="stale")
    assert tier._client.api_key == "model-key"
    # A model that is not in the list keeps the per-call key, then the global one.
    assert build_llm_client("elsewhere", s, api_key="call-key")._client.api_key == "call-key"
    assert build_llm_client("elsewhere", s)._client.api_key == "global-key"


def test_providers_endpoint_round_trip(monkeypatch, settings):
    app, s = _app(monkeypatch, settings, _ChatLLM([], title=None, title_delay=0))
    with TestClient(app) as tc:
        assert tc.get("/api/providers").json() == {"providers": []}
        body = {"providers": [{"name": "P", "base_url": "https://p/v1", "models": [{"id": "m", "api_key": "k"}]}]}
        assert tc.post("/api/providers", json=body).json()["ok"]
        assert tc.get("/api/providers").json()["providers"][0]["models"][0]["api_key"] == "k"
        assert not tc.post("/api/providers", json={"providers": "nope"}).json()["ok"]


# ------------------------------------------------------------------ fixes after the first try


async def test_title_tries_the_next_model_when_one_fails():
    built: list[str] = []

    def build(model=None, **kw):
        built.append(model)
        return _TitleLLM(fail=True) if model == "router-without-key" else _TitleLLM("Столица Франции")

    title = await generate_title("столица франции?", build, [{"model": "router-without-key"}, {"model": "strong"}])
    assert title == "Столица Франции" and built == ["router-without-key", "strong"]


def _status_error(cls, code: int):
    import httpx

    response = httpx.Response(code, request=httpx.Request("POST", "https://gw.example/v1/chat/completions"))
    return cls(f"Error code: {code}", response=response, body=None)


async def test_forbidden_key_fails_at_once_but_gateway_errors_are_retried(settings, monkeypatch):
    import openai

    import core.llm.openai_client as oc
    from core.errors import LLMError

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(oc.asyncio, "sleep", no_sleep)
    client = oc.OpenAICompatClient(settings=settings, model="glm", api_key="k")
    calls = {"n": 0}

    def failing(exc):
        async def once(*a, **kw):
            calls["n"] += 1
            raise exc
        return once

    monkeypatch.setattr(client, "_stream_once", failing(_status_error(openai.PermissionDeniedError, 403)))
    with pytest.raises(LLMError, match="403"):
        await client.complete([{"role": "user", "content": "hi"}])
    assert calls["n"] == 1  # a key that is not allowed for the model is not retried

    calls["n"] = 0
    monkeypatch.setattr(client, "_stream_once", failing(_status_error(openai.AuthenticationError, 401)))
    with pytest.raises(LLMError, match="401"):
        await client.complete([{"role": "user", "content": "hi"}])
    assert calls["n"] == 1

    calls["n"] = 0
    monkeypatch.setattr(client, "_stream_once", failing(_status_error(openai.InternalServerError, 502)))
    with pytest.raises(LLMError):
        await client.complete([{"role": "user", "content": "hi"}])
    assert calls["n"] > 1  # a flaky gateway still gets its retries


class _SlowLLM(ScriptedLLM):
    async def complete(self, messages, **kw):
        await asyncio.sleep(1.0)
        return await super().complete(messages, **kw)


def test_new_chat_is_listed_while_the_first_answer_is_still_coming(monkeypatch, settings):
    app, s = _app(monkeypatch, settings, _SlowLLM([AssistantTurn(content="ок")]))
    s.chat_titles = False
    with TestClient(app) as tc, tc.websocket_connect("/ws") as ws:
        ready = ws.receive_json()
        ws.send_json({"type": "run", "task": "долгий ответ"})
        _collect(ws, {"state"})
        deadline, listed = time.time() + 0.8, []
        while time.time() < deadline and not listed:
            listed = [x for x in tc.get("/api/sessions").json()["sessions"] if x["id"] == ready["session_id"]]
        assert listed  # in the rail before the answer, not only after it
        _collect(ws, {"run.finished"})


def test_ui_is_revalidated_not_cached(monkeypatch, settings):
    app, _ = _app(monkeypatch, settings, _ChatLLM([], title=None, title_delay=0))
    with TestClient(app) as tc:
        assert tc.get("/").headers.get("cache-control") == "no-cache"
        assert tc.get("/static/redesign.js").headers.get("cache-control") == "no-cache"
