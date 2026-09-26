"""Проверка HTTP/WebSocket слоя без обращения к реальной модели."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.llm.base import AssistantTurn
from tests.fakes import ScriptedLLM, tool_call

pytest.importorskip("httpx")


@pytest.fixture()
def client(monkeypatch, settings):
    import core.settings as settings_module
    import server.app as app_module
    import server.ws as ws_module
    from server.app import create_app

    # Подменяем во всех трёх местах: server.app импортировал get_settings по
    # имени, поэтому патча одного core.settings недостаточно — приложение
    # продолжало бы работать с реальной рабочей папкой пользователя.
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)
    monkeypatch.setattr(app_module, "get_settings", lambda: settings)
    monkeypatch.setattr(ws_module, "get_settings", lambda: settings)

    scripted = ScriptedLLM(
        [
            AssistantTurn(tool_calls=[tool_call("list_directory", path=".")]),
            AssistantTurn(content="В папке пусто."),
        ]
    )
    monkeypatch.setattr(ws_module, "build_llm_client", lambda model=None: scripted)

    with TestClient(create_app()) as test_client:
        yield test_client


def test_health_endpoint(client):
    data = client.get("/api/health").json()
    assert data["status"] == "ok"
    assert data["tools"] >= 12


def test_files_dir_lists_one_level(client, tmp_path):
    ws = tmp_path / "wsroot"
    ws.mkdir()
    (ws / "sub").mkdir()
    (ws / "sub" / "inner.txt").write_text("x", encoding="utf-8")
    (ws / "a.txt").write_text("hello", encoding="utf-8")

    data = client.get(f"/api/files/dir?workspace={ws}").json()
    assert data["ok"] is True
    names = [e["name"] for e in data["entries"]]
    assert names == ["sub", "a.txt"]  # dirs first, then files
    assert data["entries"][0]["dir"] is True
    assert data["entries"][1]["size"] == 5

    # Descend one level.
    sub = client.get(f"/api/files/dir?workspace={ws}&path=sub").json()
    assert [e["name"] for e in sub["entries"]] == ["inner.txt"]


def test_files_dir_rejects_escape(client, tmp_path):
    ws = tmp_path / "wsroot2"
    ws.mkdir()
    # A path trying to climb out of the workspace is refused.
    data = client.get(f"/api/files/dir?workspace={ws}&path=../..").json()
    assert data["ok"] is False


def test_tools_endpoint_exposes_schemas(client):
    tools = client.get("/api/tools").json()["tools"]
    names = {tool["name"] for tool in tools}
    assert {"read_file", "write_file", "execute_command"} <= names
    assert all("schema" in tool for tool in tools)


def test_sessions_endpoint(client):
    res = client.get("/api/sessions").json()
    assert "sessions" in res
    assert isinstance(res["sessions"], list)


def test_browse_endpoint(client, settings):
    res = client.get("/api/browse").json()
    assert "drives" in res
    assert "home" in res

    res_ws = client.get(f"/api/browse?path={settings.workspace}").json()
    assert "folders" in res_ws
    assert res_ws["current"] == str(settings.workspace)


def test_select_folder_dialog_mocked(client, monkeypatch):
    import server.app as app_module

    monkeypatch.setattr(app_module, "pick_folder", lambda initial: ("D:\\MockProject", ""))
    res = client.post("/api/dialog/select-folder", json={"initial_dir": ""}).json()
    assert res["ok"] is True
    assert res["path"] == "D:\\MockProject"


def test_select_folder_dialog_reports_failure(client, monkeypatch):
    """Пользователь обязан увидеть причину, а не молчание."""
    import server.app as app_module

    monkeypatch.setattr(app_module, "pick_folder", lambda initial: (None, "tkinter: нет Tk"))
    res = client.post("/api/dialog/select-folder", json={}).json()
    assert res["ok"] is False
    assert res["cancelled"] is False
    assert "tkinter" in res["error"]


def test_select_folder_dialog_cancel_is_not_an_error(client, monkeypatch):
    import server.app as app_module

    monkeypatch.setattr(app_module, "pick_folder", lambda initial: (None, "cancelled"))
    res = client.post("/api/dialog/select-folder", json={}).json()
    assert res["ok"] is False
    assert res["cancelled"] is True
    assert res["error"] == ""


def test_recent_workspaces_endpoint(client):
    data = client.get("/api/workspace/recent").json()
    assert "recent" in data and isinstance(data["recent"], list)
    assert data["default"]


def test_websocket_distributed_routing(monkeypatch, settings):
    """Роутер разбивает задачу на подзадачи и раздаёт их обеим моделям: лёгкую —
    дешёвой, сложную — сильной, в одной сессии (два прогона подряд)."""
    import core.settings as settings_module
    import server.app as app_module
    import server.ws as ws_module
    from server.app import create_app

    s = settings.model_copy(update={
        "model_routing": True, "model_fast": "cheap", "model_strong": "big",
        "health_gate": False, "verification_gate": False,
    })
    for mod in (settings_module, app_module, ws_module):
        monkeypatch.setattr(mod, "get_settings", lambda s=s: s)

    scripted = ScriptedLLM([
        AssistantTurn(content='{"subtasks":[{"text":"лёгкая правка","tier":"fast"},'
                              '{"text":"сложный рефакторинг","tier":"strong"}]}'),
        AssistantTurn(content="сделал лёгкую"),
        AssistantTurn(content="сделал сложную"),
    ])
    models: list[str | None] = []

    def fake_build(model=None, **kw):
        models.append(model)
        return scripted

    monkeypatch.setattr(ws_module, "build_llm_client", fake_build)

    with TestClient(create_app()) as tc, tc.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "run", "task": "две задачи: правка и рефакторинг"})
        routed, finishes = [], 0
        for _ in range(150):
            m = ws.receive_json()
            if m["type"] == "model.routed":
                routed.append(m["note"])
            if m["type"] == "run.finished":
                finishes += 1
                if finishes >= 2:
                    break

    assert finishes == 2, "должно быть два прогона подзадач"
    # Судья (cheap), затем клиенты тиров: fast=cheap, strong=big.
    assert "big" in models and "cheap" in models
    assert any("fast" in n for n in routed) and any("strong" in n for n in routed)


def test_workspace_locked_after_chat_starts(client, settings, tmp_path):
    """Рабочую папку можно выбрать до первого сообщения; после — нельзя."""
    ws_a = tmp_path / "proj_a"; ws_a.mkdir()
    ws_b = tmp_path / "proj_b"; ws_b.mkdir()
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "ready"
        # до сообщения — смена проходит
        ws.send_json({"type": "set_workspace", "workspace": str(ws_a)})
        upd = _drain_until(ws, {"workspace.updated", "workspace.error"}, limit=40)
        assert upd["type"] == "workspace.updated"
        # начинаем чат
        ws.send_json({"type": "run", "task": "посмотри папку"})
        _drain_until(ws, {"run.finished", "run.failed"}, limit=120)
        # теперь смена запрещена
        ws.send_json({"type": "set_workspace", "workspace": str(ws_b)})
        res = _drain_until(ws, {"workspace.updated", "workspace.error"}, limit=40)
    assert res["type"] == "workspace.error"


def test_websocket_full_run(client):
    with client.websocket_connect("/ws") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["tools"]

        ws.send_json({"type": "run", "task": "посмотри папку"})

        seen: list[str] = []
        final = None
        for _ in range(60):
            message = ws.receive_json()
            seen.append(message["type"])
            if message["type"] == "run.finished":
                final = message
                break

        assert final is not None, seen
        assert final["text"] == "В папке пусто."
        assert "tool.started" in seen and "tool.finished" in seen


def test_websocket_new_and_load_session(client, settings):
    with client.websocket_connect("/ws") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"

        ws.send_json({"type": "new_session", "workspace": str(settings.workspace), "title": "Новый чат 1"})
        # После ready соединение шлёт ещё context.usage — пропускаем до нужного.
        loaded = _drain_until(ws, {"session.loaded"})
        assert loaded["session"]["title"] == "Новый чат 1"


def test_websocket_rejects_empty_task(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # ready
        ws.send_json({"type": "run", "task": "   "})
        message = _drain_until(ws, {"run.failed"})
        assert message["type"] == "run.failed"


# ------------------------------------------- рабочая папка в конкретном чате


def _drain_until(ws, wanted: set[str], limit: int = 80) -> dict:
    """Читает сообщения, пока не встретит одно из ожидаемых."""
    seen = []
    for _ in range(limit):
        message = ws.receive_json()
        seen.append(message["type"])
        if message["type"] in wanted:
            return message
    raise AssertionError(f"не дождались {wanted}, получили: {seen}")


def _wait_for_sessions(client, timeout: float = 3.0) -> list:
    """Ждёт появления сохранённой сессии.

    Сессия пишется в отдельном потоке (`asyncio.to_thread`), а TestClient
    держит приложение в своём цикле событий — между «задача завершена» и
    видимым на диске файлом остаётся зазор в несколько миллисекунд. Без
    ожидания тест падал примерно раз в десять запусков.
    """
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        sessions = client.get("/api/sessions").json()["sessions"]
        if sessions:
            return sessions
        _time.sleep(0.05)
    return []


def _drain_until_state(ws, state: str, limit: int = 120) -> dict:
    """Читает сообщения до нужного состояния соединения."""
    seen = []
    for _ in range(limit):
        message = ws.receive_json()
        seen.append(f"{message['type']}:{message.get('state', '')}")
        if message["type"] == "state" and message.get("state") == state:
            return message
    raise AssertionError(f"не дождались состояния '{state}', получили: {seen}")


def test_set_workspace_switches_folder(client, settings, tmp_path):
    target = tmp_path / "проект_один"
    target.mkdir()

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # ready
        ws.send_json({"type": "set_workspace", "workspace": str(target)})
        message = _drain_until(ws, {"workspace.updated", "workspace.error"})

    assert message["type"] == "workspace.updated"
    assert message["workspace"] == str(target.resolve())


def test_bad_workspace_reports_error_and_keeps_previous(client, tmp_path):
    bad = tmp_path / "нет" / "такой" / "папки"

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "set_workspace", "workspace": str(bad)})
        message = _drain_until(ws, {"workspace.updated", "workspace.error"})

    assert message["type"] == "workspace.error"
    assert "не существует" in message["message"]
    assert not bad.exists()  # мусорные папки не создаются


def test_agent_works_inside_selected_folder(client, settings, tmp_path):
    """Главный сценарий: выбрал папку — инструменты видят именно её содержимое."""
    project = tmp_path / "проект_два"
    project.mkdir()
    (project / "маркер.txt").write_text("я в нужной папке", encoding="utf-8")

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "set_workspace", "workspace": str(project)})
        _drain_until(ws, {"workspace.updated"})

        ws.send_json({"type": "run", "task": "посмотри папку", "workspace": str(project)})
        tool_result = _drain_until(ws, {"tool.finished", "run.failed"}, limit=120)
        finished = _drain_until(ws, {"run.finished", "run.failed"}, limit=120)

    assert finished["type"] == "run.finished"
    # Инструмент обязан увидеть содержимое выбранной папки, а не папки агента.
    assert tool_result["type"] == "tool.finished"
    assert "маркер.txt" in tool_result["output"]


def test_new_chat_can_use_its_own_folder(client, tmp_path):
    first = tmp_path / "чат_один"
    second = tmp_path / "чат_два"
    first.mkdir()
    second.mkdir()

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()

        ws.send_json({"type": "new_session", "workspace": str(first), "title": "Первый"})
        loaded_first = _drain_until(ws, {"session.loaded"})

        ws.send_json({"type": "new_session", "workspace": str(second), "title": "Второй"})
        loaded_second = _drain_until(ws, {"session.loaded"})

    assert loaded_first["workspace"] == str(first.resolve())
    assert loaded_second["workspace"] == str(second.resolve())
    assert loaded_first["session"]["id"] != loaded_second["session"]["id"]


# ------------------------------------------------------- подтверждения в UI


def test_approval_reaches_ui_and_can_be_granted(client, settings, monkeypatch):
    """Запрос подтверждения обязан дойти до интерфейса и слушаться ответа.

    Регрессия: обработчик читал у запроса несуществующее поле `id`, падал,
    и все подтверждения молча отклонялись — пользователь даже не видел карточку.
    """
    import server.ws as ws_module
    from core.llm.base import AssistantTurn
    from tests.fakes import ScriptedLLM, tool_call

    settings.approval_mode = "dangerous"
    scripted = ScriptedLLM(
        [
            AssistantTurn(tool_calls=[tool_call("write_file", path="note.txt", content="привет")]),
            AssistantTurn(content="Файл записан."),
        ]
    )
    monkeypatch.setattr(ws_module, "build_llm_client", lambda model=None: scripted)

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # ready
        # Интерфейс шлёт выбранную папку с каждым запуском; без неё чат пишет в
        # свою личную папку (chat_files/<id>), а не в проект.
        ws.send_json({"type": "run", "task": "запиши файл", "workspace": str(settings.workspace)})

        request = _drain_until(ws, {"approval.requested", "run.failed"})
        assert request["type"] == "approval.requested", "карточка подтверждения не пришла"
        assert request["request_id"], "без идентификатора ответить невозможно"
        assert request["name"] == "write_file"
        assert "note.txt" in str(request["args"])

        ws.send_json({"type": "approval", "request_id": request["request_id"], "approved": True})
        resolved = _drain_until(ws, {"approval.resolved"})
        assert resolved["approved"] is True

        finished = _drain_until(ws, {"run.finished", "run.failed"}, limit=120)

    assert finished["type"] == "run.finished"
    assert (settings.workspace / "note.txt").read_text(encoding="utf-8") == "привет"


def test_approval_denied_by_user_is_reported_to_model(client, settings, monkeypatch):
    import server.ws as ws_module
    from core.llm.base import AssistantTurn
    from tests.fakes import ScriptedLLM, tool_call

    settings.approval_mode = "dangerous"
    scripted = ScriptedLLM(
        [
            AssistantTurn(tool_calls=[tool_call("write_file", path="nope.txt", content="x")]),
            AssistantTurn(content="Понял, не пишу."),
        ]
    )
    monkeypatch.setattr(ws_module, "build_llm_client", lambda model=None: scripted)

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "run", "task": "запиши файл", "workspace": str(settings.workspace)})
        request = _drain_until(ws, {"approval.requested"})
        ws.send_json({"type": "approval", "request_id": request["request_id"], "approved": False})
        _drain_until(ws, {"run.finished", "run.failed"}, limit=120)

    assert not (settings.workspace / "nope.txt").exists()
    tool_messages = [m for m in scripted.calls[-1]["messages"] if m.get("role") == "tool"]
    assert any("отклонил" in m["content"] for m in tool_messages)


# --------------------------------------------------------------- превью файлов


def test_preview_reads_file_content(client, settings):
    """Регрессия: превью тянуло файл через /static/../ и получало только путь."""
    target = settings.workspace / "index.html"
    target.write_text("<h1>Привет</h1>", encoding="utf-8")

    data = client.get(f"/api/file?path={target}").json()

    assert data["ok"] is True
    assert data["kind"] == "html"
    assert data["language"] == "html"
    assert "Привет" in data["content"]


def test_preview_detects_web_languages(client, settings):
    for name, language in [("app.css", "css"), ("app.js", "javascript"), ("app.ts", "typescript")]:
        path = settings.workspace / name
        path.write_text("/* x */", encoding="utf-8")
        assert client.get(f"/api/file?path={path}").json()["language"] == language


def test_raw_file_serves_correct_mime(client, settings):
    (settings.workspace / "style.css").write_text("body { color: red }", encoding="utf-8")
    response = client.get(f"/files/{settings.workspace}/style.css".replace("\\", "/"))

    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]
    assert "color: red" in response.text


def test_preview_rejects_files_outside_workspaces(client, tmp_path):
    outsider = tmp_path.parent / "секрет.txt"
    outsider.write_text("нельзя", encoding="utf-8")

    assert client.get(f"/api/file?path={outsider}").status_code == 404
    assert client.get(f"/files/{outsider}".replace("\\", "/")).status_code == 404


def test_preview_reports_missing_file(client, settings):
    assert client.get(f"/api/file?path={settings.workspace / 'нет.txt'}").status_code == 404


# ------------------------------------------ режимы, области и лента шагов


def test_ready_reports_modes(client):
    with client.websocket_connect("/ws") as ws:
        ready = ws.receive_json()

    assert ready["approval_mode"]
    assert {m["id"] for m in ready["modes"]} >= {"manual", "accept_edits", "plan", "bypass"}


def test_mode_can_be_switched_per_chat(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "set_mode", "mode": "plan"})
        updated = _drain_until(ws, {"mode.updated"})

    assert updated["mode"] == "plan"


def test_unknown_mode_is_rejected(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "set_mode", "mode": "чепуха"})
        message = _drain_until(ws, {"log", "mode.updated"})

    assert message["type"] == "log"
    assert "Unknown mode" in message["text"]


def test_always_allow_in_project_skips_next_question(client, settings, monkeypatch):
    """Второй такой же вызов не должен спрашивать снова."""
    import server.ws as ws_module
    from core.llm.base import AssistantTurn
    from tests.fakes import ScriptedLLM, tool_call

    settings.approval_mode = "manual"
    scripted = ScriptedLLM(
        [
            AssistantTurn(tool_calls=[tool_call("write_file", call_id="a", path="one.txt", content="1")]),
            AssistantTurn(tool_calls=[tool_call("write_file", call_id="b", path="two.txt", content="2")]),
            AssistantTurn(content="оба файла записаны"),
        ]
    )
    monkeypatch.setattr(ws_module, "build_llm_client", lambda model=None: scripted)

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "run", "task": "запиши два файла", "workspace": str(settings.workspace)})

        first = _drain_until(ws, {"approval.requested"})
        ws.send_json({"type": "approval", "request_id": first["request_id"], "scope": "project"})

        finished = _drain_until(ws, {"run.finished", "run.failed", "approval.requested"}, limit=200)

    assert finished["type"] == "run.finished", "второй раз спрашивать было не нужно"
    assert (settings.workspace / "one.txt").exists()
    assert (settings.workspace / "two.txt").exists()


def test_approvals_are_asked_one_at_a_time(client, settings, monkeypatch):
    """Между вопросом и ответом второй карточки быть не должно."""
    import server.ws as ws_module
    from core.llm.base import AssistantTurn
    from tests.fakes import ScriptedLLM, tool_call

    settings.approval_mode = "manual"
    scripted = ScriptedLLM(
        [
            AssistantTurn(
                tool_calls=[
                    tool_call("write_file", call_id="a", path="a.txt", content="a"),
                    tool_call("write_file", call_id="b", path="b.txt", content="b"),
                ]
            ),
            AssistantTurn(content="готово"),
        ]
    )
    monkeypatch.setattr(ws_module, "build_llm_client", lambda model=None: scripted)

    transcript: list[str] = []
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "run", "task": "запиши два файла", "workspace": str(settings.workspace)})

        for _ in range(200):
            message = ws.receive_json()
            transcript.append(message["type"])
            if message["type"] == "approval.requested":
                ws.send_json(
                    {"type": "approval", "request_id": message["request_id"], "scope": "once"}
                )
            if message["type"] in ("run.finished", "run.failed"):
                break

    assert transcript.count("approval.requested") == 2, transcript

    # Между первым вопросом и ответом на него второй вопрос не появлялся.
    first = transcript.index("approval.requested")
    resolved = transcript.index("approval.resolved")
    assert "approval.requested" not in transcript[first + 1 : resolved], transcript
    assert (settings.workspace / "a.txt").exists()
    assert (settings.workspace / "b.txt").exists()


def test_timeline_is_saved_and_restored(client, settings, monkeypatch):
    """Открытый заново чат обязан показывать шаги, а не только текст."""
    import server.ws as ws_module
    from core.llm.base import AssistantTurn
    from tests.fakes import ScriptedLLM, tool_call

    settings.approval_mode = "bypass"
    scripted = ScriptedLLM(
        [
            AssistantTurn(tool_calls=[tool_call("list_directory", path=".")]),
            AssistantTurn(content="В папке пусто."),
        ]
    )
    monkeypatch.setattr(ws_module, "build_llm_client", lambda model=None: scripted)

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "run", "task": "посмотри папку"})
        # Сначала конец задачи, и только потом "idle": run.finished приходит
        # ДО записи сессии на диск, а состояние "idle" — уже после неё.
        # Ждать сразу "idle" нельзя: соединение сообщает это состояние и до
        # запуска, и тогда проверка шла бы по пустому хранилищу.
        _drain_until(ws, {"run.finished"}, limit=120)
        _drain_until_state(ws, "idle", limit=120)

    stored = _wait_for_sessions(client)
    assert stored, "сессия не сохранилась"

    session = client.get(f"/api/sessions/{stored[0]['id']}").json()["session"]
    kinds = [entry["kind"] for entry in session["timeline"]]

    assert kinds == ["user", "step", "answer"]
    step = session["timeline"][1]
    assert step["name"] == "list_directory"
    assert step["ok"] is True
    assert step["duration_ms"] >= 0
    assert session["timeline"][2]["text"] == "В папке пусто."


def test_inline_media_persists_and_answer_is_not_duplicated(client, settings, monkeypatch):
    """Инлайн-виджет/медиа должны попадать в историю на своё место, а финальный
    текст ответа — не дублироваться поверх уже разложенных сегментов."""
    import server.ws as ws_module
    from core.llm.base import AssistantTurn
    from tests.fakes import ScriptedLLM, tool_call

    settings.approval_mode = "bypass"
    scripted = ScriptedLLM(
        [
            AssistantTurn(tool_calls=[tool_call("show_interactive", html="<b>виджет</b>", caption="w")]),
            AssistantTurn(content="Готово, виджет выше."),
        ]
    )
    monkeypatch.setattr(ws_module, "build_llm_client", lambda model=None: scripted)

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "run", "task": "покажи виджет"})
        _drain_until(ws, {"run.finished"}, limit=120)
        _drain_until_state(ws, "idle", limit=120)

    stored = _wait_for_sessions(client)
    session = client.get(f"/api/sessions/{stored[0]['id']}").json()["session"]
    tl = session["timeline"]
    kinds = [e["kind"] for e in tl]

    assert "widget" in kinds, kinds
    widget = next(e for e in tl if e["kind"] == "widget")
    assert "виджет" in widget["html"]

    answer = next(e for e in tl if e["kind"] == "answer")
    # Инлайн был — как «answer» пишется только хвост, а не весь текст ещё раз.
    assert answer["text"] == "Готово, виджет выше."
    assert answer.get("full") == "Готово, виджет выше."
    # Текст ответа не задублирован отдельными «text»-сегментами того же содержания.
    assert sum(1 for e in tl if e.get("text") == "Готово, виджет выше.") == 1


# ------------------------------------------------------------- повтор задачи


def test_rerun_rewinds_history_and_runs_again(client, settings, monkeypatch):
    """«Повторить» откатывает диалог к запросу и выполняет его заново."""
    import server.ws as ws_module
    from core.llm.base import AssistantTurn
    from tests.fakes import ScriptedLLM

    settings.approval_mode = "bypass"
    # Три ответа: первый прогон, второй прогон, и повтор первого.
    scripted = ScriptedLLM(
        [
            AssistantTurn(content="ответ 1"),
            AssistantTurn(content="ответ 2"),
            AssistantTurn(content="ответ 1 заново"),
        ]
    )
    monkeypatch.setattr(ws_module, "build_llm_client", lambda model=None: scripted)

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "run", "task": "первый запрос"})
        _drain_until_state(ws, "idle", limit=60)
        ws.send_json({"type": "run", "task": "второй запрос"})
        _drain_until_state(ws, "idle", limit=60)

        # Повтор ПЕРВОГО запроса (turn=0): второй должен исчезнуть из истории.
        ws.send_json({"type": "run", "task": "первый запрос", "rerun_turn": 0})
        final = _drain_until(ws, {"run.finished"}, limit=60)
        assert final["text"] == "ответ 1 заново"
        _drain_until_state(ws, "idle", limit=60)

    sessions = _wait_for_sessions(client)
    session = client.get(f"/api/sessions/{sessions[0]['id']}").json()["session"]
    # В истории остался один запрос пользователя — повторённый.
    users = [m for m in session["messages"] if m["role"] == "user"]
    assert len(users) == 1
    assert "второй запрос" not in str(session["messages"])


# ------------------------------------------------------------- стоимость


def test_cost_flows_through_events(client, settings, monkeypatch):
    """usage.updated в ходе задачи и cost_usd в финале, посчитанные по ценам."""
    import json

    import server.ws as ws_module
    from core.llm.base import AssistantTurn
    from tests.fakes import ScriptedLLM

    # Кладём кэш цен в папку данных тестовых настроек.
    cache = settings.app_dir / "storage" / "models.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps({"fetched_at": 0, "models": [
            {"id": "test/model", "prompt_price": 2.0, "completion_price": 6.0}
        ]}),
        encoding="utf-8",
    )

    scripted = ScriptedLLM(
        [AssistantTurn(
            content="Готово.",
            usage={"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000, "total_tokens": 2_000_000},
        )],
        model="test/model",
    )
    monkeypatch.setattr(ws_module, "build_llm_client", lambda model=None: scripted)

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "run", "task": "посчитай стоимость"})

        usage_event = _drain_until(ws, {"usage.updated"}, limit=40)
        assert usage_event["tokens"] == 2_000_000
        assert usage_event["priced"] is True

        finished = _drain_until(ws, {"run.finished"}, limit=40)
        # 1 млн промпта * $2 + 1 млн ответа * $6 = $8.
        assert finished["cost_usd"] == pytest.approx(8.0)


# ------------------------------------------------------------- откат правок


def test_checkpoint_flow_over_websocket(client, settings, monkeypatch):
    """Полный путь: правка файла -> событие checkpoint -> откат по команде."""
    import server.ws as ws_module
    from core.llm.base import AssistantTurn
    from tests.fakes import ScriptedLLM, tool_call

    settings.approval_mode = "bypass"
    target = settings.workspace / "заметка.txt"
    target.write_text("ОРИГИНАЛ", encoding="utf-8")

    scripted = ScriptedLLM(
        [
            AssistantTurn(tool_calls=[tool_call("write_file", path="заметка.txt", content="ЗАМЕНА")]),
            AssistantTurn(content="Готово."),
        ]
    )
    monkeypatch.setattr(ws_module, "build_llm_client", lambda model=None: scripted)

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "run", "task": "перепиши файл", "workspace": str(settings.workspace)})
        created = _drain_until(ws, {"checkpoint.created"}, limit=60)
        assert created["path"] == "заметка.txt"
        assert created["recoverable"] is True
        _drain_until_state(ws, "idle", limit=120)

        # Файл действительно переписан.
        assert target.read_text(encoding="utf-8") == "ЗАМЕНА"

        # Откатываем по команде интерфейса.
        ws.send_json({"type": "restore_checkpoint", "path": "заметка.txt"})
        restored = _drain_until(ws, {"checkpoint.restored"}, limit=60)
        assert restored["path"] == "заметка.txt"

    assert target.read_text(encoding="utf-8") == "ОРИГИНАЛ"


def test_rollback_run_reverts_whole_run(client, settings, monkeypatch):
    """«Откатить прогон»: все файлы прогона возвращаются к состоянию до него."""
    import server.ws as ws_module
    from core.llm.base import AssistantTurn
    from tests.fakes import ScriptedLLM, tool_call

    settings.approval_mode = "bypass"
    a = settings.workspace / "a.txt"
    a.write_text("БЫЛО", encoding="utf-8")

    scripted = ScriptedLLM(
        [
            AssistantTurn(tool_calls=[
                tool_call("write_file", path="a.txt", content="СТАЛО"),
                tool_call("write_file", path="new.txt", content="НОВЫЙ"),
            ]),
            AssistantTurn(content="Готово."),
        ]
    )
    monkeypatch.setattr(ws_module, "build_llm_client", lambda model=None: scripted)

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "run", "task": "правки", "workspace": str(settings.workspace)})
        finished = _drain_until(ws, {"run.finished"}, limit=120)
        run_id = finished["run_id"]
        assert run_id
        _drain_until_state(ws, "idle", limit=120)
        assert a.read_text(encoding="utf-8") == "СТАЛО"
        assert (settings.workspace / "new.txt").exists()

        ws.send_json({"type": "rollback_run", "run_id": run_id})
        rolled = _drain_until(ws, {"run_rollback"}, limit=60)
        assert set(rolled["restored"]) == {"a.txt", "new.txt"}

    assert a.read_text(encoding="utf-8") == "БЫЛО"          # вернулось
    assert not (settings.workspace / "new.txt").exists()   # созданный удалён


def test_interrupted_run_surfaced_and_dismissed(client, settings):
    """Оставшийся маркер прогона (процесс умер) → при загрузке чата предлагаем продолжить."""
    from core.agent.run_state import RunStateStore
    from core.agent.session import Session
    from core.agent.storage import SessionStore

    sess = Session(id="sess_int01", title="Прерванный", workspace=str(settings.workspace))
    SessionStore(settings=settings).save(sess)
    store = RunStateStore(settings.data_dir)
    store.begin("sess_int01", "run_abc", "почини баг", "test/model")

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # ready
        ws.send_json({"type": "load_session", "session_id": "sess_int01"})
        note = _drain_until(ws, {"run_interrupted"})
        assert note["run_id"] == "run_abc"
        assert note["task"] == "почини баг"

        # «Скрыть» снимает маркер (ответа нет — синхронизируемся через ping/pong).
        ws.send_json({"type": "dismiss_interrupted"})
        ws.send_json({"type": "ping"})
        _drain_until(ws, {"pong"})

    assert store.interrupted("sess_int01") is None


def test_resume_run_continues_and_clears_marker(client, settings):
    """«Продолжить» запускает задачу заново по сохранённой истории и снимает маркер."""
    from core.agent.run_state import RunStateStore
    from core.agent.session import Session
    from core.agent.storage import SessionStore

    sess = Session(id="sess_int02", title="Прерванный-2", workspace=str(settings.workspace))
    SessionStore(settings=settings).save(sess)
    store = RunStateStore(settings.data_dir)
    store.begin("sess_int02", "run_xyz", "доделай отчёт", "test/model")

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # ready
        ws.send_json({"type": "load_session", "session_id": "sess_int02"})
        _drain_until(ws, {"run_interrupted"})
        ws.send_json({"type": "resume_run"})
        finished = _drain_until(ws, {"run.finished", "run.failed"}, limit=120)
        assert finished["type"] == "run.finished"

    assert store.interrupted("sess_int02") is None  # маркер снят при продолжении


def test_restore_without_snapshot_warns(client, settings):
    """Откат несуществующего снимка — предупреждение, а не молчание или сбой."""
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "restore_checkpoint", "path": "нет-такого.txt"})
        message = _drain_until(ws, {"log"}, limit=20)
        assert "Нет сохранённых" in message["text"]


# ------------------------------------------------------------- остановка


def test_stop_cancels_a_running_task(client, settings, monkeypatch):
    """«Стоп» обязан прерывать задачу, а не ждать её конца."""
    import server.ws as ws_module
    from tests.fakes import HangingLLM

    monkeypatch.setattr(ws_module, "build_llm_client", lambda model=None: HangingLLM())

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "run", "task": "бесконечная задача"})
        _drain_until(ws, {"step.started"}, limit=40)

        ws.send_json({"type": "stop"})
        message = _drain_until(ws, {"run.cancelled", "run.finished", "run.failed"}, limit=60)

    assert message["type"] == "run.cancelled", "остановка не прервала задачу"


def test_stop_frees_the_connection_for_the_next_task(client, settings, monkeypatch):
    """После остановки соединение должно принимать новую задачу сразу."""
    import server.ws as ws_module
    from core.llm.base import AssistantTurn
    from tests.fakes import HangingLLM, ScriptedLLM

    clients = [HangingLLM(), ScriptedLLM([AssistantTurn(content="сделано")])]
    monkeypatch.setattr(ws_module, "build_llm_client", lambda model=None: clients.pop(0))

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "run", "task": "первая"})
        _drain_until(ws, {"step.started"}, limit=40)
        ws.send_json({"type": "stop"})
        _drain_until_state(ws, "idle", limit=60)

        ws.send_json({"type": "run", "task": "вторая"})
        finished = _drain_until(ws, {"run.finished", "run.failed"}, limit=60)

    assert finished["type"] == "run.finished"
    assert finished["text"] == "сделано"


def test_composer_form_does_not_block_the_stop_button():
    """Кнопка «Запустить» во время задачи работает как «Стоп», а поле пусто.

    С обязательным полем браузер глотал нажатие и показывал «Заполните это
    поле» — остановка не срабатывала, и это выглядело как зависший агент.
    """
    from pathlib import Path

    html = Path(__file__).parent.parent / "static" / "index.html"
    markup = html.read_text(encoding="utf-8")

    form_start = markup.index('<form class="composer"')
    form_html = markup[form_start : markup.index("</form>", form_start)]

    assert "novalidate" in form_html.split(">")[0], "форма композера обязана быть novalidate"
    assert "required" not in form_html, "обязательных полей в композере быть не должно"


# ------------------------------------------------------------- настройки в UI


def test_settings_endpoint_masks_the_key(client, settings):
    settings.llm_api_key = "sk-or-v1-очень-секретный-9876"
    data = client.get("/api/settings").json()

    assert data["api_key_set"] is True
    assert data["api_key_hint"] == "…9876"
    assert "секретный" not in str(data)


def test_settings_can_be_saved_from_ui(client, settings, monkeypatch):
    import core.config_file as config_module

    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(config_module, "reload_settings", lambda: settings)

    result = client.post("/api/settings", json={"default_model": "openai/gpt-5"}).json()

    assert result["ok"] is True
    text = (settings.app_dir / ".env").read_text(encoding="utf-8")
    assert "DEFAULT_MODEL=openai/gpt-5" in text


# ------------------------------------------------------------- загрузка файлов


def test_upload_endpoint_saves_and_returns_paths(client, settings):
    """Перетащенный файл сохраняется и его путь читается системой вложений."""
    from pathlib import Path

    files = [
        ("files", ("отчёт.txt", b"data-inside", "text/plain")),
        ("files", ("pic.png", b"\x89PNG", "image/png")),
    ]
    res = client.post("/api/attachments/upload", files=files).json()

    assert res["errors"] == []
    assert len(res["paths"]) == 2
    assert Path(res["paths"][0]).read_bytes() == b"data-inside"
    # Лежит в папке данных, а не в чатах.
    assert settings.data_dir in Path(res["paths"][0]).parents


# ------------------------------------------------------------- пресеты


def test_preset_endpoints(client, settings):
    """Список с дефолтами, сохранение своего, применение читаемо, удаление."""
    listed = client.get("/api/presets").json()["presets"]
    assert {p["name"] for p in listed} >= {"Кодинг", "Учёба", "Быт"}

    saved = client.post("/api/presets", json={
        "name": "Ревью", "approval_mode": "plan", "web_mode": "off", "skills": ["python_expert"],
    }).json()
    assert saved["ok"]
    assert saved["preset"]["approval_mode"] == "plan"

    after = {p["name"] for p in client.get("/api/presets").json()["presets"]}
    assert "Ревью" in after

    deleted = client.delete("/api/presets/Ревью").json()
    assert deleted["ok"]


def test_preset_without_name_is_rejected(client):
    res = client.post("/api/presets", json={"name": "  "})
    assert res.status_code == 400


# ------------------------------------------------------------- быстрые команды


def test_command_endpoints(client):
    listed = client.get("/api/commands").json()["commands"]
    assert {c["name"] for c in listed} >= {"тесты", "ревью", "объясни"}

    saved = client.post("/api/commands", json={
        "name": "чеклист", "template": "Составь чеклист для {{ввод}}", "description": "чеклист",
    }).json()
    assert saved["ok"]
    assert "чеклист" in {c["name"] for c in client.get("/api/commands").json()["commands"]}

    assert client.delete("/api/commands/чеклист").json()["ok"]


def test_command_bad_name_rejected(client):
    res = client.post("/api/commands", json={"name": "с пробелом", "template": "x"})
    assert res.status_code == 400


# --------------------------------------------------------------- экспорт диалога


def _seed_session(client):
    from core.agent.session import Session
    store = client.app.state.store
    s = Session(title="Экспорт-тест")
    s.append_timeline({"kind": "user", "text": "привет"})
    s.append_timeline({"kind": "answer", "text": "**ответ** тут", "steps": 1, "duration_ms": 500})
    store.save(s)
    return s.id


def test_export_markdown_endpoint(client):
    sid = _seed_session(client)
    res = client.get(f"/api/sessions/{sid}/export?format=md")
    assert res.status_code == 200
    assert "text/markdown" in res.headers["content-type"]
    assert "attachment" in res.headers["content-disposition"]
    assert "ответ" in res.text


def test_export_html_endpoint(client):
    sid = _seed_session(client)
    res = client.get(f"/api/sessions/{sid}/export?format=html")
    assert res.status_code == 200
    assert res.text.startswith("<!DOCTYPE html>")


def test_export_bad_format(client):
    sid = _seed_session(client)
    assert client.get(f"/api/sessions/{sid}/export?format=xml").status_code == 400


def test_export_missing_session(client):
    assert client.get("/api/sessions/нетакой/export").status_code == 404


# ------------------------------------------------------------- git-дифф в UI


def test_git_diff_endpoint_non_repo(client):
    # settings.workspace в тестах — не git-репозиторий.
    data = client.get("/api/git/diff").json()
    assert data["available"] is False
    assert "reason" in data
