"""Обёртка MCP-инструмента в обычный Tool агента.

Схему аргументов MCP-сервер присылает сам, поэтому вместо pydantic-модели
используется «сырая» JSON-схема: валидацию делает сервер.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from core.i18n import tr
from core.mcp.client import MCPClient
from core.tools.base import Tool, ToolContext, ToolResult

_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def mcp_tool_name(server: str, tool: str) -> str:
    """Уникальное и допустимое для API имя инструмента."""
    return f"mcp__{_SAFE.sub('_', server)}__{_SAFE.sub('_', tool)}"[:64]


class RawArgs(BaseModel):
    """Позволяет любые поля: схему контролирует MCP-сервер."""

    model_config = {"extra": "allow"}


class MCPTool(Tool):
    name = "mcp__placeholder"
    description = "MCP tool"
    Args = RawArgs
    dangerous = True  # внешний сервер может делать что угодно
    timeout = None

    def __init__(self, client: MCPClient, info: dict[str, Any]) -> None:
        self._client = client
        self._remote_name = info.get("name", "")
        self.name = mcp_tool_name(client.name, self._remote_name)
        description = info.get("description") or "A tool of an MCP server."
        self.description = f"[MCP:{client.name}] {description}"
        self._schema: dict[str, Any] = info.get("inputSchema") or {
            "type": "object",
            "properties": {},
        }

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description[:1024],
                "parameters": self._schema,
            },
        }

    def approval_reason(self, args: BaseModel) -> str:
        return tr("appr.mcp", tool=self._remote_name, server=self._client.name)

    async def run(self, args: RawArgs, ctx: ToolContext) -> ToolResult:
        payload = args.model_dump(exclude_none=False)
        text, ok = await self._client.call_tool(self._remote_name, payload)
        return ToolResult(content=text, ok=ok)
