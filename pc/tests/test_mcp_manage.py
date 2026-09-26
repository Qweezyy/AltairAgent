"""MCP servers the user adds from settings: live connect/disconnect, remote HTTP servers, API."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from core.mcp.manager import MCPManager, normalize_server, parse_pasted_config
from core.tools.base import ToolContext
from core.tools.registry import ToolRegistry
from tests.test_mcp import SERVER, _requires_subprocess


@pytest.fixture()
def fake_server(tmp_path: Path) -> list[str]:
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(SERVER, encoding="utf-8")
    return [sys.executable, str(script)]


@pytest.fixture()
def manager(settings, tmp_path) -> MCPManager:
    return MCPManager(settings, config_path=tmp_path / "mcp_servers.json")


def test_pasted_configs_in_every_common_shape():
    claude = parse_pasted_config('{"mcpServers": {"fs": {"command": "npx", "args": ["-y", "x"]}}}')
    assert claude["fs"]["command"] == "npx" and claude["fs"]["args"] == ["-y", "x"]
    vscode = parse_pasted_config('{"servers": {"gh": {"type": "http", "url": "https://api.example.com/mcp"}}}')
    assert vscode["gh"] == {"url": "https://api.example.com/mcp", "transport": "http"}
    legacy = normalize_server({"type": "sse", "url": "https://x.example/sse"})
    assert legacy["transport"] == "sse"
    windsurf = normalize_server({"serverUrl": "https://x.example/mcp"})
    assert windsurf["url"] == "https://x.example/mcp" and "serverUrl" not in windsurf
    for bad in ('{"mcpServers": {"x": {}}}', '{"mcpServers": {"x": {"url": "ftp://x"}}}', "[]"):
        with pytest.raises(ValueError):
            parse_pasted_config(bad)


async def test_add_disable_enable_remove_updates_the_agent_tools(manager, fake_server):
    _requires_subprocess()
    registry = ToolRegistry()
    manager.bind_registry(registry)
    try:
        state = await manager.upsert("fake", {"command": fake_server[0], "args": fake_server[1:]})
        assert state["state"] == "connected" and state["tools"] == ["echo", "fail"]
        assert "mcp__fake__echo" in registry.names()
        saved = json.loads(manager.config_path.read_text(encoding="utf-8"))
        assert saved["mcpServers"]["fake"]["command"] == fake_server[0]

        tool = registry.get("mcp__fake__echo")
        result = await tool.run(tool.parse_args({"text": "привет"}), ToolContext())
        assert result.ok and result.content == "echo:привет"

        off = await manager.set_disabled("fake", True)
        assert off["state"] == "disabled" and "mcp__fake__echo" not in registry.names()
        on = await manager.set_disabled("fake", False)
        assert on["state"] == "connected" and "mcp__fake__echo" in registry.names()

        await manager.remove("fake")
        assert "mcp__fake__echo" not in registry.names()
        assert manager.servers() == []
    finally:
        await manager.stop()


async def test_broken_server_is_reported_not_raised(manager):
    _requires_subprocess()
    manager.bind_registry(ToolRegistry())
    state = await manager.upsert("broken", {"command": "definitely_not_a_program_xyz"})
    assert state["state"] == "error" and state["error"]
    await manager.stop()


def test_secrets_are_masked_for_the_ui(manager):
    manager.config_path.write_text(json.dumps({"mcpServers": {"gh": {
        "command": "npx", "env": {"GITHUB_TOKEN": "ghp_1234567890abcdef", "MODE": "fast"}},
        "remote": {"url": "https://x.example/mcp", "headers": {"Authorization": "Bearer secretvalue123"}},
    }}), encoding="utf-8")
    by_name = {s["name"]: s for s in manager.servers()}
    assert "1234567890" not in json.dumps(by_name)
    assert by_name["gh"]["env"]["MODE"] == "fast"
    assert "secretvalue" not in by_name["remote"]["headers"]["Authorization"]
    assert by_name["remote"]["kind"] == "remote"


# --------------------------------------------------------------- a real remote (HTTP) server


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def http_server():
    """The official SDK's server over Streamable HTTP, on localhost — no network needed."""
    fastmcp = pytest.importorskip("mcp.server.fastmcp")
    import uvicorn

    port = _free_port()
    server = fastmcp.FastMCP("local-test", host="127.0.0.1", port=port)

    @server.tool()
    def add(a: int, b: int):  # no return annotation: postponed annotations confuse FastMCP
        """Adds two numbers."""
        return a + b

    @server.tool()
    def shout(text: str):
        """Upper-cases the text."""
        return text.upper()

    config = uvicorn.Config(server.streamable_http_app(), host="127.0.0.1", port=port, log_level="error")
    runner = uvicorn.Server(config)
    thread = threading.Thread(target=runner.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not runner.started and time.time() < deadline:
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}/mcp"
    runner.should_exit = True
    thread.join(timeout=10)


async def test_remote_server_connects_and_its_tools_work(manager, http_server):
    registry = ToolRegistry()
    manager.bind_registry(registry)
    try:
        state = await manager.upsert("remote", {"url": http_server})
        assert state["state"] == "connected", state
        assert sorted(state["tools"]) == ["add", "shout"]
        tool = registry.get("mcp__remote__shout")
        result = await tool.run(tool.parse_args({"text": "привет"}), ToolContext())
        assert result.ok and result.content == "ПРИВЕТ"
        add = registry.get("mcp__remote__add")
        assert (await add.run(add.parse_args({"a": 2, "b": 40}), ToolContext())).content == "42"
    finally:
        await manager.stop()


async def test_unreachable_remote_server_gives_a_clear_error(manager):
    state = await manager.upsert("gone", {"url": f"http://127.0.0.1:{_free_port()}/mcp", "startupTimeout": 5})
    assert state["state"] == "error"
    assert "gone" in state["error"] and "127.0.0.1" in state["error"]
    await manager.stop()


# --------------------------------------------------------------- HTTP API


def test_api_add_list_remove_and_local_only(settings, tmp_path, monkeypatch, fake_server):
    from fastapi.testclient import TestClient

    import server.app as app_module

    monkeypatch.setattr(app_module, "get_settings", lambda: settings)
    app = app_module.create_app()
    with TestClient(app) as client:
        body = {"json": json.dumps({"mcpServers": {"fake": {"command": fake_server[0], "args": fake_server[1:]}}})}
        added = client.post("/api/mcp/servers", json=body).json()
        assert added["ok"], added
        assert added["servers"][0]["state"] == "connected"
        listed = client.get("/api/mcp").json()["servers"]
        assert [s["name"] for s in listed] == ["fake"]
        assert "mcp__fake__echo" in app.state.registry.names()

        bad = client.post("/api/mcp/servers", json={"name": "x", "config": {}}).json()
        assert not bad["ok"] and bad["error"]
        assert client.delete("/api/mcp/servers/fake").json()["ok"]
        assert "mcp__fake__echo" not in app.state.registry.names()


async def test_phone_with_token_cannot_install_code(settings, monkeypatch):
    """The bridge token lets the phone read, but installing skills or MCP servers runs code
    on this PC — only the local app window may do that."""
    import httpx

    import server.app as app_module
    import server.remote_auth as remote_auth

    with_token = settings.model_copy(update={"bridge_token": "t0ken"})
    monkeypatch.setattr(app_module, "get_settings", lambda: with_token)
    monkeypatch.setattr(remote_auth, "get_settings", lambda: with_token)
    app = app_module.create_app()
    transport = httpx.ASGITransport(app=app, client=("192.168.1.50", 5000))
    async with httpx.AsyncClient(transport=transport, base_url="http://pc") as phone:
        auth = {"Authorization": "Bearer t0ken"}
        for method, url, body in (
            ("POST", "/api/skills/import", {"paths": []}),
            ("POST", "/api/mcp/servers", {"name": "x", "config": {"command": "calc"}}),
            ("DELETE", "/api/mcp/servers/x", None),
        ):
            response = await phone.request(method, url, json=body, headers=auth)
            assert response.status_code == 403, (url, response.status_code)
        assert (await phone.post("/api/mcp/servers", json={})).status_code == 401  # no token at all


def test_api_skill_upload_and_delete(settings, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import server.app as app_module

    monkeypatch.setattr(app_module, "get_settings", lambda: settings)
    app = app_module.create_app()
    with TestClient(app) as client:
        files = {"files": ("notes.md", b"---\nname: notes\ndescription: how I take notes\n---\nBullets.\n")}
        up = client.post("/api/skills/upload", files=files).json()
        assert up == {"ok": True, "installed": ["notes"]}
        again = client.post("/api/skills/upload", files=files).json()
        assert not again["ok"] and again["exists"] == "notes"
        skills = client.get("/api/skills").json()["skills"]
        assert any(s["name"] == "notes" and s["scope"] == "global" for s in skills)
        assert client.delete("/api/skills/notes").json()["ok"]
        assert not any(s["name"] == "notes" for s in client.get("/api/skills").json()["skills"])


def test_asyncio_marker_is_active():
    # the module relies on auto async mode like the rest of the suite
    assert asyncio.iscoroutinefunction(test_remote_server_connects_and_its_tools_work)


async def test_tool_search_shows_the_parameters_of_mcp_tools():
    """The model loads MCP tools through tool_search; the summary must list their real
    parameters (from the server's schema), not "no parameters"."""
    from core.mcp.tool_wrapper import MCPTool
    from core.tools.builtin.tool_search import ToolSearchTool

    class Client:
        name = "memory"

    tool = MCPTool(Client(), {"name": "create_entities", "description": "Create entities",
                              "inputSchema": {"type": "object", "properties": {"entities": {"type": "array"}}}})
    registry = ToolRegistry([tool])
    result = await ToolSearchTool().invoke({"query": "select:mcp__memory__create_entities"},
                                           ToolContext(registry=registry))
    assert result.ok and "mcp__memory__create_entities(entities)" in result.content
