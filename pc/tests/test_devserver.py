"""Dev-server watcher: the process manager and the error detector."""

from __future__ import annotations

import sys
import time

import pytest

from core.devserver.detect import looks_ready, scan_output
from core.devserver.manager import DevServerError, DevServerManager

# ------------------------------------------------------------------ detector


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


# ------------------------------------------------------------------ manager


def _echo_command(text: str) -> str:
    """A short command that prints a line and exits."""
    return f"echo {text}"


def _sleep_server() -> str:
    """A long-running command posing as a dev server (native to the shell)."""
    if sys.platform == "win32":
        return "Write-Host SERVERREADY; Start-Sleep -Seconds 30"
    return "echo SERVERREADY; sleep 30"


def _wait_until(condition, timeout: float = 30.0) -> None:
    """Polls until `condition()` holds. The generous deadline only matters on a slow CI runner
    (a cold PowerShell start there can take many seconds); green runs return at once."""
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.1)


def test_start_read_stop(tmp_path):
    mgr = DevServerManager()
    server = mgr.start("srv", _sleep_server(), tmp_path)
    try:
        _wait_until(lambda: server.line_count() > 0)
        new = server.read_new()
        assert any("SERVERREADY" in line for line in new)
        # A second read with no new output is empty.
        assert server.read_new() == []
        assert server.is_running()
    finally:
        assert mgr.stop("srv") is True
    assert mgr.stop("srv") is False


def test_duplicate_name_rejected(tmp_path):
    mgr = DevServerManager()
    mgr.start("dup", _sleep_server(), tmp_path)
    try:
        with pytest.raises(DevServerError, match="already running"):
            mgr.start("dup", _sleep_server(), tmp_path)
    finally:
        mgr.stop("dup")


def test_get_unknown_raises(tmp_path):
    mgr = DevServerManager()
    with pytest.raises(DevServerError, match="was not found"):
        mgr.get("нет")


def test_exited_process_status(tmp_path):
    mgr = DevServerManager()
    server = mgr.start("quick", _echo_command("привет"), tmp_path)
    try:
        _wait_until(lambda: not server.is_running())
        assert not server.is_running()
        assert server.exit_code() is not None
        # The name is freed: the server can be started again.
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
