"""Мост к телефону: файловые команды (слой 2) и обратные запросы (слой 3).

Тестируем логику `Connection` напрямую, с фейковым сокетом — без HTTP-стека:
так проверяется именно контракт сообщений из server/BRIDGE_PC_SPEC.md.
"""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace

import pytest

import server.ws as ws_module
from core.tools.base import ToolContext
from core.tools.builtin.bridge_tools import (
    PhoneAskUserTool,
    PhoneCapabilityTool,
    PhoneRequestFileTool,
)
from server.ws import Connection, _resolve_in_workspace


class FakeWebSocket:
    """Минимальный двойник WebSocket: даёт host и глотает accept/send."""

    def __init__(self, host: str = "127.0.0.1") -> None:
        self.client = SimpleNamespace(host=host)
        self.app = SimpleNamespace(state=SimpleNamespace(active_workspaces=set()))

    async def accept(self) -> None:  # pragma: no cover - не используется в юнитах
        pass

    async def send_json(self, payload) -> None:  # pragma: no cover
        pass

    async def receive_json(self):  # pragma: no cover
        raise AssertionError("не должно вызываться в этих тестах")


@pytest.fixture()
def conn(settings, monkeypatch):
    monkeypatch.setattr(ws_module, "get_settings", lambda: settings)
    connection = Connection(FakeWebSocket(host="127.0.0.1"), registry=SimpleNamespace(all=lambda: []))
    return connection


def _drain(connection) -> list[dict]:
    """Забирает всё, что соединение положило в исходящую очередь."""
    out = []
    while not connection.outbox.empty():
        out.append(connection.outbox.get_nowait())
    return out


def _last(connection) -> dict:
    messages = _drain(connection)
    assert messages, "соединение ничего не отправило"
    return messages[-1]


# --------------------------------------------------------------- песочница


def test_resolve_in_workspace_blocks_escape(tmp_path):
    base = tmp_path / "ws"
    base.mkdir()
    assert _resolve_in_workspace(base, "sub/file.txt") == (base / "sub/file.txt").resolve()
    with pytest.raises(ValueError):
        _resolve_in_workspace(base, "../secret.txt")
    with pytest.raises(ValueError):
        _resolve_in_workspace(base, "../../etc/passwd")


# --------------------------------------------------------------- слой 1: hello


@pytest.mark.asyncio
async def test_hello_records_peer_and_answers(conn):
    await conn._bridge_hello(
        {"type": "hello", "platform": "android", "capabilities": ["camera", "files"]}
    )
    assert conn.peer["platform"] == "android"
    assert conn.phone_connected is True

    reply = _last(conn)
    assert reply["type"] == "hello"
    assert reply["platform"] == "pc"
    assert "shell" in reply["capabilities"]
    assert reply["workspace"]


def test_remote_socket_counts_as_phone(settings, monkeypatch):
    monkeypatch.setattr(ws_module, "get_settings", lambda: settings)
    remote = Connection(FakeWebSocket(host="100.64.0.7"), registry=SimpleNamespace(all=lambda: []))
    assert remote.phone_connected is True  # даже без hello


# --------------------------------------------------------------- слой 2: файлы


@pytest.mark.asyncio
async def test_list_files_lists_workspace(conn, tmp_path):
    ws = tmp_path / "shared"
    (ws / "out").mkdir(parents=True)
    (ws / "out" / "report.pdf").write_bytes(b"%PDF-1.4 data")
    (ws / "note.txt").write_text("привет", encoding="utf-8")

    await conn._bridge_list_files({"type": "list_files", "workspace": str(ws)})
    msg = _last(conn)

    assert msg["type"] == "files"
    by_path = {item["path"]: item for item in msg["items"]}
    assert set(by_path) == {"out/report.pdf", "note.txt"}
    assert by_path["out/report.pdf"]["kind"] == "file"
    assert by_path["note.txt"]["kind"] == "text"
    assert by_path["note.txt"]["bytes"] > 0
    assert by_path["note.txt"]["mtime"]


@pytest.mark.asyncio
async def test_list_files_honours_glob(conn, tmp_path):
    ws = tmp_path / "shared"
    (ws / "out").mkdir(parents=True)
    (ws / "out" / "a.txt").write_text("a", encoding="utf-8")
    (ws / "skip.txt").write_text("b", encoding="utf-8")

    await conn._bridge_list_files({"workspace": str(ws), "glob": "out/**"})
    paths = {item["path"] for item in _last(conn)["items"]}
    assert paths == {"out/a.txt"}


@pytest.mark.asyncio
async def test_stat_file_reports_existence(conn, tmp_path):
    ws = tmp_path / "shared"
    ws.mkdir()
    (ws / "pic.png").write_bytes(b"\x89PNG rest")

    await conn._bridge_stat_file({"workspace": str(ws), "path": "pic.png"})
    ok = _last(conn)
    assert ok == {
        "type": "file.stat",
        "path": "pic.png",
        "exists": True,
        "bytes": ok["bytes"],
        "mtime": ok["mtime"],
        "kind": "image",
    }

    await conn._bridge_stat_file({"workspace": str(ws), "path": "нет.txt"})
    missing = _last(conn)
    assert missing == {"type": "file.stat", "path": "нет.txt", "exists": False}


@pytest.mark.asyncio
async def test_get_file_returns_base64(conn, tmp_path):
    ws = tmp_path / "shared"
    ws.mkdir()
    payload = b"binary\x00\x01content"
    (ws / "data.bin").write_bytes(payload)

    await conn._bridge_get_file({"workspace": str(ws), "path": "data.bin"})
    msg = _last(conn)
    assert msg["type"] == "file"
    assert base64.b64decode(msg["b64"]) == payload
    assert msg["bytes"] == len(payload)


@pytest.mark.asyncio
async def test_get_file_missing_and_escape(conn, tmp_path):
    ws = tmp_path / "shared"
    ws.mkdir()

    await conn._bridge_get_file({"workspace": str(ws), "path": "нет.txt"})
    assert _last(conn) == {"type": "file.missing", "path": "нет.txt"}

    await conn._bridge_get_file({"workspace": str(ws), "path": "../secret"})
    assert _last(conn) == {"type": "file.missing", "path": "../secret"}


@pytest.mark.asyncio
async def test_get_file_rejects_oversize(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(ws_module, "MAX_FILE_BYTES", 8)
    ws = tmp_path / "shared"
    ws.mkdir()
    (ws / "big.bin").write_bytes(b"0123456789")  # 10 > 8

    await conn._bridge_get_file({"workspace": str(ws), "path": "big.bin"})
    assert _last(conn) == {"type": "file.missing", "path": "big.bin"}


@pytest.mark.asyncio
async def test_put_file_writes_and_creates_dirs(conn, tmp_path):
    ws = tmp_path / "shared"
    ws.mkdir()
    payload = b"uploaded bytes"
    b64 = base64.b64encode(payload).decode("ascii")

    await conn._bridge_put_file(
        {"workspace": str(ws), "path": "inbox/photo.jpg", "b64": b64}
    )
    msg = _last(conn)
    assert msg == {"type": "put_file.ok", "path": "inbox/photo.jpg", "bytes": len(payload)}
    assert (ws / "inbox" / "photo.jpg").read_bytes() == payload


@pytest.mark.asyncio
async def test_put_file_without_workspace_uses_run_folder(conn):
    """Ответ на need_file приходит без workspace — пишем в папку прогона."""
    payload = b"from phone"
    b64 = base64.b64encode(payload).decode("ascii")

    await conn._bridge_put_file({"path": "inbox/x.dat", "b64": b64})
    msg = _last(conn)
    assert msg["type"] == "put_file.ok"

    run_ws = conn.session_settings.workspace
    assert (run_ws / "inbox" / "x.dat").read_bytes() == payload


@pytest.mark.asyncio
async def test_put_file_rejects_escape(conn, tmp_path):
    ws = tmp_path / "shared"
    ws.mkdir()
    await conn._bridge_put_file(
        {"workspace": str(ws), "path": "../evil.txt", "b64": base64.b64encode(b"x").decode()}
    )
    msg = _last(conn)
    assert msg["type"] == "put_file.error"
    assert not (tmp_path / "evil.txt").exists()


@pytest.mark.asyncio
async def test_put_file_rejects_oversize(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(ws_module, "MAX_FILE_BYTES", 4)
    ws = tmp_path / "shared"
    ws.mkdir()
    await conn._bridge_put_file(
        {"workspace": str(ws), "path": "big.bin", "b64": base64.b64encode(b"0123456789").decode()}
    )
    msg = _last(conn)
    assert msg["type"] == "put_file.error"
    assert "25" in msg["message"] or "больше" in msg["message"]
    assert not (ws / "big.bin").exists()


# ----------------------------------------------------- слой 3-bis: память


@pytest.mark.asyncio
async def test_sync_memory_merges_and_returns_all(conn, settings):
    # У ПК уже есть свой факт в общей памяти.
    from core.memory import MemoryStore

    MemoryStore(settings.data_dir).remember("Проект — Android-агент", category="fact")

    await conn._bridge_sync_memory(
        {
            "type": "sync_memory",
            "scope": "global",
            "facts": ["Пользователь любит Kotlin", "Проект — Android-агент", "  "],
        }
    )
    msg = _last(conn)
    assert msg["type"] == "memory_sync"
    # Пустые отсеяны, дубликат «Проект…» не задвоился, факт телефона долит.
    assert set(msg["facts"]) == {"Пользователь любит Kotlin", "Проект — Android-агент"}

    # Присланный факт реально сохранён на диске ПК (переживёт перезапуск).
    reopened = {f.text for f in MemoryStore(settings.data_dir).all()}
    assert "Пользователь любит Kotlin" in reopened


@pytest.mark.asyncio
async def test_sync_memory_empty_is_safe(conn):
    await conn._bridge_sync_memory({"type": "sync_memory"})
    msg = _last(conn)
    assert msg["type"] == "memory_sync"
    assert msg["facts"] == []


# --------------------------------------------------------- слой 3: обратные запросы


@pytest.mark.asyncio
async def test_request_from_phone_resolves_by_req_id(conn):
    async def responder():
        # ждём, пока запрос уйдёт в очередь, читаем req_id и отвечаем
        for _ in range(50):
            msgs = _drain(conn)
            sent = next((m for m in msgs if m.get("type") == "need_file"), None)
            if sent:
                conn._bridge_resolve(
                    {"type": "need_file.done", "req_id": sent["req_id"], "path": "inbox/logo.png"}
                )
                return
            await asyncio.sleep(0.01)
        raise AssertionError("запрос need_file не был отправлен")

    task = asyncio.create_task(responder())
    reply = await conn.request_from_phone({"type": "need_file", "hint": "логотип"}, timeout=5)
    await task
    assert reply["type"] == "need_file.done"
    assert reply["path"] == "inbox/logo.png"


@pytest.mark.asyncio
async def test_request_from_phone_times_out(conn):
    reply = await conn.request_from_phone({"type": "need_file", "hint": "x"}, timeout=0.05)
    assert reply == {"cancelled": True}
    assert not conn._bridge_waiters  # ожидание снято


@pytest.mark.asyncio
async def test_cancel_bridge_waiters_unblocks(conn):
    async def canceller():
        await asyncio.sleep(0.02)
        conn._cancel_bridge_waiters()

    asyncio.create_task(canceller())
    reply = await conn.request_from_phone({"type": "need_capability"}, timeout=5)
    assert reply == {"cancelled": True}


@pytest.mark.asyncio
async def test_ask_phone_uses_answer_channel(conn):
    async def responder():
        for _ in range(50):
            msgs = _drain(conn)
            sent = next((m for m in msgs if m.get("type") == "ask_user"), None)
            if sent:
                # телефон отвечает в формате ask: request_id + answers
                conn._resolve_question(
                    {
                        "type": "answer",
                        "request_id": sent["req_id"],
                        "answers": {"0": {"selected": ["Да"]}},
                    }
                )
                return
            await asyncio.sleep(0.01)
        raise AssertionError("ask_user не отправлен")

    task = asyncio.create_task(responder())
    answers = await conn.ask_phone("Точно удалить?", options=["Да", "Нет"], timeout=5)
    await task
    assert answers == {"0": {"selected": ["Да"]}}


# ----------------------------------------------------- инструменты ПК-агента


def _ctx(conn) -> ToolContext:
    return ToolContext(scratch=conn._scratch)


@pytest.mark.asyncio
async def test_phone_tool_refuses_without_phone(conn):
    # Локальный сокет без hello — телефона нет.
    result = await PhoneRequestFileTool().run(
        PhoneRequestFileTool.Args(hint="x"), _ctx(conn)
    )
    assert result.ok is False
    assert "не подключён" in result.content


@pytest.mark.asyncio
async def test_phone_request_file_tool_happy_path(conn):
    conn.peer = {"platform": "android"}  # телефон представился

    async def responder():
        for _ in range(50):
            msgs = _drain(conn)
            sent = next((m for m in msgs if m.get("type") == "need_file"), None)
            if sent:
                conn._bridge_resolve(
                    {"type": "need_file.done", "req_id": sent["req_id"], "path": "inbox/a.pdf"}
                )
                return
            await asyncio.sleep(0.01)

    asyncio.create_task(responder())
    out = await PhoneRequestFileTool().run(PhoneRequestFileTool.Args(hint="дай pdf"), _ctx(conn))
    assert "inbox/a.pdf" in out


@pytest.mark.asyncio
async def test_phone_ask_user_tool_formats_answer(conn):
    conn.peer = {"platform": "android"}

    async def responder():
        for _ in range(50):
            msgs = _drain(conn)
            sent = next((m for m in msgs if m.get("type") == "ask_user"), None)
            if sent:
                conn._resolve_question(
                    {"request_id": sent["req_id"], "answers": {"0": {"selected": ["Оставить"]}}}
                )
                return
            await asyncio.sleep(0.01)

    asyncio.create_task(responder())
    out = await PhoneAskUserTool().run(
        PhoneAskUserTool.Args(question="Что делать?", options=["Оставить", "Удалить"]), _ctx(conn)
    )
    assert "Оставить" in out


@pytest.mark.asyncio
async def test_phone_capability_tool_returns_output(conn):
    conn.peer = {"platform": "android"}

    async def responder():
        for _ in range(50):
            msgs = _drain(conn)
            sent = next((m for m in msgs if m.get("type") == "need_capability"), None)
            if sent:
                conn._bridge_resolve(
                    {"req_id": sent["req_id"], "ok": True, "output": "55.75, 37.61"}
                )
                return
            await asyncio.sleep(0.01)

    asyncio.create_task(responder())
    out = await PhoneCapabilityTool().run(
        PhoneCapabilityTool.Args(capability="location", task="координаты"), _ctx(conn)
    )
    assert "55.75, 37.61" in out


# ------------------------------------------------ синк плагинов (MCP) и навыков


@pytest.mark.asyncio
async def test_bridge_mcp_list_returns_only_http_servers(conn, settings):
    import json

    settings.mcp_config_path.write_text(
        json.dumps({"mcpServers": {
            "local": {"command": "npx", "args": ["x"]},                    # stdio → пропускается
            "remote": {"url": "https://mcp.example/x", "token": "T"},       # http + token→Bearer
            "streamsse": {"url": "https://s/y", "transport": "sse", "headers": {"X-A": "1"}},
            "off": {"url": "https://z", "disabled": True},                  # выключен → пропускается
        }}),
        encoding="utf-8",
    )
    await conn._bridge_mcp_list({"type": "mcp_list"})
    reply = _last(conn)
    assert reply["type"] == "mcp"
    by = {s["name"]: s for s in reply["servers"]}
    assert set(by) == {"remote", "streamsse"}
    assert by["remote"]["transport"] == "http"
    assert by["remote"]["headers"]["Authorization"] == "Bearer T"
    assert by["streamsse"]["transport"] == "sse"
    assert by["streamsse"]["headers"]["X-A"] == "1"


@pytest.mark.asyncio
async def test_bridge_skills_list_and_get(conn, settings):
    sk = settings.skills_dir / "demo"
    sk.mkdir(parents=True, exist_ok=True)
    (sk / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Демо-навык\nversion: 1.2\n---\nтело навыка\n", encoding="utf-8"
    )
    (sk / "ref.txt").write_text("данные", encoding="utf-8")

    await conn._bridge_skills_list({"type": "skills_list"})
    reply = _last(conn)
    assert reply["type"] == "skills"
    item = next(i for i in reply["items"] if i["name"] == "demo")
    assert item["description"] == "Демо-навык"
    assert item["version"] == "1.2"

    await conn._bridge_skill_get({"type": "skill_get", "name": "demo"})
    reply = _last(conn)
    assert reply["type"] == "skill" and reply["name"] == "demo"
    got = {f["path"]: base64.b64decode(f["b64"]).decode("utf-8") for f in reply["files"]}
    assert "SKILL.md" in got and got["ref.txt"] == "данные"


@pytest.mark.asyncio
async def test_bridge_skill_get_unknown(conn):
    await conn._bridge_skill_get({"type": "skill_get", "name": "нет-такого"})
    reply = _last(conn)
    assert reply["type"] == "skill" and reply["files"] == [] and reply.get("error")
