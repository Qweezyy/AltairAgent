"""Finding the running backend, or starting one for the terminal."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from core import backend_info
from core.settings import get_settings

#: The desktop shell's usual port: tried when there is no backend.json (an older backend).
DEFAULT_PORT = 8137
START_TIMEOUT = 90.0


@dataclass
class Backend:
    port: int
    proc: subprocess.Popen | None = None  # ours to stop; None = someone else's (the window's)
    #: A server shown through this PC's backend (its tunnel, server/bodies.py); "" = this PC.
    body: str = ""

    @property
    def root(self) -> str:
        """This PC's own backend, whatever body is shown (the bodies, the servers' setup)."""
        return f"http://127.0.0.1:{self.port}"

    @property
    def http(self) -> str:
        return self.root + (f"/b/{self.body}" if self.body else "")

    @property
    def ws(self) -> str:
        return f"ws://127.0.0.1:{self.port}" + (f"/b/{self.body}" if self.body else "") + "/ws"

    def stop(self) -> None:
        """Our own backend stops gracefully: closing its stdin asks it to (main.py)."""
        if self.proc is None or self.proc.poll() is not None:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=12)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()


def healthy(port: int, timeout: float = 1.5) -> bool:
    try:
        response = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=timeout)
        return response.status_code == 200 and response.json().get("status") == "ok"
    except (httpx.HTTPError, ValueError):
        return False


def find_running() -> Backend | None:
    info = backend_info.read()
    if info and healthy(int(info["port"])):
        return Backend(int(info["port"]))
    return None


def backend_command(port: int) -> list[str]:
    """The backend of this installation: the packaged exe next to us, or main.py from sources."""
    args = ["--server", "--host", "127.0.0.1", "--port", str(port), "--stop-on-stdin-eof"]
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).parent
        name = "LocalAIAgent.exe" if os.name == "nt" else "LocalAIAgent"
        # altair lives in bin/ of the app folder (see build_app.py).
        exe = next((d / name for d in (here.parent, here) if (d / name).exists()), here.parent / name)
        return [str(exe), *args]
    return [sys.executable, str(Path(__file__).resolve().parents[1] / "main.py"), *args]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_own() -> Backend:
    port = free_port()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    logs = get_settings().app_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        backend_command(port), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, creationflags=flags,
    )
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"the backend exited with code {proc.returncode} (see {logs / 'agent.log'})")
        if healthy(port, 1.0):
            return Backend(port, proc)
        time.sleep(0.3)
    proc.kill()
    raise RuntimeError(f"the backend did not answer in {START_TIMEOUT:g} s")


def connect() -> tuple[Backend, bool]:
    """(backend, joined an existing one)."""
    found = find_running()
    if found is not None:
        return found, True
    if healthy(DEFAULT_PORT, 0.7):
        return Backend(DEFAULT_PORT), True
    return start_own(), False
