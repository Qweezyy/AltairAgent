"""Remote MCP servers over HTTP: Streamable HTTP (the current spec) and the legacy SSE transport.

Most MCP servers people add today are remote (a URL plus a token), so the PC connects to them
directly. The transports come from the official MCP Python SDK (MIT), which tracks the spec and
handles sessions, SSE streams and resumption; our stdio client stays hand-written because it
carries Windows-specific process handling.

The SDK's clients are async context managers that must stay open for the whole connection, so
each server lives in its own background task that holds the context until `stop()`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.errors import MCPError
from core.logging_setup import get_logger
from core.mcp.results import format_call_result

logger = get_logger("mcp.http")

try:  # the SDK is bundled; without it remote servers report a clear error instead of crashing
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamablehttp_client

    SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only in a broken install
    SDK_AVAILABLE = False


class HttpMCPClient:
    """Same surface as the stdio `MCPClient`: start/stop, tools, call_tool, is_running."""

    def __init__(
        self,
        name: str,
        url: str,
        *,
        transport: str = "http",
        headers: dict[str, str] | None = None,
        startup_timeout: float = 30.0,
        call_timeout: float = 120.0,
    ) -> None:
        self.name = name
        self.url = url
        self.transport = "sse" if transport == "sse" else "http"
        self.headers = dict(headers or {})
        self.startup_timeout = startup_timeout
        self.call_timeout = call_timeout

        self.tools: list[dict[str, Any]] = []
        self.server_info: dict[str, Any] = {}

        self._session: Any = None
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._error: BaseException | None = None

    @property
    def is_running(self) -> bool:
        return self._session is not None and self._task is not None and not self._task.done()

    async def start(self) -> None:
        if not SDK_AVAILABLE:
            raise MCPError(f"[{self.name}] remote MCP needs the 'mcp' package, which is not installed.")
        self._task = asyncio.create_task(self._hold(), name=f"mcp-http-{self.name}")
        waiter = asyncio.create_task(self._ready.wait())
        try:
            done, _ = await asyncio.wait({waiter, self._task}, timeout=self.startup_timeout,
                                         return_when=asyncio.FIRST_COMPLETED)
        finally:
            waiter.cancel()
        if self._ready.is_set() and self._session is not None:
            logger.info("[%s] connected over %s, tools: %d", self.name, self.transport, len(self.tools))
            return
        await self.stop()
        if not done:
            raise MCPError(f"[{self.name}] no answer from {self.url} in {self.startup_timeout:g} s.")
        raise MCPError(f"[{self.name}] could not connect to {self.url}: {_describe(self._error)}")

    async def _hold(self) -> None:
        opener = (
            sse_client(self.url, headers=self.headers, timeout=self.startup_timeout)
            if self.transport == "sse"
            else streamablehttp_client(self.url, headers=self.headers, timeout=self.startup_timeout)
        )
        try:
            async with opener as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    self.server_info = _dump(getattr(init, "serverInfo", None))
                    self.tools = await self._list_tools(session)
                    self._session = session
                    self._ready.set()
                    await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - anyio wraps failures in exception groups
            self._error = exc
            if self._ready.is_set():
                logger.warning("[%s] connection lost: %s", self.name, _describe(exc))
        finally:
            self._session = None

    async def _list_tools(self, session: Any) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(20):  # guard against endless pagination
            page = await session.list_tools(cursor=cursor) if cursor else await session.list_tools()
            tools.extend(_dump(t) for t in page.tools)
            cursor = getattr(page, "nextCursor", None)
            if not cursor:
                break
        return tools

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        session = self._session
        if session is None:
            return f"MCP server '{self.name}' is not connected.", False
        try:
            result = await asyncio.wait_for(session.call_tool(tool_name, arguments), self.call_timeout)
        except asyncio.TimeoutError:
            return f"MCP tool '{tool_name}' did not answer in {self.call_timeout:g} s.", False
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a failed call is a tool error, not a crash
            return f"MCP tool '{tool_name}' failed: {_describe(exc)}", False
        return format_call_result(_dump(result))

    async def stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logger.debug("[%s] connection task cancelled on stop", self.name)
        self._session = None


def _dump(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if hasattr(obj, "model_dump"):
        return obj.model_dump(by_alias=True, exclude_none=True, mode="json")
    return dict(obj)


def _describe(exc: BaseException | None) -> str:
    """The innermost real reason: anyio buries it inside exception groups."""
    while exc is not None and getattr(exc, "exceptions", None):  # an exception group
        exc = exc.exceptions[0]
    if exc is None:
        return "unknown error"
    text = str(exc) or type(exc).__name__
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None):
        text = f"HTTP {response.status_code}: {text}"
    return text[:300]
