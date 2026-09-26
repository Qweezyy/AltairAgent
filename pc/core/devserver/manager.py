"""Менеджер долгоживущих dev-серверов.

Отличие от `execute_command`: тот запускает команду и ждёт завершения. Dev-сервер
(`npm run dev`, `uvicorn --reload`, `cargo watch`) не завершается сам — он висит и
поток за потоком печатает логи. Агенту нужно уметь: запустить сервер в фоне,
почитать НОВЫЙ вывод с прошлого раза, поймать в нём ошибку сборки, починить файл
и снова почитать логи, убедившись, что пересборка прошла.

Вывод (stdout+stderr слиты, как их видит человек в терминале) читается фоновым
потоком в кольцевой буфер строк. Курсор чтения у каждого сервера свой: инструмент
`read_dev_server` отдаёт только то, что появилось после предыдущего чтения.

Менеджер — процессо-глобальный синглтон: dev-серверы и так общий ресурс машины,
а держать их в контексте одного запуска нельзя — процесс должен пережить много
вызовов инструментов и весь диалог.
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

#: Сколько последних строк лога держим на сервер. Больше не нужно: агент читает
#: инкрементально, а на разбор ошибки хватает и хвоста.
MAX_LINES = 2000

#: Предел одновременно живущих серверов — страховка от утечки процессов, если
#: агент забудет остановить старые.
MAX_SERVERS = 8

#: На сколько снимков-строк за раз отдаём при чтении без ожидания.
_NAME_MAX = 40


class DevServerError(RuntimeError):
    """Ошибка управления dev-сервером (имя занято, лимит, не найден)."""


@dataclass
class _LogLine:
    ts: float
    text: str


@dataclass
class DevServerProcess:
    """Один запущенный dev-сервер и его лог."""

    name: str
    command: str
    cwd: str
    proc: subprocess.Popen
    started_at: float = field(default_factory=time.time)
    _lines: deque[_LogLine] = field(default_factory=lambda: deque(maxlen=MAX_LINES))
    _lock: threading.Lock = field(default_factory=threading.Lock)
    #: Сколько строк уже отдано читателю (глобальный счётчик, не длина deque).
    _emitted_total: int = 0
    _read_cursor: int = 0
    _reader: threading.Thread | None = None

    def start_reader(self) -> None:
        self._reader = threading.Thread(
            target=self._pump, name=f"devserver-{self.name}", daemon=True
        )
        self._reader.start()

    def _pump(self) -> None:
        """Читает объединённый stdout/stderr построчно в кольцевой буфер."""
        stream = self.proc.stdout
        if stream is None:
            return
        for raw in iter(stream.readline, b""):
            text = raw.decode("utf-8", "replace").rstrip("\r\n")
            with self._lock:
                self._lines.append(_LogLine(time.time(), text))
                self._emitted_total += 1
        # Поток закрылся — процесс завершился.

    # ------------------------------------------------------------------ чтение

    def read_new(self) -> list[str]:
        """Строки, появившиеся с прошлого чтения. Двигает курсор."""
        with self._lock:
            behind = self._emitted_total - self._read_cursor
            if behind <= 0:
                return []
            # Если отстали больше, чем помещается в буфер, часть строк потеряна —
            # отдаём то, что есть, честно пометив пропуск.
            available = min(behind, len(self._lines))
            lines = [ln.text for ln in list(self._lines)[-available:]]
            lost = behind - available
            self._read_cursor = self._emitted_total
            if lost > 0:
                lines.insert(0, f"[...пропущено {lost} строк лога...]")
            return lines

    def tail(self, count: int) -> list[str]:
        """Последние `count` строк без сдвига курсора."""
        with self._lock:
            return [ln.text for ln in list(self._lines)[-count:]]

    def line_count(self) -> int:
        with self._lock:
            return self._emitted_total

    # ------------------------------------------------------------------ статус

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
        """Останавливает процесс мягко, затем жёстко.

        На Windows dev-сервер обычно это `powershell → npm → node …`, и обычный
        terminate() гасит только оболочку, оставляя node висеть на порту. Поэтому
        на Windows убиваем всё дерево через `taskkill /T`.
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
                logger.warning("taskkill для %s не сработал: %s", self.name, exc)
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
            logger.warning("Не удалось остановить dev-сервер %s: %s", self.name, exc)


class DevServerManager:
    """Реестр запущенных dev-серверов процесса."""

    def __init__(self) -> None:
        self._servers: dict[str, DevServerProcess] = {}
        self._lock = threading.Lock()

    def start(
        self, name: str, command: str, cwd: Path | str, *, env: dict[str, str] | None = None
    ) -> DevServerProcess:
        name = (name or "").strip()
        if not name or len(name) > _NAME_MAX:
            raise DevServerError("Имя сервера должно быть 1–40 символов.")
        with self._lock:
            existing = self._servers.get(name)
            if existing and existing.is_running():
                raise DevServerError(
                    f"Сервер «{name}» уже запущен. Остановите его или выберите другое имя."
                )
            # Освобождаем имя от завершившегося процесса.
            if existing:
                del self._servers[name]
            running = [s for s in self._servers.values() if s.is_running()]
            if len(running) >= MAX_SERVERS:
                raise DevServerError(
                    f"Достигнут предел в {MAX_SERVERS} серверов. Остановите ненужные."
                )

            proc_env = {**os.environ, **(env or {})} if env else None
            proc = subprocess.Popen(  # noqa: S603 - команда уже прошла проверки инструмента
                shell_argv(command),
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # сливаем в один поток, как в терминале
                stdin=subprocess.DEVNULL,  # dev-сервер не должен ждать ввода
                bufsize=0,
                env=proc_env,
            )
            server = DevServerProcess(name=name, command=command, cwd=str(cwd), proc=proc)
            server.start_reader()
            self._servers[name] = server
            logger.info("Запущен dev-сервер «%s»: %s (pid %s)", name, command, proc.pid)
            return server

    def get(self, name: str) -> DevServerProcess:
        with self._lock:
            server = self._servers.get(name)
        if server is None:
            raise DevServerError(f"Сервер «{name}» не найден. Список: {self._names() or '—'}")
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
        """Останавливает все серверы. Зовётся при выключении приложения."""
        for server in self.all():
            server.stop()
        with self._lock:
            self._servers.clear()


_manager: DevServerManager | None = None
_manager_lock = threading.Lock()


def get_manager() -> DevServerManager:
    """Возвращает процессо-глобальный менеджер (создаёт при первом обращении)."""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = DevServerManager()
    return _manager
