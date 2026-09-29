"""Where a running backend can be reached: `<app dir>/backend.json` ({port, pid, version}).

The backend writes it when it starts serving and removes it when it stops; the terminal client
(altair) reads it to join that backend instead of starting a second one — two backends writing
the same chats would overwrite each other. A file left by a crash is recognised by its dead pid.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from core.fs_atomic import atomic_write_text
from core.logging_setup import get_logger
from core.settings import get_settings
from core.version import __version__

logger = get_logger("backend_info")

FILE_NAME = "backend.json"


def info_path(app_dir: Path | None = None) -> Path:
    return (app_dir or get_settings().app_dir) / FILE_NAME


def advertise(port: int, app_dir: Path | None = None) -> None:
    path = info_path(app_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps({"port": port, "pid": os.getpid(), "version": __version__}))
    except OSError:
        logger.warning("could not write %s: the terminal will not find this backend", path, exc_info=True)


def withdraw(app_dir: Path | None = None) -> None:
    path = info_path(app_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if data.get("pid") == os.getpid():  # only our own record: a newer backend may have replaced it
        path.unlink(missing_ok=True)


def read(app_dir: Path | None = None) -> dict | None:
    """The advertised backend, if its process is alive."""
    try:
        data = json.loads(info_path(app_dir).read_text(encoding="utf-8"))
        port, pid = int(data["port"]), int(data["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return data if pid_alive(pid) and port > 0 else None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
