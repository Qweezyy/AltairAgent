"""The desktop shell stops the backend gracefully: it closes the backend's stdin, and the
backend stops its runs as "the app closed" and stores every chat before it exits.

Before, the shell killed the backend: a running task lost its last seconds and a wait in
progress looked like a crash.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

PC = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_closing_stdin_stops_the_backend_gracefully(tmp_path):
    port = _free_port()
    app_dir, workspace = tmp_path / "app", tmp_path / "ws"
    app_dir.mkdir()
    workspace.mkdir()
    env = {**os.environ, "APP_PATH": str(app_dir), "WORKSPACE_PATH": str(workspace), "BRIDGE_LAN": "false",
           "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [sys.executable, str(PC / "main.py"), "--server", "--host", "127.0.0.1", "--port", str(port),
         "--stop-on-stdin-eof"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=tmp_path, env=env,
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.3)
        else:
            raise AssertionError("the backend did not start")

        started = time.monotonic()
        proc.stdin.close()                                        # what the shell does on exit
        code = proc.wait(timeout=20)
        took = time.monotonic() - started
    finally:
        if proc.poll() is None:
            proc.kill()
    assert code == 0
    assert took < 6, took                                         # the server stopped, not the fallback kill
    logs = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in (app_dir / "logs").glob("*.log*"))
    assert "The shell asked to close" in logs


def test_a_backend_without_the_flag_ignores_its_stdin(tmp_path):
    """Started any other way, a backend may have no stdin at all (it reads as closed at once):
    it must not stop because of that."""
    port = _free_port()
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    env = {**os.environ, "APP_PATH": str(app_dir), "WORKSPACE_PATH": str(tmp_path), "BRIDGE_LAN": "false"}
    proc = subprocess.Popen(
        [sys.executable, str(PC / "main.py"), "--server", "--host", "127.0.0.1", "--port", str(port)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=tmp_path, env=env,
    )
    try:
        deadline = time.monotonic() + 60
        up = False
        while time.monotonic() < deadline and not up:
            try:
                up = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1).status_code == 200
            except httpx.HTTPError:
                time.sleep(0.3)
        assert up
        time.sleep(1.5)
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait(timeout=10)
