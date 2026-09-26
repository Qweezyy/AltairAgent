"""Тесты MCP-клиента на настоящем stdio-сервере (без сети и без npm).

Сервер — крошечный python-скрипт, который говорит по JSON-RPC. Это ловит
реальные поломки протокола: handshake, пагинацию, ошибки, зависшие ответы.
"""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from pathlib import Path

import pytest

from core.errors import MCPError
from core.mcp.client import MCPClient
from core.mcp.manager import MCPManager
from core.mcp.tool_wrapper import mcp_tool_name
from core.tools.base import ToolContext

SERVER = textwrap.dedent(
    """
    import json, sys

    def send(payload):
        sys.stdout.write(json.dumps(payload) + "\\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method, mid = msg.get("method"), msg.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake", "version": "1.0"},
            }})
        elif method == "tools/list":
            cursor = (msg.get("params") or {}).get("cursor")
            if not cursor:
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "tools": [{
                        "name": "echo",
                        "description": "Повторяет текст",
                        "inputSchema": {"type": "object",
                                        "properties": {"text": {"type": "string"}},
                                        "required": ["text"]},
                    }],
                    "nextCursor": "page2",
                }})
            else:
                send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [{
                    "name": "fail",
                    "description": "Всегда ошибка",
                    "inputSchema": {"type": "object", "properties": {}},
                }]}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            if params.get("name") == "fail":
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": "так и задумано"}],
                    "isError": True,
                }})
            else:
                text = (params.get("arguments") or {}).get("text", "")
                send({"jsonrpc": "2.0", "id": mid,
                      "result": {"content": [{"type": "text", "text": "echo:" + text}]}})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": -32601, "message": "method not found"}})
    """
)


def _requires_subprocess() -> None:
    if sys.platform == "win32" and not type(asyncio.get_event_loop()).__name__.startswith("Proactor"):
        pytest.skip("event loop без поддержки подпроцессов")


@pytest.fixture()
def server_script(tmp_path: Path) -> Path:
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(SERVER, encoding="utf-8")
    return script


async def test_handshake_and_pagination(server_script):
    _requires_subprocess()
    client = MCPClient("fake", sys.executable, [str(server_script)], startup_timeout=20.0)
    await client.start()
    try:
        assert client.is_running
        assert client.server_info["name"] == "fake"
        # инструменты собраны с обеих страниц
        assert [tool["name"] for tool in client.tools] == ["echo", "fail"]
    finally:
        await client.stop()
    assert not client.is_running


async def test_call_tool_success_and_error(server_script, settings):
    _requires_subprocess()
    client = MCPClient("fake", sys.executable, [str(server_script)])
    await client.start()
    try:
        text, ok = await client.call_tool("echo", {"text": "привет"})
        assert ok and text == "echo:привет"

        text, ok = await client.call_tool("fail", {})
        assert not ok and "задумано" in text

        # обёртка ведёт себя как обычный инструмент агента
        from core.mcp.tool_wrapper import MCPTool

        tool = MCPTool(client, client.tools[0])
        settings.approval_mode = "auto"
        result = await tool.invoke({"text": "x"}, ToolContext(settings=settings))
        assert result.ok and result.content == "echo:x"
        assert tool.schema()["function"]["parameters"]["required"] == ["text"]
    finally:
        await client.stop()


async def test_start_failure_is_readable():
    _requires_subprocess()
    client = MCPClient("broken", "definitely_not_a_real_command_xyz", [])
    with pytest.raises(MCPError) as exc:
        await client.start()
    assert "broken" in str(exc.value)


async def test_dead_server_does_not_hang(tmp_path):
    _requires_subprocess()
    script = tmp_path / "dies.py"
    script.write_text("import sys; sys.exit(1)", encoding="utf-8")
    client = MCPClient("dead", sys.executable, [str(script)], startup_timeout=5.0)
    with pytest.raises(MCPError):
        await client.start()


def test_tool_name_is_registry_safe():
    name = mcp_tool_name("my server!", "do/thing")
    assert name == "mcp__my_server___do_thing"
    assert len(name) <= 64


async def test_manager_reports_broken_server(settings, tmp_path):
    _requires_subprocess()
    config = settings.workspace / "mcp_servers.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "broken": {"command": "nope_xyz", "args": []},
                    "skipped": {"command": "nope_xyz", "disabled": True},
                }
            }
        ),
        encoding="utf-8",
    )
    manager = MCPManager(settings, config_path=config)
    tools = await manager.start()

    assert tools == []
    assert "broken" in manager.errors
    assert "skipped" not in manager.errors  # отключённые не считаются ошибкой
    await manager.stop()


async def test_manager_without_config_is_quiet(settings, tmp_path):
    manager = MCPManager(settings, config_path=tmp_path / "нет-такого.json")
    assert await manager.start() == []
    assert manager.status()["servers"] == {}
