"""PTY-сессия: настоящий интерактивный shell за псевдотерминалом.

В отличие от инструмента `run_command` (один запуск — один результат), здесь
живой процесс: пользователь печатает, сразу видит вывод, работают интерактивные
программы (ping, python REPL, far, vim). Это ConPTY через pywinpty на Windows и
обычный pty на POSIX.

ВАЖНО про потоки. На Windows ConPTY перестаёт отдавать вывод/принимать ввод, если
чтение блокирует в одном потоке, а запись идёт из другого, — а именно так
получается, когда сервер живёт в отдельном (не главном) потоке, как в десктопном
приложении. Симптом: shell стартует, печатает первые байты и «замолкает».

Поэтому здесь ОДИН поток-хозяин делает всё: spawn, чтение и запись. Чтение не
блокирующее — ждём готовности сокета PTY через `select` с коротким таймаутом, а в
паузах разбираем очередь ввода. Так единственный поток спокойно чередует ввод и
вывод, и ConPTY работает независимо от того, где запущен сервер.
"""

from __future__ import annotations

import asyncio
import os
import queue
import select
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger("terminal")

#: Типы элементов внутренней очереди ввода.
_DATA = "data"
_RESIZE = "resize"

#: Таймаут ожидания готовности PTY в секундах. Короткий — чтобы ввод, пришедший
#: между чтениями, применялся без заметной задержки.
_POLL_TIMEOUT = 0.05


class TerminalUnavailable(RuntimeError):
    """PTY-движок недоступен (не установлен pywinpty или нет shell)."""


def default_shell() -> list[str]:
    """Команда запуска оболочки: PowerShell на Windows, $SHELL/bash на POSIX."""
    if os.name == "nt":
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if pwsh:
            return [pwsh, "-NoLogo"]
        return [os.environ.get("COMSPEC", "cmd.exe")]
    shell = os.environ.get("SHELL") or shutil.which("bash") or "/bin/sh"
    return [shell]


def windows_cmdline(command: list[str]) -> str:
    """Join argv into a single WinPTY command line with CreateProcess quoting.

    WinPTY spawn takes one command line, not an argv list. A plain " ".join
    breaks any path with spaces (e.g. C:\\Program Files\\PowerShell\\7\\pwsh.exe):
    WinPTY would try to run "C:\\Program" with the rest as arguments. list2cmdline
    applies the exact rules CreateProcess uses to split the line back into argv.
    """
    return subprocess.list2cmdline(command)


class TerminalSession:
    """Один PTY-процесс. Всё общение с winpty — из единственного потока-хозяина.

    `on_output` вызывается уже в event loop (через `call_soon_threadsafe`),
    поэтому из него можно слать данные в веб-сокет.
    """

    def __init__(
        self,
        *,
        cwd: str | Path | None = None,
        cols: int = 80,
        rows: int = 24,
        command: list[str] | None = None,
    ) -> None:
        self._cwd = str(cwd) if cwd else None
        self._cols = max(1, cols)
        self._rows = max(1, rows)
        self._command = command or default_shell()
        self._proc = None  # winpty.PtyProcess | ptyprocess.PtyProcess
        self._owner: threading.Thread | None = None
        self._inbox: queue.Queue[tuple[str, object]] = queue.Queue()
        self._closed = threading.Event()
        self._spawn_error: Exception | None = None
        self._spawned = threading.Event()

    # ------------------------------------------------------------------ запуск

    def start(self, loop: asyncio.AbstractEventLoop, on_output: Callable[[str], None]) -> None:
        """Запускает поток-хозяин и ждёт результат spawn (успех или ошибку)."""
        self._owner = threading.Thread(
            target=self._run, args=(loop, on_output), name="pty-owner", daemon=True
        )
        self._owner.start()
        self._spawned.wait(timeout=15)
        if self._spawn_error is not None:
            raise self._spawn_error

    def _spawn(self):
        cwd = self._cwd if self._cwd and Path(self._cwd).is_dir() else None
        if os.name == "nt":
            try:
                from winpty import Backend, PtyProcess
            except ImportError as exc:  # pragma: no cover - зависит от окружения
                raise TerminalUnavailable(
                    "Не установлен pywinpty — интерактивный терминал недоступен. "
                    "Поставьте: pip install pywinpty"
                ) from exc
            # ВАЖНО: backend WinPTY, а НЕ ConPTY (по умолчанию). ConPTY внутри
            # использует IOCP и конфликтует с IOCP asyncio ProactorEventLoop,
            # когда сервер работает в отдельном потоке (как в десктопном
            # приложении): псевдотерминал стартует, но перестаёт принимать ввод.
            # WinPTY работает через winpty-agent.exe и именованные каналы — без
            # IOCP — и потому от этого конфликта свободен.
            #
            # WinPTY spawn takes a single command line, so join argv with the
            # Windows CreateProcess quoting rules. A plain " ".join breaks any
            # path with spaces (e.g. C:\Program Files\PowerShell\7\pwsh.exe) —
            # WinPTY would try to run "C:\Program" with the rest as arguments.
            cmdline = windows_cmdline(self._command)
            return PtyProcess.spawn(
                cmdline, cwd=cwd, dimensions=(self._rows, self._cols), backend=Backend.WinPTY
            )
        try:
            from ptyprocess import PtyProcess
        except ImportError as exc:  # pragma: no cover - POSIX-ветка
            raise TerminalUnavailable(
                "Не установлен ptyprocess — интерактивный терминал недоступен."
            ) from exc
        return PtyProcess.spawn(self._command, cwd=cwd, dimensions=(self._rows, self._cols))

    # ------------------------------------------------------------- цикл-хозяин

    def _run(self, loop: asyncio.AbstractEventLoop, on_output: Callable[[str], None]) -> None:
        """Единственный поток: spawn, затем чередование чтения и записи."""
        try:
            self._proc = self._spawn()
        except Exception as exc:  # noqa: BLE001 - пробросим в start()
            self._spawn_error = exc
            self._spawned.set()
            return
        self._spawned.set()

        fileobj = getattr(self._proc, "fileobj", None)
        while not self._closed.is_set():
            self._drain_input()
            if not self._read_ready(fileobj):
                continue
            try:
                data = self._proc.read()
            except EOFError:
                break
            except OSError:
                break
            if data:
                try:
                    loop.call_soon_threadsafe(on_output, data)
                except RuntimeError:
                    break
        # Сообщаем наверх о завершении процесса пустой строкой-флагом.
        try:
            loop.call_soon_threadsafe(on_output, "")
        except RuntimeError:
            pass

    def _read_ready(self, fileobj) -> bool:
        """Готов ли PTY отдать данные. Через сокет — `select`; иначе — просто да.

        winpty читает из сокета (`fileobj`), поэтому его можно опрашивать через
        `select` без блокировки. Если сокета нет (иной бэкенд), возвращаем True —
        тогда `read()` заблокирует до данных, но ввод всё равно разберётся перед
        следующим чтением.
        """
        if fileobj is None:
            return True
        try:
            readable, _, _ = select.select([fileobj], [], [], _POLL_TIMEOUT)
        except (OSError, ValueError):
            return True
        return bool(readable)

    def _drain_input(self) -> None:
        """Применяет накопившийся ввод и запросы на смену размера."""
        while True:
            try:
                kind, payload = self._inbox.get_nowait()
            except queue.Empty:
                return
            if self._proc is None:
                continue
            try:
                if kind == _DATA:
                    self._proc.write(str(payload))
                elif kind == _RESIZE:
                    cols, rows = payload  # type: ignore[misc]
                    self._proc.setwinsize(rows, cols)
            except (OSError, EOFError, ValueError):
                pass
            except Exception:  # noqa: BLE001 - разные бэкенды кидают своё
                pass

    # ------------------------------------------------------------------ ввод

    def write(self, data: str) -> None:
        """Кладёт нажатия пользователя в очередь; применяет их поток-хозяин."""
        if not self._closed.is_set():
            self._inbox.put((_DATA, data))

    def resize(self, cols: int, rows: int) -> None:
        """Меняет размер окна PTY. Нулевой размер роняет ConPTY — клампуем до 1."""
        self._cols, self._rows = max(1, cols), max(1, rows)
        if not self._closed.is_set():
            self._inbox.put((_RESIZE, (self._cols, self._rows)))

    # ------------------------------------------------------------------ статус

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.isalive()

    def close(self) -> None:
        """Останавливает процесс и поток-хозяин. Идемпотентно."""
        self._closed.set()
        proc = self._proc
        if proc is not None:
            try:
                if proc.isalive():
                    proc.terminate(force=True)
            except (OSError, EOFError):
                pass
        self._proc = None
