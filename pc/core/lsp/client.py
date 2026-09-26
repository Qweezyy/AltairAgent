"""Клиент Language Server Protocol поверх stdio (JSON-RPC).

Даёт семантическую навигацию через настоящий language server: go-to-definition,
find-references, hover. В отличие от `find_symbol` (поиск по имени), сервер
понимает импорты, псевдонимы, переопределения и наследование, а также умеет это
для не-Python языков (если их сервер установлен).

Транспорт нарочно на ПОТОКАХ (Popen + поток-читатель), а не на asyncio-сабпроцессе:
на Windows asyncio-пайпы к дочернему процессу глохнут, когда сервер работает в не
главном потоке (как uvicorn в десктопном приложении) — та же причина, по которой
терминал сделан на потоках. Ответы сервера пробрасываются в event loop через
`call_soon_threadsafe`, разрешая future по id запроса.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from pathlib import Path
from typing import Any

from core.logging_setup import get_logger

logger = get_logger("lsp.client")

#: Дефолтный таймаут одного запроса к серверу.
_REQUEST_TIMEOUT = 20.0


class LspError(RuntimeError):
    """Сбой запуска сервера или запроса к нему."""


def _uri(path: Path) -> str:
    return path.resolve().as_uri()


class LspClient:
    """Одна сессия с language server для конкретного корня проекта."""

    def __init__(self, command: list[str], root: Path, language_id: str) -> None:
        self._command = command
        self._root = root.resolve()
        self._language_id = language_id
        self._proc: subprocess.Popen | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reader: threading.Thread | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._opened: dict[str, int] = {}  # uri -> version
        self._ready: dict[str, asyncio.Event] = {}  # uri -> проанализирован сервером
        self._write_lock = threading.Lock()
        self._closed = threading.Event()

    # ------------------------------------------------------------------ запуск

    async def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        try:
            # Именно Popen (не asyncio-сабпроцесс): на Windows asyncio-пайпы к
            # дочернему процессу глохнут вне главного потока. Спавн быстрый и не
            # блокирует loop надолго.
            self._proc = subprocess.Popen(  # noqa: S603, ASYNC220 - см. модульный докстринг
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=str(self._root),
                bufsize=0,
            )
        except OSError as exc:
            raise LspError(f"Не удалось запустить language server: {exc}") from exc

        self._reader = threading.Thread(target=self._read_loop, name="lsp-reader", daemon=True)
        self._reader.start()

        await self._request(
            "initialize",
            {
                "processId": None,
                "rootUri": _uri(self._root),
                # БЕЗ workspaceFolders pyright не загружает pyproject.toml и не
                # настраивает окружение проекта: импорты своих же пакетов и
                # ре-экспорты не резолвятся (definition/hover приходят пустыми).
                "workspaceFolders": [{"uri": _uri(self._root), "name": self._root.name}],
                "capabilities": {
                    "textDocument": {
                        "definition": {"linkSupport": False},
                        "references": {},
                        "hover": {"contentFormat": ["plaintext", "markdown"]},
                    }
                },
            },
            timeout=40.0,  # первый запуск langserver может подтягивать node
        )
        self._notify("initialized", {})

    # ------------------------------------------------------------------ чтение

    def _read_loop(self) -> None:
        stream = self._proc.stdout if self._proc else None
        if stream is None:
            return
        buffer = b""
        while not self._closed.is_set():
            try:
                chunk = stream.read(1)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            buffer += chunk
            # Заголовок кадра завершён — читаем тело по Content-Length.
            if buffer.endswith(b"\r\n\r\n"):
                length = _content_length(buffer)
                buffer = b""
                if length is None:
                    continue
                body = self._read_exact(stream, length)
                if body is None:
                    break
                self._dispatch(body)

    @staticmethod
    def _read_exact(stream: Any, length: int) -> bytes | None:
        data = b""
        while len(data) < length:
            piece = stream.read(length - len(data))
            if not piece:
                return None
            data += piece
        return data

    def _dispatch(self, body: bytes) -> None:
        try:
            message = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        loop = self._loop
        if loop is None:
            return
        msg_id = message.get("id")
        if msg_id in self._pending and "id" in message:
            future = self._pending.pop(msg_id)
            if "error" in message:
                loop.call_soon_threadsafe(
                    future.set_exception, LspError(str(message["error"]))
                )
            else:
                loop.call_soon_threadsafe(future.set_result, message.get("result"))
            return
        # publishDiagnostics = сервер закончил анализ файла: снимаем готовность.
        if message.get("method") == "textDocument/publishDiagnostics":
            uri = message.get("params", {}).get("uri")
            if uri:
                loop.call_soon_threadsafe(self._mark_ready, uri)

    def _mark_ready(self, uri: str) -> None:
        event = self._ready.get(uri)
        if event is not None:
            event.set()

    # ------------------------------------------------------------------ запись

    def _write(self, message: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise LspError("Language server не запущен.")
        body = json.dumps(message).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode()
        with self._write_lock:
            try:
                self._proc.stdin.write(header + body)
                self._proc.stdin.flush()
            except (OSError, ValueError) as exc:
                raise LspError(f"Language server закрыл ввод: {exc}") from exc

    def _notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def _request(
        self, method: str, params: dict, *, timeout: float = _REQUEST_TIMEOUT
    ) -> Any:
        loop = self._loop or asyncio.get_running_loop()
        msg_id = self._next_id
        self._next_id += 1
        future: asyncio.Future = loop.create_future()
        self._pending[msg_id] = future
        self._write({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(msg_id, None)
            raise LspError(f"Language server не ответил на {method} за {timeout:g} с.") from exc

    # ------------------------------------------------------------- документы

    def _ensure_open(self, path: Path) -> str:
        """Открывает (или переоткрывает свежей версией) документ на сервере."""
        uri = _uri(path)
        text = path.read_text(encoding="utf-8", errors="replace")
        version = self._opened.get(uri, 0) + 1
        self._opened[uri] = version
        # Новый анализ — сбрасываем готовность: ждём свежий publishDiagnostics.
        self._ready[uri] = asyncio.Event()
        if version == 1:
            self._notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": self._language_id,
                        "version": version,
                        "text": text,
                    }
                },
            )
        else:
            self._notify(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": uri, "version": version},
                    "contentChanges": [{"text": text}],
                },
            )
        return uri

    async def _query(self, method: str, path: Path, line: int, char: int, extra: dict) -> Any:
        uri = self._ensure_open(path)
        # Ждём, пока сервер проанализирует файл (сигнал — publishDiagnostics),
        # иначе definition/hover приходят пустыми на «холодном» файле.
        event = self._ready.get(uri)
        if event is not None:
            try:
                await asyncio.wait_for(event.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                pass
        params = {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": char},
            **extra,
        }
        result = None
        for attempt in range(3):
            if attempt:
                await asyncio.sleep(0.6)
            result = await self._request(method, params)
            if result:
                break
        return result

    async def definition(self, path: Path, line: int, char: int) -> list[dict]:
        result = await self._query("textDocument/definition", path, line, char, {})
        return _as_locations(result)

    async def references(self, path: Path, line: int, char: int) -> list[dict]:
        result = await self._query(
            "textDocument/references",
            path,
            line,
            char,
            {"context": {"includeDeclaration": True}},
        )
        return _as_locations(result)

    async def hover(self, path: Path, line: int, char: int) -> str:
        result = await self._query("textDocument/hover", path, line, char, {})
        return _hover_text(result)

    # ------------------------------------------------------------------ стоп

    def close(self) -> None:
        self._closed.set()
        if self._proc is None:
            return
        try:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
        except OSError:
            pass
        self._proc = None


def _content_length(header: bytes) -> int | None:
    for line in header.decode("ascii", "replace").split("\r\n"):
        if line.lower().startswith("content-length:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def _as_locations(result: Any) -> list[dict]:
    """Приводит definition/references к списку {uri, range}."""
    if not result:
        return []
    if isinstance(result, dict):
        return [result]
    return [item for item in result if isinstance(item, dict)]


def _hover_text(result: Any) -> str:
    if not result:
        return ""
    contents = result.get("contents") if isinstance(result, dict) else None
    if isinstance(contents, dict):
        return str(contents.get("value", "")).strip()
    if isinstance(contents, list):
        parts = []
        for item in contents:
            if isinstance(item, dict):
                parts.append(str(item.get("value", "")))
            else:
                parts.append(str(item))
        return "\n".join(p for p in parts if p.strip()).strip()
    return str(contents or "").strip()
