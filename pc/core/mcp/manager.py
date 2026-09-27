"""Жизненный цикл MCP-серверов.

Серверы поднимаются при старте приложения (server/app.py, lifespan), а дальше пользователь
добавляет, выключает и переподключает их из настроек — без перезапуска: менеджер держит реестр
инструментов агента в согласии с тем, что реально подключено. Падение одного сервера не мешает
остальным и не мешает агенту работать со встроенными инструментами.

Формат mcp_servers.json — как у Claude Desktop (его можно вставить как есть):

    {
      "mcpServers": {
        "filesystem": {
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/AI_Agent"],
          "env": {},
          "disabled": false,
          "startupTimeout": 30
        },
        "deepwiki": {"url": "https://mcp.deepwiki.com/mcp", "headers": {"Authorization": "Bearer …"}}
      }
    }

Удалённые серверы: `url` + необязательные `transport` ("http" — Streamable HTTP, по умолчанию;
"sse" — старый транспорт), `headers` и `token`.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from core.fs_atomic import atomic_write_text
from core.logging_setup import get_logger
from core.mcp.client import MCPClient
from core.mcp.http_client import HttpMCPClient
from core.mcp.tool_wrapper import MCPTool
from core.settings import Settings, get_settings
from core.tools.base import Tool

logger = get_logger("mcp.manager")

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,48}$")
#: Keys whose values are secrets — the settings UI shows them masked.
_SECRET_HINT = re.compile(r"(key|token|secret|pass|auth|cookie|bearer)", re.IGNORECASE)


def normalize_server(cfg: dict[str, Any]) -> dict[str, Any]:
    """One server entry in our format; accepts Claude Desktop, Cursor and VS Code shapes."""
    if not isinstance(cfg, dict):
        raise ValueError("a server entry must be a JSON object")
    out = {k: v for k, v in cfg.items() if k not in ("type",)}
    kind = str(cfg.get("type") or "").lower()
    url = str(cfg.get("url") or cfg.get("serverUrl") or "").strip()
    if url:
        out.pop("serverUrl", None)
        out["url"] = url
        transport = str(cfg.get("transport") or "").lower() or ("sse" if kind == "sse" else "http")
        out["transport"] = "sse" if transport == "sse" else "http"
        if not url.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
    elif cfg.get("command"):
        out["command"] = str(cfg["command"]).strip()
        out["args"] = [str(a) for a in cfg.get("args") or []]
        out["env"] = {str(k): str(v) for k, v in dict(cfg.get("env") or {}).items()}
    else:
        raise ValueError("a server needs either 'command' (local) or 'url' (remote)")
    return out


def parse_pasted_config(text: str) -> dict[str, dict[str, Any]]:
    """Servers from JSON pasted by the user: {"mcpServers": {...}}, {"servers": {...}} or {name: {...}}."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    servers = data.get("mcpServers") or data.get("servers") or data
    if not isinstance(servers, dict) or not servers:
        raise ValueError("no servers found in the JSON")
    return {str(name): normalize_server(cfg) for name, cfg in servers.items()}


def _mask(value: str) -> str:
    return value[:3] + "…" + value[-2:] if len(value) > 8 else "••••"


class MCPManager:
    """Пул подключённых MCP-серверов."""

    def __init__(self, settings: Settings | None = None, config_path: Path | None = None) -> None:
        self.settings = settings or get_settings()
        self.config_path = config_path or self.settings.mcp_config_path
        self.clients: dict[str, MCPClient | HttpMCPClient] = {}
        self.errors: dict[str, str] = {}
        self._tools_by_server: dict[str, list[Tool]] = {}
        self._registry: Any = None
        self._lock = asyncio.Lock()
        self._started = False

    # ------------------------------------------------------------------ config

    def read_config(self) -> dict[str, dict]:
        if not self.config_path.exists():
            return {}
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Не удалось прочитать %s: %s", self.config_path, exc)
            self.errors["__config__"] = f"{self.config_path.name}: {exc}"
            return {}
        self.errors.pop("__config__", None)
        servers = data.get("mcpServers", data)
        return servers if isinstance(servers, dict) else {}

    def _write_config(self, servers: dict[str, dict]) -> None:
        data: dict[str, Any] = {}
        if self.config_path.exists():
            try:
                loaded = json.loads(self.config_path.read_text(encoding="utf-8"))
                data = loaded if isinstance(loaded, dict) and "mcpServers" in loaded else {}
            except (OSError, json.JSONDecodeError):
                data = {}
        data["mcpServers"] = servers
        atomic_write_text(self.config_path, json.dumps(data, ensure_ascii=False, indent=2))

    def shareable_servers(self) -> list[dict[str, Any]]:
        """HTTP/SSE MCP-серверы из конфига — для синка с телефоном.

        Телефон подключается к серверам НАПРЯМУЮ по HTTP, поэтому синкать можно
        только записи с `url` (транспорт http/sse). stdio-серверы (command) телефон
        запустить не может — их пропускаем. Возвращает `[{name, url, transport, headers}]`
        в формате контракта Android-моста.
        """
        out: list[dict[str, Any]] = []
        for name, cfg in self.read_config().items():
            if not isinstance(cfg, dict) or cfg.get("disabled"):
                continue
            url = str(cfg.get("url") or "").strip()
            if not url:
                continue
            transport = str(cfg.get("transport") or "http").strip().lower()
            if transport not in ("http", "sse"):
                transport = "http"
            out.append({"name": name, "url": url, "transport": transport, "headers": self._headers(cfg)})
        return out

    @staticmethod
    def _headers(cfg: dict[str, Any]) -> dict[str, str]:
        headers = {str(k): str(v) for k, v in dict(cfg.get("headers") or {}).items()}
        token = cfg.get("token") or cfg.get("authToken") or cfg.get("bearer")
        if token and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    # ------------------------------------------------------------------ lifecycle

    def bind_registry(self, registry: Any) -> None:
        """The agent's tool registry: connected servers' tools are kept in it."""
        self._registry = registry
        for tools in self._tools_by_server.values():
            for tool in tools:
                registry.add(tool, override=True)

    async def start(self) -> list[Tool]:
        """Поднимает все серверы параллельно. Возвращает список доступных инструментов."""
        if self._started:
            return self.tools()
        self._started = True
        config = self.read_config()
        if not config:
            logger.info("MCP-серверы не настроены (%s).", self.config_path.name)
            return []
        await asyncio.gather(*(self._connect(name, cfg) for name, cfg in config.items()))
        logger.info(
            "MCP: серверов подключено %d, инструментов %d, ошибок %d",
            len(self.clients), len(self.tools()), len(self.errors),
        )
        return self.tools()

    async def _connect(self, name: str, cfg: dict) -> None:
        self.errors.pop(name, None)
        if not isinstance(cfg, dict):
            self.errors[name] = "invalid entry"
            return
        if cfg.get("disabled"):
            return
        startup = float(cfg.get("startupTimeout", 30.0))
        calls = float(cfg.get("callTimeout", 120.0))
        client: MCPClient | HttpMCPClient
        if cfg.get("command"):
            client = MCPClient(
                name=name, command=cfg["command"], args=list(cfg.get("args", [])),
                env=dict(cfg.get("env", {})), startup_timeout=startup, call_timeout=calls,
            )
        elif cfg.get("url"):
            client = HttpMCPClient(
                name, str(cfg["url"]), transport=str(cfg.get("transport") or "http"),
                headers=self._headers(cfg), startup_timeout=startup, call_timeout=calls,
            )
        else:
            self.errors[name] = "no 'command' or 'url'"
            return
        try:
            await client.start()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - один сервер не должен ломать остальные
            self.errors[name] = str(exc)
            logger.warning("MCP-сервер '%s' не подключён: %s", name, exc)
            return
        self.clients[name] = client
        tools = [MCPTool(client, info) for info in client.tools if info.get("name")]
        self._tools_by_server[name] = tools
        if self._registry is not None:
            for tool in tools:
                self._registry.add(tool, override=True)

    async def _disconnect(self, name: str) -> None:
        client = self.clients.pop(name, None)
        for tool in self._tools_by_server.pop(name, []):
            if self._registry is not None and self._registry.get(tool.name) is tool:
                self._registry.remove(tool.name)
        self.errors.pop(name, None)
        if client is not None:
            await client.stop()

    async def restart(self, name: str) -> dict[str, Any]:
        """Reconnect one server from the current config (or drop it if it is gone/disabled)."""
        async with self._lock:
            await self._disconnect(name)
            cfg = self.read_config().get(name)
            if cfg is not None:
                await self._connect(name, cfg)
        return self.server_state(name)

    # ------------------------------------------------------------------ editing (settings UI)

    async def upsert(self, name: str, cfg: dict[str, Any]) -> dict[str, Any]:
        if not _NAME_RE.match(name or ""):
            raise ValueError("name: latin letters, digits, '-', '_' and '.', up to 48 characters")
        entry = normalize_server(cfg)
        servers = self.read_config()
        servers[name] = entry
        await asyncio.to_thread(self._write_config, servers)
        return await self.restart(name)

    async def remove(self, name: str) -> None:
        servers = self.read_config()
        if servers.pop(name, None) is None:
            raise KeyError(name)
        await asyncio.to_thread(self._write_config, servers)
        async with self._lock:
            await self._disconnect(name)

    async def set_disabled(self, name: str, disabled: bool) -> dict[str, Any]:
        servers = self.read_config()
        if name not in servers:
            raise KeyError(name)
        if disabled:
            servers[name]["disabled"] = True
        else:
            servers[name].pop("disabled", None)
        await asyncio.to_thread(self._write_config, servers)
        return await self.restart(name)

    # ------------------------------------------------------------------ views

    def tools(self) -> list[Tool]:
        return [tool for tools in self._tools_by_server.values() for tool in tools]

    def server_state(self, name: str) -> dict[str, Any]:
        cfg = self.read_config().get(name) or {}
        client = self.clients.get(name)
        if cfg.get("disabled"):
            state = "disabled"
        elif client is not None and client.is_running:
            state = "connected"
        elif name in self.errors:
            state = "error"
        else:
            state = "stopped"
        env = dict(cfg.get("env") or {})
        headers = dict(cfg.get("headers") or {})
        return {
            "name": name,
            "kind": "remote" if cfg.get("url") else "local",
            "command": cfg.get("command", ""),
            "args": list(cfg.get("args") or []),
            "url": cfg.get("url", ""),
            "transport": cfg.get("transport", "http") if cfg.get("url") else "stdio",
            "env": {k: (_mask(str(v)) if _SECRET_HINT.search(k) else str(v)) for k, v in env.items()},
            "headers": {k: _mask(str(v)) for k, v in headers.items()},
            "has_token": bool(cfg.get("token") or cfg.get("authToken") or cfg.get("bearer")),
            "state": state,
            "error": self.errors.get(name, ""),
            "tools": [t.get("name", "") for t in (client.tools if client else [])],
        }

    def servers(self) -> list[dict[str, Any]]:
        return [self.server_state(name) for name in self.read_config()]

    def status(self) -> dict[str, object]:
        return {
            "servers": {
                name: {"tools": len(client.tools), "running": client.is_running}
                for name, client in self.clients.items()
            },
            "errors": dict(self.errors),
            "config_path": str(self.config_path),
        }

    async def stop(self) -> None:
        await asyncio.gather(*(client.stop() for client in self.clients.values()), return_exceptions=True)
        self.clients.clear()
        self._tools_by_server.clear()
        self._started = False
