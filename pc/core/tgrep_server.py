"""Персистентный индекс-сервер tgrep для огромных репозиториев (опция).

По умолчанию `grep_search` ищет прямым сканом (`tgrep --no-index` / ripgrep) — это
быстро и всегда свежо. Но на кодовых базах в сотни тысяч файлов трёхграммный индекс
tgrep даёт ещё порядок скорости. Этот модуль по требованию (`settings.tgrep_serve`)
поднимает `tgrep serve` на корень рабочей папки: сервер держит индекс в памяти и
следит за файлами (watcher), поэтому клиентские вызовы `tgrep … --index-path <тот же>`
авто-подключаются к нему и отвечают почти мгновенно, а правки подхватываются вотчером.

Осторожно и best-effort:
* включается только если файлов в папке не меньше порога (иначе прямой скан и так мгновенен);
* сервер поднимается в фоне, а пока он не готов — поиск идёт прямым сканом (корректно);
* индекс хранится в app_path/tgrep-index/<hash>, НЕ в проекте пользователя;
* все поднятые серверы гасятся при выходе (atexit) и через stop_all().

Если что-то не так (tgrep нет, сервер не поднялся, таймаут) — просто продолжаем
работать прямым сканом. Никаких жёстких зависимостей от демона.
"""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from core.search_backend import _find_exe

#: Флаги запуска дочернего процесса без мелькающего окна консоли на Windows.
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW

#: Сколько ждать готовности сервера (сек) и с каким шагом опрашивать статус.
_READY_TIMEOUT = 90.0
_POLL_STEP = 1.5


@dataclass
class _Server:
    root: str
    index_path: str
    proc: subprocess.Popen | None = None
    ready: bool = False
    #: "starting" | "ready" | "skip" (мало файлов) | "failed"
    state: str = "starting"
    task: asyncio.Task | None = field(default=None, repr=False)


class TgrepServerManager:
    """Управляет персистентными tgrep-серверами по корням рабочих папок."""

    def __init__(self) -> None:
        self._servers: dict[str, _Server] = {}
        self._index_root: Path | None = None
        self._min_files: int = 20_000
        self._lock = threading.Lock()
        atexit.register(self.stop_all)

    def configure(self, *, index_root: Path, min_files: int) -> None:
        self._index_root = index_root
        self._min_files = max(1, int(min_files))

    def _tgrep(self) -> str | None:
        return _find_exe("tgrep")

    def _index_path_for(self, root: str) -> str:
        digest = hashlib.sha1(os.path.normcase(root).encode("utf-8", "ignore")).hexdigest()[:16]
        base = self._index_root or (Path.home() / ".localaiagent")
        return str(base / "tgrep-index" / digest)

    async def ready_index_path(self, root: str) -> str | None:
        """Путь индекса, если сервер для root уже готов; иначе None (ищем прямым сканом).

        Первый вызов на новый корень запускает фоновую подготовку и возвращает None.
        """
        exe = self._tgrep()
        if exe is None or self._index_root is None:
            return None
        key = os.path.normcase(os.path.abspath(root))  # noqa: ASYNC240 — pure string op, no I/O
        with self._lock:
            srv = self._servers.get(key)
            if srv is None:
                srv = _Server(root=key, index_path=self._index_path_for(key))
                self._servers[key] = srv
                srv.task = asyncio.ensure_future(self._bootstrap(exe, srv))
        return srv.index_path if srv.ready else None

    async def _count_files(self, exe: str, root: str) -> int:
        """Быстрый подсчёт текстовых файлов через `tgrep count-files`."""
        try:
            proc = await asyncio.create_subprocess_exec(
                exe, "count-files", root,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), 60.0)
        except (asyncio.TimeoutError, OSError, ValueError):
            return -1
        digits = "".join(ch for ch in out.decode("utf-8", "ignore") if ch.isdigit())
        return int(digits) if digits else -1

    async def _status_ok(self, exe: str, srv: _Server) -> bool:
        """Сервер поднят и ответил (в выводе status есть PID/Port)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                exe, "status", srv.root, "--index-path", srv.index_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), 15.0)
        except (asyncio.TimeoutError, OSError, ValueError):
            return False
        text = out.decode("utf-8", "ignore")
        return proc.returncode == 0 and ("PID:" in text or "Port:" in text)

    async def _bootstrap(self, exe: str, srv: _Server) -> None:
        """Фон: проверить размер, поднять сервер, дождаться готовности."""
        count = await self._count_files(exe, srv.root)
        if 0 <= count < self._min_files:
            srv.state = "skip"
            return
        def spawn() -> subprocess.Popen[bytes]:
            # A long-lived daemon we only poll, so a plain Popen fits better than an asyncio
            # subprocess tied to this loop; run it off-loop (AGENTS.md rule 5).
            Path(srv.index_path).mkdir(parents=True, exist_ok=True)
            return subprocess.Popen(
                [exe, "serve", srv.root, "--index-path", srv.index_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW,
                close_fds=True,
            )

        try:
            srv.proc = await asyncio.to_thread(spawn)
        except OSError:
            srv.state = "failed"
            return
        # Ждём, пока сервер завершит первичную индексацию и начнёт отвечать.
        waited = 0.0
        while waited < _READY_TIMEOUT:
            if srv.proc.poll() is not None:  # процесс умер
                srv.state = "failed"
                return
            if await self._status_ok(exe, srv):
                srv.ready = True
                srv.state = "ready"
                return
            await asyncio.sleep(_POLL_STEP)
            waited += _POLL_STEP
        srv.state = "failed"  # не дождались — останемся на прямом скане

    def stop_all(self) -> None:
        """Погасить все поднятые серверы (идемпотентно)."""
        with self._lock:
            servers = list(self._servers.values())
            self._servers.clear()
        for srv in servers:
            if srv.task is not None and not srv.task.done():
                srv.task.cancel()
            proc = srv.proc
            if proc is None or proc.poll() is not None:
                continue
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except OSError:
                pass


#: Единственный менеджер на процесс.
manager = TgrepServerManager()
