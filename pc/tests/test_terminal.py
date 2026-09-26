"""Интерактивный терминал: PTY-сессия и веб-сокет."""

from __future__ import annotations

import asyncio
import sys

import pytest

from core.terminal import TerminalSession
from core.terminal.session import default_shell, windows_cmdline


def test_default_shell_nonempty():
    shell = default_shell()
    assert isinstance(shell, list) and shell and shell[0]


def test_windows_cmdline_quotes_path_with_spaces():
    """Регрессия: путь к shell с пробелами должен остаться одним аргументом.

    Иначе WinPTY запускает «C:\\Program» вместо pwsh.exe и терминал не стартует.
    """
    cmdline = windows_cmdline([r"C:\Program Files\PowerShell\7\pwsh.exe", "-NoLogo"])
    assert cmdline == r'"C:\Program Files\PowerShell\7\pwsh.exe" -NoLogo'


def test_windows_cmdline_leaves_simple_args_untouched():
    assert windows_cmdline(["cmd.exe"]) == "cmd.exe"
    assert windows_cmdline([r"C:\Windows\System32\bash.exe", "-i"]) == r"C:\Windows\System32\bash.exe -i"


@pytest.mark.skipif(sys.platform != "win32", reason="PTY-движок проверяем на Windows")
def test_pty_echo_roundtrip():
    """Живой PTY: пишем команду — получаем её вывод."""
    loop = asyncio.new_event_loop()
    chunks: list[str] = []
    done = asyncio.Event()

    def on_output(data: str) -> None:
        if data:
            chunks.append(data)
        else:
            loop.call_soon_threadsafe(done.set)

    async def scenario():
        session = TerminalSession(cols=80, rows=24)
        session.start(loop, on_output)
        session.write("echo TERMINALPROBE\r\n")
        # Ждём появления маркера в выводе (echo печатает и команду, и результат).
        for _ in range(100):
            if any("TERMINALPROBE" in c for c in chunks):
                break
            await asyncio.sleep(0.05)
        session.write("exit\r\n")
        try:
            await asyncio.wait_for(done.wait(), timeout=5)
        except asyncio.TimeoutError:
            session.close()
        assert any("TERMINALPROBE" in c for c in chunks)
        assert not session.is_alive()

    try:
        loop.run_until_complete(scenario())
    finally:
        loop.close()


@pytest.mark.skipif(sys.platform != "win32", reason="PTY-движок проверяем на Windows")
def test_pty_resize_does_not_crash():
    loop = asyncio.new_event_loop()

    async def scenario():
        session = TerminalSession(cols=80, rows=24)
        session.start(loop, lambda _d: None)
        session.resize(120, 40)  # не должно бросить
        session.resize(0, 0)  # бессмысленный размер — тоже без исключения
        session.close()

    try:
        loop.run_until_complete(scenario())
    finally:
        loop.close()


@pytest.mark.skipif(sys.platform != "win32", reason="PTY-движок проверяем на Windows")
def test_terminal_websocket_roundtrip(settings, monkeypatch):
    """WS-протокол терминала: ввод команды доходит, вывод возвращается."""
    from fastapi.testclient import TestClient

    import server.terminal_ws as tws
    from server.app import create_app

    monkeypatch.setattr(tws, "get_settings", lambda: settings)
    app = create_app()
    with TestClient(app) as client, client.websocket_connect(
        "/ws/terminal?cols=80&rows=24"
    ) as ws:
        ws.send_json({"type": "input", "data": "echo WSROUNDTRIP\r"})
        buf = ""
        for _ in range(80):
            try:
                buf += ws.receive_text()
            except Exception:  # noqa: BLE001 - таймаут/закрытие завершает цикл
                break
            if "WSROUNDTRIP" in buf:
                break
        ws.send_json({"type": "input", "data": "exit\r"})
    assert "WSROUNDTRIP" in buf
