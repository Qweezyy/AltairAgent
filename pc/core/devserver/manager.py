"""The manager of long-running dev servers.

Unlike `execute_command`, which runs a command and waits for it to end, a dev server
(`npm run dev`, `uvicorn --reload`, `cargo watch`) never ends by itself: it stays up and keeps
printing logs. The agent needs to start one in the background, read the NEW output since the
last time, catch a build error in it, fix the file and read the logs again to see the rebuild
go through.

The output (stdout and stderr merged, as a person sees them in a terminal) is read by a
background thread into a ring buffer of lines. Each server has its own read cursor:
`read_dev_server` returns only what appeared after the previous read.

The manager is one per process: dev servers are a shared resource of the machine anyway, and
they cannot live inside one run — a server must outlive many tool calls and the whole chat.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from core.logging_setup import get_logger
from core.utils.proc import shell_argv

logger = get_logger("devserver")

#: How many recent log lines are kept per server. More is not needed: the agent reads
#: incrementally, and the tail is enough to make out an error.
MAX_LINES = 2000

#: At most this many servers at once — a guard against leaked processes when the agent
#: forgets to stop the old ones.
MAX_SERVERS = 8

#: The longest a server's name may be.
_NAME_MAX = 40


class DevServerError(RuntimeError):
    """A dev server could not be managed (the name is taken, the limit is reached, not found)."""


@dataclass
class _LogLine:
    ts: float
    text: str


@dataclass
class DevServerProcess:
    """One running dev server and its log."""

    name: str
    command: str
    cwd: str
    proc: subprocess.Popen
    started_at: float = field(default_factory=time.time)
    _lines: deque[_LogLine] = field(default_factory=lambda: deque(maxlen=MAX_LINES))
    _lock: threading.Lock = field(default_factory=threading.Lock)
    #: How many lines were produced in all (a running count, not the deque's length).
    _emitted_total: int = 0
    _read_cursor: int = 0
    _reader: threading.Thread | None = None

    def start_reader(self) -> None:
        self._reader = threading.Thread(
            target=self._pump, name=f"devserver-{self.name}", daemon=True
        )
        self._reader.start()

    def _pump(self) -> None:
        """Reads the merged stdout/stderr line by line into the ring buffer."""
        stream = self.proc.stdout
        if stream is None:
            return
        for raw in iter(stream.readline, b""):
            text = raw.decode("utf-8", "replace").rstrip("\r\n")
            with self._lock:
                self._lines.append(_LogLine(time.time(), text))
                self._emitted_total += 1
        # The stream closed: the process has ended.

    # ------------------------------------------------------------------ reading

    def read_new(self) -> list[str]:
        """The lines that appeared since the last read. Moves the cursor."""
        with self._lock:
            behind = self._emitted_total - self._read_cursor
            if behind <= 0:
                return []
            # Behind by more than the buffer holds: some lines are lost — return what is
            # there and say plainly how many were skipped.
            available = min(behind, len(self._lines))
            lines = [ln.text for ln in list(self._lines)[-available:]]
            lost = behind - available
            self._read_cursor = self._emitted_total
            if lost > 0:
                lines.insert(0, f"[... {lost} log lines skipped ...]")
            return lines

    def tail(self, count: int) -> list[str]:
        """The last `count` lines, without moving the cursor."""
        with self._lock:
            return [ln.text for ln in list(self._lines)[-count:]]

    def line_count(self) -> int:
        with self._lock:
            return self._emitted_total

    # ------------------------------------------------------------------ state

    def is_running(self) -> bool:
        return self.proc.poll() is None

    def exit_code(self) -> int | None:
        return self.proc.poll()

    def status(self) -> dict:
        code = self.exit_code()
        return {
            "name": self.name,
            "command": self.command,
            "cwd": self.cwd,
            "running": code is None,
            "exit_code": code,
            "uptime_sec": round(time.time() - self.started_at, 1),
            "log_lines": self.line_count(),
        }

    def stop(self, timeout: float = 5.0) -> None:
        """Stops the process gently, then for good.

        On Windows a dev server is usually `powershell → npm → node …`, and a plain terminate()
        ends only the shell, leaving node on its port. So on Windows the whole tree is killed
        with `taskkill /T`.
        """
        if self.proc.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                    capture_output=True,
                    timeout=timeout,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                logger.warning("taskkill for %s failed: %s", self.name, exc)
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            return
        try:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        except OSError as exc:
            logger.warning("Could not stop dev server %s: %s", self.name, exc)


class DevServerManager:
    """The process's running dev servers."""

    def __init__(self) -> None:
        self._servers: dict[str, DevServerProcess] = {}
        self._lock = threading.Lock()

    def start(
        self, name: str, command: str, cwd: Path | str, *, env: dict[str, str] | None = None
    ) -> DevServerProcess:
        name = (name or "").strip()
        if not name or len(name) > _NAME_MAX:
            raise DevServerError("A server's name must be 1–40 characters.")
        with self._lock:
            existing = self._servers.get(name)
            if existing and existing.is_running():
                raise DevServerError(
                    f"Server '{name}' is already running. Stop it or pick another name."
                )
            # A process that has ended gives its name back.
            if existing:
                del self._servers[name]
            running = [s for s in self._servers.values() if s.is_running()]
            if len(running) >= MAX_SERVERS:
                raise DevServerError(
                    f"The limit of {MAX_SERVERS} servers is reached. Stop the ones not needed."
                )

            proc_env = {**os.environ, **(env or {})} if env else None
            proc = subprocess.Popen(  # noqa: S603 - the command passed the tool's checks
                shell_argv(command),
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # one stream, as in a terminal
                stdin=subprocess.DEVNULL,  # a dev server must not wait for input
                bufsize=0,
                env=proc_env,
            )
            server = DevServerProcess(name=name, command=command, cwd=str(cwd), proc=proc)
            server.start_reader()
            self._servers[name] = server
            logger.info("Dev server '%s' started: %s (pid %s)", name, command, proc.pid)
            return server

    def get(self, name: str) -> DevServerProcess:
        with self._lock:
            server = self._servers.get(name)
        if server is None:
            raise DevServerError(f"Server '{name}' was not found. Running: {self._names() or '—'}")
        return server

    def stop(self, name: str) -> bool:
        with self._lock:
            server = self._servers.pop(name, None)
        if server is None:
            return False
        server.stop()
        return True

    def all(self) -> list[DevServerProcess]:
        with self._lock:
            return list(self._servers.values())

    def _names(self) -> str:
        return ", ".join(self._servers.keys())

    def shutdown(self) -> None:
        """Stops every server. Called when the app shuts down."""
        for server in self.all():
            server.stop()
        with self._lock:
            self._servers.clear()


_manager: DevServerManager | None = None
_manager_lock = threading.Lock()


def get_manager() -> DevServerManager:
    """The process's manager (created on first use)."""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = DevServerManager()
    return _manager
