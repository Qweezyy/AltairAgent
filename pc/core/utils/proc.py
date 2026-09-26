"""Запуск внешних процессов без блокировки event loop.

На Windows asyncio-подпроцессы работают только на ProactorEventLoop.
Если цикл не поддерживает их (SelectorEventLoop), автоматически падаем
в поток с обычным subprocess — агент продолжает работать.
"""

from __future__ import annotations

import asyncio
import functools
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core.logging_setup import get_logger
from core.utils.text import decode_bytes

logger = get_logger("utils.proc")

# Windows: when the app runs windowed (no console of its own, as the packaged
# exe does), launching a console program (powershell, git, python, adb, node…)
# makes Windows allocate a NEW console window that flashes on screen. This flag
# suppresses it. No effect / not passed on POSIX.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def no_window_kwargs() -> dict[str, int]:
    """subprocess/asyncio kwargs that keep a child from flashing a console window."""
    return {"creationflags": CREATE_NO_WINDOW} if CREATE_NO_WINDOW else {}


# Flags a caller might set on purpose to get a visible/own console — we leave those alone.
_CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
_DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)


def merge_no_window(flags: int) -> int:
    """Add CREATE_NO_WINDOW unless the caller asked for a new/own console."""
    if not CREATE_NO_WINDOW:
        return flags
    if flags & (_CREATE_NEW_CONSOLE | _DETACHED_PROCESS):
        return flags
    return flags | CREATE_NO_WINDOW


def install_no_window_default() -> None:
    """Make EVERY child process default to no console window (Windows only).

    The packaged app is windowed (no console of its own), so any console child
    — powershell, git, python, adb, node, ruff, pytest, MCP servers — makes
    Windows pop a console window that flashes on screen, even for a moment.

    asyncio's Windows subprocess transport is built on ``subprocess.Popen``, so
    patching ``Popen`` once covers both direct subprocess use and every
    ``create_subprocess_exec/shell`` call, plus any future spawn site. GUI
    children (Chrome, Edge) have no console, so the flag is a no-op for them; a
    caller that explicitly asked for a new/own console is left untouched.
    """
    if os.name != "nt" or CREATE_NO_WINDOW == 0:
        return
    if getattr(subprocess.Popen, "_no_window_patched", False):
        return
    original_init = subprocess.Popen.__init__

    def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["creationflags"] = merge_no_window(kwargs.get("creationflags", 0))
        original_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = patched_init  # type: ignore[method-assign]
    subprocess.Popen._no_window_patched = True  # type: ignore[attr-defined]


@dataclass(slots=True)
class ProcResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def supports_async_subprocess() -> bool:
    """Умеет ли текущий event loop запускать подпроцессы."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    if os.name != "nt":
        return True
    return type(loop).__name__.startswith("Proactor")


async def run_process(
    argv: list[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    timeout: float = 120.0,
    env: dict[str, str] | None = None,
    stdin_data: bytes | None = None,
) -> ProcResult:
    """Запускает процесс и возвращает декодированный вывод.

    Никогда не кидает исключений времени выполнения процесса — ошибки
    приходят в ProcResult.
    """
    cwd = str(cwd) if cwd else None
    proc_env = {**os.environ, **(env or {})}
    # Заставляем дочерний Python печатать в UTF-8, чтобы не ловить кракозябры.
    proc_env.setdefault("PYTHONIOENCODING", "utf-8")

    if supports_async_subprocess():
        return await _run_async(argv, cwd, timeout, proc_env, stdin_data)
    return await asyncio.to_thread(_run_sync, argv, cwd, timeout, proc_env, stdin_data)


async def _run_async(
    argv: list[str],
    cwd: str | None,
    timeout: float,
    env: dict[str, str],
    stdin_data: bytes | None,
) -> ProcResult:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.PIPE if stdin_data else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **no_window_kwargs(),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin_data), timeout=timeout)
    except asyncio.TimeoutError:
        _kill(proc)
        return ProcResult(-1, "", f"Процесс превысил лимит {timeout:g} с и был остановлен.", True)
    except asyncio.CancelledError:
        _kill(proc)
        raise
    return ProcResult(proc.returncode or 0, decode_bytes(out), decode_bytes(err))


def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.kill()
    except (ProcessLookupError, OSError):  # pragma: no cover
        pass


def _run_sync(
    argv: list[str],
    cwd: str | None,
    timeout: float,
    env: dict[str, str],
    stdin_data: bytes | None,
) -> ProcResult:
    try:
        completed = subprocess.run(  # noqa: S603 - команда формируется вызывающим кодом
            argv,
            cwd=cwd,
            env=env,
            input=stdin_data,
            capture_output=True,
            timeout=timeout,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        return ProcResult(
            -1,
            decode_bytes(exc.stdout if isinstance(exc.stdout, bytes) else None),
            f"Процесс превысил лимит {timeout:g} с и был остановлен.",
            True,
        )
    return ProcResult(
        completed.returncode,
        decode_bytes(completed.stdout),
        decode_bytes(completed.stderr),
    )


def powershell_argv(command: str) -> list[str]:
    """Аргументы для неинтерактивного PowerShell."""
    exe = "powershell.exe" if os.name == "nt" else "pwsh"
    return [
        exe,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        command,
    ]


def shell_argv(command: str) -> list[str]:
    """Кроссплатформенный запуск строки команды."""
    if os.name == "nt":
        return powershell_argv(command)
    return ["/bin/sh", "-c", command]


def python_executable(cwd: str | os.PathLike[str] | None = None) -> str:
    """Настоящий Python для дочерних процессов (run_python, ruff, pytest, mypy…).

    В СОБРАННОМ exe ``sys.executable`` — это сам ``LocalAIAgent.exe``, а НЕ
    интерпретатор, поэтому ``[sys.executable, "-m", "pytest"]`` запускал бы
    приложение вместо Python (баг health-gate/run_lint в exe). Приоритет:
    1) venv проекта (в нём стоят его зависимости, ruff, pytest);
    2) обычный запуск из интерпретатора — он и есть нужный Python;
    3) собранный exe без venv — системный python из PATH.
    """
    import shutil
    import sys

    if cwd:
        base = Path(cwd)
        rels = (
            (".venv/Scripts/python.exe", "venv/Scripts/python.exe")
            if os.name == "nt"
            else (".venv/bin/python", "venv/bin/python")
        )
        for rel in rels:
            candidate = base / rel
            if candidate.exists():
                return str(candidate)

    if not getattr(sys, "frozen", False):
        return sys.executable

    for name in ("python", "python3", "py"):
        found = shutil.which(name)
        if found and _runs_python(found):
            return found
    # No Python on the machine. Returning sys.executable here would run the app
    # itself with a script argument (argparse "unrecognized arguments"); a plain
    # name fails with a clear "not found" error that the caller can report.
    return "python"


@functools.lru_cache(maxsize=8)
def _runs_python(path: str) -> bool:
    """False for the Microsoft Store stub in WindowsApps, which only prints
    "Python was not found" (exit 9009). A real Store install lives in the same
    folder, so the only reliable check is to run it once (cached)."""
    if "windowsapps" not in path.lower():
        return True
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, our own probe
            [path, "-c", "import sys"], capture_output=True, timeout=15, **no_window_kwargs()
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def python_argv(script_path: Path, cwd: str | os.PathLike[str] | None = None) -> list[str]:
    return [python_executable(cwd), "-X", "utf8", str(script_path)]
