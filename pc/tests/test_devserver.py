"""Наблюдатель за dev-серверами: менеджер процессов и детектор ошибок."""

from __future__ import annotations

import sys
import time

import pytest

from core.devserver.detect import looks_ready, scan_output
from core.devserver.manager import DevServerError, DevServerManager

# ------------------------------------------------------------------ детектор


def test_scan_detects_python_traceback():
    lines = [
        "INFO: сервер стартовал",
        "Traceback (most recent call last):",
        '  File "app.py", line 10, in <module>',
        "ValueError: плохое значение",
    ]
    errors = scan_output(lines)
    sources = {e.source for e in errors}
    assert "python" in sources
    assert any("ValueError" in e.text for e in errors)


def test_scan_detects_typescript_and_vite():
    assert scan_output(["src/a.ts(3,5): error TS2304: Cannot find name 'x'."])
    assert scan_output(["[vite] Internal server error: Failed to resolve import"])


def test_scan_clean_output_empty():
    assert scan_output(["ready in 300 ms", "Local: http://localhost:5173/"]) == []


def test_looks_ready():
    assert looks_ready(["webpack compiled successfully"])
    assert looks_ready(["Application startup complete."])
    assert not looks_ready(["still bundling..."])


# ------------------------------------------------------------------ менеджер


def _echo_command(text: str) -> str:
    """Короткая команда, печатающая строку и завершающаяся."""
    return f"echo {text}"


def _sleep_server() -> str:
    """Долгоживущая команда, имитирующая dev-сервер (нативная для оболочки)."""
    if sys.platform == "win32":
        return "Write-Host SERVERREADY; Start-Sleep -Seconds 30"
    return "echo SERVERREADY; sleep 30"


def test_start_read_stop(tmp_path):
    mgr = DevServerManager()
    server = mgr.start("srv", _sleep_server(), tmp_path)
    try:
        # Ждём стартовую строку.
        for _ in range(40):
            if server.line_count() > 0:
                break
            time.sleep(0.1)
        new = server.read_new()
        assert any("SERVERREADY" in line for line in new)
        # Повторное чтение без новых логов пусто.
        assert server.read_new() == []
        assert server.is_running()
    finally:
        assert mgr.stop("srv") is True
    assert mgr.stop("srv") is False


def test_duplicate_name_rejected(tmp_path):
    mgr = DevServerManager()
    mgr.start("dup", _sleep_server(), tmp_path)
    try:
        with pytest.raises(DevServerError, match="уже запущен"):
            mgr.start("dup", _sleep_server(), tmp_path)
    finally:
        mgr.stop("dup")


def test_get_unknown_raises(tmp_path):
    mgr = DevServerManager()
    with pytest.raises(DevServerError, match="не найден"):
        mgr.get("нет")


def test_exited_process_status(tmp_path):
    mgr = DevServerManager()
    server = mgr.start("quick", _echo_command("привет"), tmp_path)
    try:
        for _ in range(40):
            if not server.is_running():
                break
            time.sleep(0.1)
        assert not server.is_running()
        assert server.exit_code() is not None
        # Имя освобождается: можно запустить снова.
        again = mgr.start("quick", _sleep_server(), tmp_path)
        assert again.is_running()
    finally:
        mgr.shutdown()


def test_shutdown_stops_all(tmp_path):
    mgr = DevServerManager()
    mgr.start("a", _sleep_server(), tmp_path)
    mgr.start("b", _sleep_server(), tmp_path)
    assert len(mgr.all()) == 2
    mgr.shutdown()
    assert mgr.all() == []
