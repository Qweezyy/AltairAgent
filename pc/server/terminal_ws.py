"""WebSocket-мост между xterm.js в интерфейсе и PTY-процессом.

Протокол прост и асимметричен:
* клиент → сервер: JSON-команды `{"type":"input","data":...}` и
  `{"type":"resize","cols":N,"rows":M}`;
* сервер → клиент: сырой текст вывода оболочки (кадры text), а по завершении —
  строка `\r\n[процесс завершён]`.

Вывод PTY читается в фоновом потоке и складывается в asyncio-очередь: отдельная
задача-отправитель сливает её по порядку. Без очереди быстрый поток вывода
переупорядочился бы между конкурентными `send_text`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

from fastapi import WebSocket, WebSocketDisconnect

from core.logging_setup import get_logger
from core.settings import get_settings
from core.terminal import TerminalSession, TerminalUnavailable

logger = get_logger("terminal.ws")

#: Защита от абсурдных размеров окна (кривой клиент или переполнение).
_MAX_COLS = 500
_MAX_ROWS = 300


def _clamp(value: object, default: int, high: int) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(num, high))


async def serve_terminal(websocket: WebSocket) -> None:
    await websocket.accept()
    settings = get_settings()

    params = websocket.query_params
    cols = _clamp(params.get("cols"), 80, _MAX_COLS)
    rows = _clamp(params.get("rows"), 24, _MAX_ROWS)
    # Терминал открывается в рабочей папке текущего чата, если её прислали,
    # иначе — в общем workspace приложения.
    cwd = params.get("cwd") or str(settings.workspace)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def on_output(data: str) -> None:
        # Пустая строка от читателя = процесс завершился (EOF).
        queue.put_nowait(data if data else None)

    session = TerminalSession(cwd=cwd, cols=cols, rows=rows)
    try:
        session.start(loop, on_output)
    except TerminalUnavailable as exc:
        await websocket.send_text(f"\r\n\x1b[31m{exc}\x1b[0m\r\n")
        await websocket.close()
        return

    def apply(message: dict) -> None:
        kind = message.get("type")
        if kind == "input":
            session.write(str(message.get("data", "")))
        elif kind == "resize":
            session.resize(
                _clamp(message.get("cols"), cols, _MAX_COLS),
                _clamp(message.get("rows"), rows, _MAX_ROWS),
            )

    # Вывод PTY и приём от клиента — две независимые задачи. Порядок вывода
    # сохраняет очередь: без неё быстрый поток переупорядочился бы между
    # конкурентными `send_text`.
    async def pump_output() -> None:
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            try:
                await websocket.send_text(chunk)
            except (WebSocketDisconnect, RuntimeError):
                break

    sender = asyncio.ensure_future(pump_output())
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                apply(json.loads(raw))
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - терминал не должен ронять сервер
        logger.exception("Сбой в терминальном веб-сокете")
    finally:
        session.close()
        queue.put_nowait(None)
        with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(sender, timeout=2.0)
        try:
            await websocket.send_text("\r\n\x1b[90m[процесс завершён]\x1b[0m\r\n")
        except (WebSocketDisconnect, RuntimeError):
            pass
