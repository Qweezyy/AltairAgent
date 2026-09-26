"""Клиент MCP-сервера по stdio (JSON-RPC 2.0, сообщения разделены \\n).

Что здесь важно и почему:
  * stderr сервера постоянно вычитывается — иначе буфер трубы переполнится
    и сервер намертво зависнет;
  * на запросы сервера к клиенту (ping и т.п.) обязательно отвечаем, иначе
    некоторые серверы блокируются;
  * при падении процесса все ожидающие запросы получают ошибку, а не висят вечно.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from core.errors import MCPError
from core.logging_setup import get_logger
from core.mcp.results import format_call_result
from core.utils.proc import no_window_kwargs
from core.utils.text import decode_bytes

logger = get_logger("mcp.client")

PROTOCOL_VERSION = "2025-06-18"
#: Команды, которые на Windows являются .cmd-обёртками и требуют shell.
WINDOWS_SHELL_COMMANDS = {"npx", "npm", "pnpm", "yarn", "uvx", "bunx", "deno"}


class MCPClient:
    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        *,
        startup_timeout: float = 30.0,
        call_timeout: float = 120.0,
    ) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.startup_timeout = startup_timeout
        self.call_timeout = call_timeout

        self.tools: list[dict[str, Any]] = []
        self.server_info: dict[str, Any] = {}

        self._process: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._request_id = 0
        self._stderr_tail: list[str] = []

    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        """Запускает процесс и выполняет handshake. Кидает MCPError при неудаче."""
        env = {**os.environ, **self.env, "PYTHONIOENCODING": "utf-8"}
        argv = [self.command, *self.args]

        try:
            if sys.platform == "win32" and (
                self.command in WINDOWS_SHELL_COMMANDS or self.command.endswith((".cmd", ".bat"))
            ):
                self._process = await asyncio.create_subprocess_shell(
                    " ".join(_quote(part) for part in argv),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
            else:
                self._process = await asyncio.create_subprocess_exec(
                    self.command,
                    *self.args,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
        except (OSError, NotImplementedError) as exc:
            raise MCPError(f"[{self.name}] could not start '{self.command}': {exc}") from exc

        self._tasks = [
            asyncio.create_task(self._read_stdout(), name=f"mcp-stdout-{self.name}"),
            asyncio.create_task(self._read_stderr(), name=f"mcp-stderr-{self.name}"),
        ]

        try:
            init = await self._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "LocalAIAgent", "version": "1.0.0"},
                },
                timeout=self.startup_timeout,
            )
            self.server_info = init.get("serverInfo", {})
            await self._notify("notifications/initialized", {})
            self.tools = await self._list_tools()
        except Exception as exc:
            await self.stop()
            hint = f" stderr: {' | '.join(self._stderr_tail[-3:])}" if self._stderr_tail else ""
            raise MCPError(f"[{self.name}] handshake failed: {exc}.{hint}") from exc

        logger.info("[%s] connected, tools: %d", self.name, len(self.tools))

    async def _list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(20):  # защита от бесконечной пагинации
            params = {"cursor": cursor} if cursor else {}
            result = await self._request("tools/list", params, timeout=self.startup_timeout)
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Вызывает инструмент. Возвращает (текст, успех)."""
        if not self.is_running:
            return f"MCP server '{self.name}' is not running.", False
        try:
            result = await self._request(
                "tools/call",
                {"name": tool_name, "arguments": arguments},
                timeout=self.call_timeout,
            )
        except MCPError as exc:
            return str(exc), False
        except asyncio.TimeoutError:
            return f"MCP tool '{tool_name}' did not answer in {self.call_timeout:g} s.", False

        return format_call_result(result)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

        for future in self._pending.values():
            if not future.done():
                future.set_exception(MCPError(f"[{self.name}] connection closed."))
        self._pending.clear()

        process, self._process = self._process, None
        if process is None or process.returncode is not None:
            return
        if process.stdin is not None:
            process.stdin.close()
        if sys.platform == "win32":
            # npx/uvx run through cmd.exe: terminating it leaves node/python orphans alive,
            # so every reconnect would leak a server. taskkill /T takes the whole tree.
            await _kill_tree_windows(process.pid)
        try:
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except (asyncio.TimeoutError, ProcessLookupError, OSError):
            if process.returncode is not None:
                return
            try:
                process.kill()
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError, OSError):
                logger.warning("[%s] server process did not exit", self.name)

    # ------------------------------------------------------------------

    async def _request(
        self, method: str, params: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        if self._process is None or self._process.stdin is None:
            raise MCPError(f"[{self.name}] the server process is not running.")

        self._request_id += 1
        request_id = self._request_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future

        await self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise MCPError(f"[{self.name}] '{method}' got no answer in {timeout:g} s.") from None

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def _write(self, message: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise MCPError(f"[{self.name}] no stdin pipe.")
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        self._process.stdin.write(data)
        try:
            await self._process.stdin.drain()
        except (ConnectionResetError, BrokenPipeError) as exc:
            raise MCPError(f"[{self.name}] the server closed the connection: {exc}") from exc

    async def _read_stdout(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        stream = self._process.stdout
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = decode_bytes(line).strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    logger.debug("[%s] non-JSON line on stdout: %s", self.name, text[:200])
                    continue
                await self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] stdout reader stopped: %s", self.name, exc)
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(MCPError(f"[{self.name}] the server exited unexpectedly."))
            self._pending.clear()

    async def _dispatch(self, message: dict[str, Any]) -> None:
        # Ответ на наш запрос
        if "id" in message and ("result" in message or "error" in message):
            future = self._pending.pop(message["id"], None)
            if future is None or future.done():
                return
            if "error" in message:
                future.set_exception(MCPError(f"[{self.name}] {message['error']}"))
            else:
                future.set_result(message.get("result") or {})
            return

        # Запрос от сервера к нам — обязаны ответить, иначе сервер может ждать вечно.
        if "id" in message and "method" in message:
            method = message["method"]
            result: dict[str, Any] = {} if method == "ping" else {}
            await self._write({"jsonrpc": "2.0", "id": message["id"], "result": result})
            return

        # Уведомления сервера игнорируем (логи, изменения списка инструментов).
        logger.debug("[%s] notification: %s", self.name, message.get("method"))

    async def _read_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        stream = self._process.stderr
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = decode_bytes(line).strip()
                if text:
                    self._stderr_tail = [*self._stderr_tail[-9:], text]
                    logger.debug("[%s stderr] %s", self.name, text[:500])
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - stderr не критичен
            pass


def _quote(part: str) -> str:
    return f'"{part}"' if " " in part and not part.startswith('"') else part


async def _kill_tree_windows(pid: int) -> None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(pid), "/T", "/F",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            **no_window_kwargs(),
        )
        await asyncio.wait_for(proc.wait(), timeout=10.0)
    except (OSError, asyncio.TimeoutError) as exc:
        logger.warning("taskkill for MCP server pid %s failed: %s", pid, exc)
