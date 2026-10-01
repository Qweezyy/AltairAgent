"""Быстрый внешний движок поиска-кандидатов для grep_search (tgrep / ripgrep).

Идея. Самый дорогой шаг текстового поиска на большом репозитории — прочитать и
прогнать регулярку по каждому файлу. Если в системе есть внешний быстрый grep, мы
отдаём ему только предварительную фильтрацию: он возвращает СПИСОК файлов, где
литерал встречается (флаг ``-l``), а точным разбором, контекстом и форматированием
по-прежнему занимается наш Python-движок. Так вывод остаётся байт-в-байт прежним, а
скан ускоряется за счёт пропуска файлов без совпадений.

Приоритет движков:

* **tgrep** (Microsoft, Rust, MIT) — трёхграммный индекс + демон, до ~50× быстрее
  ripgrep на больших репозиториях; https://github.com/microsoft/tgrep
* **ripgrep** (``rg``) — широко распространён, единый бинарник, тоже очень быстр.

Жёсткой зависимости нет: если ни один бинарник не найден или внешний вызов дал
сбой/таймаут, ``grep_search`` работает как раньше на чистом Python. Список
кандидатов всегда строится как НАДмножество (флаги ``--no-ignore``/``--hidden``,
отключающие пропуск по .gitignore и скрытых файлов), чтобы фильтр не «потерял» ни
одного файла, который наш обычный обход прочитал бы сам.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

#: Порядок предпочтения движков.
_ORDER = ("tgrep", "rg")

#: Кэш обнаружения: None — ещё не искали.
_detected: list[tuple[str, str]] | None = None


def _env_choice() -> str:
    return (os.environ.get("AGENT_GREP_BACKEND") or "auto").strip().lower()


def _bundled_dir() -> Path | None:
    """Папка с забандленными бинарниками (vendor/bin) — рядом с exe или в исходниках.

    Мы поставляем tgrep/ripgrep ВМЕСТЕ с приложением, чтобы быстрый поиск работал
    без установки чего-либо пользователем.
    """
    cands: list[Path] = []
    if getattr(sys, "frozen", False):
        cands.append(Path(sys.executable).parent / "vendor" / "bin")
        mp = getattr(sys, "_MEIPASS", None)
        if mp:
            cands.append(Path(mp) / "vendor" / "bin")
    cands.append(Path(__file__).resolve().parent.parent / "vendor" / "bin")
    for d in cands:
        if d.is_dir():
            return d
    return None


def _find_exe(name: str) -> str | None:
    """Ищем бинарник: сначала забандленный (vendor/bin), затем в PATH."""
    d = _bundled_dir()
    if d is not None:
        # The bundled .exe files are Windows builds: elsewhere only a native binary will do
        # (under WSL an .exe even starts, gets Linux paths and silently finds nothing).
        for fn in (f"{name}.exe", name) if os.name == "nt" else (name,):
            p = d / fn
            if p.is_file() and (os.name == "nt" or os.access(p, os.X_OK)):
                return str(p)
    return shutil.which(name)


def available_backends() -> list[tuple[str, str]]:
    """Список доступных движков как ``[(имя, путь), …]`` в порядке приоритета.

    Приоритет пути: забандленный vendor/bin → системный PATH. Управляется
    переменной ``AGENT_GREP_BACKEND``: ``auto`` (по умолчанию) — автоопределение;
    ``off``/``python``/``none`` — отключить внешний поиск; ``tgrep``/``rg`` —
    принудительно только этот движок.
    """
    global _detected
    choice = _env_choice()
    if choice in ("off", "python", "none"):
        return []
    if choice in _ORDER:  # принудительный выбор не кэшируем — дёшево
        exe = _find_exe(choice)
        return [(choice, exe)] if exe else []
    if _detected is None:
        found: list[tuple[str, str]] = []
        for name in _ORDER:
            exe = _find_exe(name)
            if exe:
                found.append((name, exe))
        _detected = found
    return list(_detected)


def reset_cache() -> None:
    """Сбросить кэш обнаружения (для тестов)."""
    global _detected
    _detected = None


async def _run_candidate(name: str, exe: str, query: str, base: str, *, case_sensitive: bool,
                         timeout: float, serve_index_path: str | None = None) -> set[str] | None:
    """Один вызов внешнего grep за списком файлов-кандидатов. None — откат."""
    args = [exe, "-l", "-F", "--no-ignore", "--hidden"]
    # tgrep: по умолчанию --no-index — прямой скан, всегда свежо и без папки
    # .tgrep в проекте. Но если поднят персистентный index-сервер (опция для
    # огромных репо), клиент подключается к нему по общему --index-path: индекс
    # тёплый, а свежесть держит вотчер сервера. ripgrep индекса не имеет.
    if name == "tgrep":
        if serve_index_path:
            args += ["--index-path", serve_index_path]
        else:
            args.append("--no-index")
    if not case_sensitive:
        args.append("-i")
    args += ["-e", query, base]
    # На Windows create_subprocess_exec не запускает .bat/.cmd напрямую (такие
    # обёртки бывают у npm/scoop-установок) — оборачиваем в cmd /c.
    if os.name == "nt" and exe.lower().endswith((".bat", ".cmd")):
        args = ["cmd", "/c", *args]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _err = await asyncio.wait_for(proc.communicate(), timeout)
    except (asyncio.TimeoutError, OSError, ValueError, NotImplementedError):
        if proc is not None:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        return None
    rc = proc.returncode
    # Коды как у ripgrep: 0 — есть совпадения, 1 — нет, 2 — ошибка (неизвестный
    # флаг и т.п.). На ошибке откатываемся к следующему движку / Python.
    if rc == 1:
        return set()
    if rc != 0:
        return None
    files: set[str] = set()
    for line in out.decode("utf-8", "ignore").splitlines():
        line = line.strip()
        if line:
            files.add(os.path.normcase(line))
    return files


async def candidate_files(query: str, base: str, *, case_sensitive: bool,
                          timeout: float = 20.0, serve_index_path: str | None = None) -> set[str] | None:
    """Множество путей (normcase), где встречается литерал ``query``.

    Перебирает установленные движки по приоритету, пока один не отработает.
    ``serve_index_path`` (если задан) — путь тёплого индекса персистентного
    tgrep-сервера; используется только движком tgrep. Возвращает ``None``, если
    внешний поиск недоступен/сбоит — тогда вызывающий код ищет полным обходом.
    """
    backends = available_backends()
    if not backends:
        return None
    for name, exe in backends:
        idx = serve_index_path if name == "tgrep" else None
        result = await _run_candidate(name, exe, query, base, case_sensitive=case_sensitive,
                                      timeout=timeout, serve_index_path=idx)
        if result is not None:
            return result
    return None


def can_accelerate(query: str, *, is_regex: bool, multiline: bool) -> bool:
    """Годится ли запрос для внешнего ускорения.

    Ускоряем только простой случай, где предфильтр гарантированно НАДмножество:
    литерал (не regex), однострочный, непустой и ASCII (чтобы не разойтись с
    внешним движком на кодировках). Остальное идёт обычным Python-путём.
    """
    if is_regex or multiline:
        return False
    q = query.strip()
    return bool(q) and q.isascii()
