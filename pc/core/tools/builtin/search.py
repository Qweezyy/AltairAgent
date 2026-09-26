"""Поиск по проекту: файлы по маске и текст внутри файлов.

`grep_search` и `find_files` подтянуты ближе к Grep/Glob из Claude Code:
контекстные строки (-A/-B/-C), режимы вывода (content/files/count), multiline,
рекурсивные маски пути (`src/**/*.ts`) и сортировка по времени. Движок —
чистый Python (работает и в собранном exe), без внешних зависимостей.
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
from pathlib import Path

from pydantic import BaseModel, Field

from core.errors import ToolError
from core.search_backend import can_accelerate, candidate_files
from core.security.paths import resolve_path, safe_relpath
from core.tgrep_server import manager as tgrep_manager
from core.utils.fs import IGNORED_DIRS, human_size
from core.utils.text import looks_binary

from core.tools.base import Tool, ToolContext  # isort: skip

SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".bmp", ".pdf",
    ".exe", ".dll", ".so", ".dylib", ".pyc", ".pyd", ".class",
    ".zip", ".gz", ".tar", ".7z", ".rar", ".mp3", ".mp4", ".avi", ".mkv",
    ".sqlite", ".db", ".bin", ".woff", ".woff2", ".ttf",
}


def _walk(base: Path, max_files: int = 20_000):
    """Обход дерева с пропуском мусорных папок."""
    seen = 0
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
        for name in files:
            yield Path(root) / name
            seen += 1
            if seen >= max_files:
                return


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Компилирует glob (`*`, `?`, `**`) в regex по posix-путю.

    `**` матчит любые сегменты (включая `/`), одиночная `*` — в пределах сегмента.
    Так работают маски вида `src/**/*.ts` и `**/test_*.py`.
    """
    i, n = 0, len(pattern)
    out = ["(?s:"]
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 2] == "**":
                i += 2
                if pattern[i : i + 1] == "/":
                    i += 1
                out.append("(?:.*/)?")  # ноль или больше сегментов
            else:
                i += 1
                out.append("[^/]*")
        elif c == "?":
            i += 1
            out.append("[^/]")
        else:
            i += 1
            out.append(re.escape(c))
    out.append(")\\Z")
    return re.compile("".join(out))


def _is_path_pattern(pattern: str) -> bool:
    return "/" in pattern or "**" in pattern


class FindFilesArgs(BaseModel):
    pattern: str = Field(
        description=(
            "Pattern: a name ('*.py', '*test*') or a recursive path ('src/**/*.ts', '**/conftest.py')"
        )
    )
    path: str = Field(default=".", description="Where to search (default: the whole workspace)")
    sort: str = Field(default="name", description="Sort: 'name' or 'modified' (newest first)")
    max_results: int = Field(default=80, ge=1, le=500)


class FindFilesTool(Tool):
    name = "find_files"
    description = (
        "Finds files recursively by a name or path pattern ('*.py', 'src/**/*.ts'), optionally "
        "newest first. Service folders (.git, node_modules, venv, __pycache__) are skipped."
    )
    Args = FindFilesArgs
    category = "read"
    timeout = 60.0

    async def run(self, args: FindFilesArgs, ctx: ToolContext) -> str:
        base = resolve_path(args.path, settings=ctx.settings, must_exist=True, must_be_dir=True)
        path_mode = _is_path_pattern(args.pattern)
        matcher = _glob_to_regex(args.pattern) if path_mode else None
        name_mask = args.pattern.lower()

        def _find() -> str:
            found: list[tuple[Path, float]] = []
            for file_path in _walk(base):
                if path_mode:
                    rel = file_path.relative_to(base).as_posix()
                    hit = matcher.match(rel) is not None  # type: ignore[union-attr]
                else:
                    hit = fnmatch.fnmatch(file_path.name.lower(), name_mask)
                if not hit:
                    continue
                try:
                    mtime = file_path.stat().st_mtime
                except OSError:
                    mtime = 0.0
                found.append((file_path, mtime))
                if args.sort != "modified" and len(found) >= args.max_results:
                    break

            if args.sort == "modified":
                found.sort(key=lambda p: p[1], reverse=True)
            limited = found[: args.max_results]

            if not limited:
                where = safe_relpath(base, ctx.settings)
                return f"Файлов по маске '{args.pattern}' не найдено в {where}."

            lines = []
            for file_path, _ in limited:
                try:
                    size = human_size(file_path.stat().st_size)
                except OSError:
                    size = "?"
                lines.append(f"{safe_relpath(file_path, ctx.settings)} ({size})")

            head = f"Найдено файлов: {len(found)}"
            if len(found) > args.max_results:
                head += f" (показаны первые {args.max_results})"
            return head + "\n" + "\n".join(lines)

        return await asyncio.to_thread(_find)


class GrepArgs(BaseModel):
    query: str = Field(description="Text or regular expression to find")
    path: str = Field(default=".", description="Folder or file to search")
    is_regex: bool = Field(default=False, description="Treat query as a regex")
    case_sensitive: bool = Field(default=False, description="Case-sensitive match")
    glob: str = Field(default="*", description="File filter: '*.py' or a path pattern '**/*.ts'")
    output_mode: str = Field(
        default="content",
        description="'content': matching lines; 'files': paths only (cheapest); 'count': matches per file",
    )
    context: int = Field(default=0, ge=0, le=20, description="Context lines before and after (-C)")
    before: int = Field(default=0, ge=0, le=20, description="Context lines before (-B)")
    after: int = Field(default=0, ge=0, le=20, description="Context lines after (-A)")
    multiline: bool = Field(default=False, description="Match across lines")
    max_results: int = Field(default=60, ge=1, le=300)


class GrepSearchTool(Tool):
    name = "grep_search"
    description = (
        "Searches file contents for text or a regex; returns path, line number and line. Supports "
        "context lines, output modes (content/files/count), multiline and path filters. The fastest "
        "way to find where something is defined or used; 'files' mode first keeps big searches cheap."
    )
    Args = GrepArgs
    category = "read"
    timeout = 90.0

    async def run(self, args: GrepArgs, ctx: ToolContext) -> str:
        base = resolve_path(args.path, settings=ctx.settings, must_exist=True)
        flags = 0 if args.case_sensitive else re.IGNORECASE
        if args.multiline:
            flags |= re.MULTILINE | re.DOTALL
        try:
            pattern = re.compile(args.query if args.is_regex else re.escape(args.query), flags)
        except re.error as exc:
            raise ToolError(f"Некорректное регулярное выражение: {exc}") from exc

        before = args.context or args.before
        after = args.context or args.after
        mode = args.output_mode if args.output_mode in ("content", "files", "count") else "content"
        path_glob = args.glob
        glob_is_path = _is_path_pattern(path_glob)
        glob_matcher = _glob_to_regex(path_glob) if glob_is_path else None
        name_mask = path_glob.lower()

        def _file_matches_glob(file_path: Path) -> bool:
            if path_glob in ("*", ""):
                return True
            if glob_is_path:
                try:
                    rel = file_path.relative_to(base if base.is_dir() else base.parent).as_posix()
                except ValueError:
                    rel = file_path.name
                return glob_matcher.match(rel) is not None  # type: ignore[union-attr]
            return fnmatch.fnmatch(file_path.name.lower(), name_mask)

        def _read_lines(file_path: Path) -> list[str] | None:
            if file_path.suffix.lower() in SKIP_SUFFIXES or looks_binary(file_path):
                return None
            try:
                with open(file_path, encoding="utf-8", errors="ignore") as fh:
                    return fh.readlines()
            except OSError:
                return None

        def _scan_content(file_path: Path, out: list[str]) -> bool:
            """content-режим: собирает совпавшие строки с контекстом. False = лимит."""
            lines = _read_lines(file_path)
            if lines is None:
                return True
            rel = safe_relpath(file_path, ctx.settings)

            if args.multiline:
                text = "".join(lines)
                # Номера строк по смещению начала совпадения.
                starts = [m.start() for m in pattern.finditer(text)]
                for off in starts:
                    line_no = text.count("\n", 0, off) + 1
                    snippet = lines[line_no - 1].strip()[:300] if line_no - 1 < len(lines) else ""
                    out.append(f"{rel}:{line_no}: {snippet}")
                    if len(out) >= args.max_results:
                        return False
                return True

            hit_rows = [i for i, line in enumerate(lines) if pattern.search(line)]
            if not hit_rows:
                return True
            printed: set[int] = set()
            prev_end = -1
            for row in hit_rows:
                lo = max(0, row - before)
                hi = min(len(lines) - 1, row + after)
                if (before or after) and printed and lo > prev_end + 1:
                    out.append("--")
                for j in range(lo, hi + 1):
                    if j in printed:
                        continue
                    printed.add(j)
                    sep = ":" if j == row else "-"
                    out.append(f"{rel}:{j + 1}{sep} {lines[j].rstrip()[:300]}")
                    if len(out) >= args.max_results:
                        return False
                prev_end = hi
            return True

        def _count_file(file_path: Path) -> int:
            lines = _read_lines(file_path)
            if lines is None:
                return 0
            if args.multiline:
                return len(pattern.findall("".join(lines)))
            return sum(1 for line in lines if pattern.search(line))

        def _iter_files(allow: set[str] | None):
            if base.is_file():
                yield base
                return
            for file_path in _walk(base):
                # allow — надмножество файлов с совпадением от внешнего grep
                # (tgrep/ripgrep). Пропускаем чтение файлов заведомо без совпадений,
                # сохраняя порядок обхода и точный формат вывода.
                if allow is not None and os.path.normcase(str(file_path)) not in allow:
                    continue
                if _file_matches_glob(file_path):
                    yield file_path

        def _grep(allow: set[str] | None) -> str:
            if mode == "files":
                hits: list[str] = []
                for file_path in _iter_files(allow):
                    if _count_file(file_path) > 0:
                        hits.append(safe_relpath(file_path, ctx.settings))
                        if len(hits) >= args.max_results:
                            break
                if not hits:
                    return f"Файлов с совпадением '{args.query}' не найдено."
                return f"Файлов с совпадениями: {len(hits)}\n" + "\n".join(hits)

            if mode == "count":
                rows: list[tuple[str, int]] = []
                total = 0
                for file_path in _iter_files(allow):
                    c = _count_file(file_path)
                    if c:
                        rows.append((safe_relpath(file_path, ctx.settings), c))
                        total += c
                        if len(rows) >= args.max_results:
                            break
                if not rows:
                    return f"Совпадений с '{args.query}' не найдено."
                body = "\n".join(f"{path}: {c}" for path, c in rows)
                return f"Совпадений всего: {total} в {len(rows)} файлах\n{body}"

            matches: list[str] = []
            for file_path in _iter_files(allow):
                if not _scan_content(file_path, matches):
                    break
            if not matches:
                return f"Совпадений с '{args.query}' не найдено."
            head = f"Совпадений: {len([m for m in matches if m != '--'])}"
            if len(matches) >= args.max_results:
                head += " (лимит достигнут — уточни запрос)"
            return head + "\n" + "\n".join(matches)

        # Быстрый путь: если есть внешний grep (tgrep/ripgrep) и запрос —
        # простой литерал, заранее сузим набор файлов, чтобы не читать те, где
        # совпадения точно нет. Вывод от этого не меняется — только скорость.
        allow_set: set[str] | None = None
        if base.is_dir() and can_accelerate(args.query, is_regex=args.is_regex, multiline=args.multiline):
            serve_index: str | None = None
            # Опция для огромных репо: тёплый индекс-сервер tgrep (см. settings).
            if getattr(ctx.settings, "tgrep_serve", False):
                tgrep_manager.configure(
                    index_root=ctx.settings.app_path,
                    min_files=getattr(ctx.settings, "tgrep_serve_min_files", 20_000),
                )
                try:
                    serve_index = await tgrep_manager.ready_index_path(str(base))
                except Exception:  # noqa: BLE001 — сервер best-effort, не должен ронять поиск
                    serve_index = None
            allow_set = await candidate_files(
                args.query, str(base), case_sensitive=args.case_sensitive, serve_index_path=serve_index,
            )

        return await asyncio.to_thread(_grep, allow_set)
