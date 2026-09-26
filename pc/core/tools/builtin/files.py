"""Инструменты работы с файловой системой: листинг, чтение, запись, правка."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, Field

from core.errors import ToolError
from core.events import ArtifactCreated
from core.i18n import tr
from core.patch import find_unique_block, split_lines
from core.security.paths import resolve_path, safe_relpath
from core.tools.base import Tool, ToolContext
from core.tools.checkpointing import snapshot_before_change
from core.utils.fs import human_size
from core.utils.text import looks_binary, read_text_file

ARTIFACT_KINDS = {
    "code": {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".cs", ".rb",
             ".php", ".sh", ".sql", ".css", ".yml", ".yaml", ".toml", ".ini"},
    "markdown": {".md", ".markdown", ".rst"},
    "html": {".html", ".htm", ".svg"},
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp"},
    "data": {".json", ".csv", ".tsv", ".xlsx", ".xml", ".parquet"},
}


def _detect_kind(path: Path) -> str:
    """Тип артефакта для интерфейса (по расширению файла)."""
    suffix = path.suffix.lower()
    for kind, suffixes in ARTIFACT_KINDS.items():
        if suffix in suffixes:
            return kind
    return "file"


# ---------------------------------------------------------------- list


class ListDirectoryArgs(BaseModel):
    path: str = Field(default=".", description="Folder path relative to the workspace (default: its root)")
    show_hidden: bool = Field(default=False, description="Include hidden files (starting with a dot)")
    max_entries: int = Field(default=200, ge=1, le=1000, description="Maximum entries returned")


class ListDirectoryTool(Tool):
    name = "list_directory"
    description = "Lists files and folders in a directory with their sizes."
    Args = ListDirectoryArgs
    category = "read"
    timeout = 30.0

    async def run(self, args: ListDirectoryArgs, ctx: ToolContext) -> str:
        path = resolve_path(args.path, settings=ctx.settings, must_exist=True, must_be_dir=True)

        def _scan() -> str:
            entries = []
            try:
                for entry in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                    if not args.show_hidden and entry.name.startswith("."):
                        continue
                    entries.append(entry)
            except OSError as exc:
                raise ToolError(f"Не удалось прочитать '{args.path}': {exc}") from exc

            lines = [f"Содержимое {safe_relpath(path, ctx.settings)} ({len(entries)} элементов):"]
            shown = 0
            for entry in entries:
                if shown >= args.max_entries:
                    lines.append(f"  ... [ещё {len(entries) - shown} скрыто, используй max_entries]")
                    break
                if entry.is_dir():
                    lines.append(f"  [DIR]  {entry.name}/")
                else:
                    try:
                        size = human_size(entry.stat().st_size)
                    except OSError:
                        size = "n/a"
                    lines.append(f"  [FILE] {entry.name} ({size})")
                shown += 1
            if shown == 0:
                lines.append("  (пусто)")
            return "\n".join(lines)

        return await asyncio.to_thread(_scan)


# ---------------------------------------------------------------- read


class ReadFileArgs(BaseModel):
    path: str = Field(description="File path")
    start_line: int = Field(default=1, ge=1, description="First line to read (1 = start)")
    max_lines: int = Field(default=400, ge=1, le=2000, description="How many lines to return")


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Reads a text file with line numbers. When you know the relevant range (from code_map or "
        "grep_search), read just that range; large files come in parts — continue with start_line. "
        "Jupyter notebooks are shown cell by cell with outputs. For images use view_image."
    )
    Args = ReadFileArgs
    category = "read"
    timeout = 30.0

    async def run(self, args: ReadFileArgs, ctx: ToolContext) -> str:
        path = resolve_path(args.path, settings=ctx.settings, must_exist=True, must_be_file=True)
        # F8: субагенту нельзя читать секреты — .env закрыт.
        if ctx.scratch.get("no_secrets") and path.name == ".env":
            raise ToolError("Чтение .env недоступно субагенту (изоляция секретов).")

        def _read() -> str:
            if path.suffix.lower() == ".ipynb":
                return _render_notebook(path, safe_relpath(path, ctx.settings))
            if looks_binary(path):
                size = path.stat().st_size
                raise ToolError(
                    f"'{args.path}' похоже на бинарный файл ({human_size(size)}). "
                    "Читать его как текст бессмысленно."
                )
            lines = read_text_file(path).splitlines()
            total = len(lines)
            if total == 0:
                return f"Файл '{args.path}' пуст."

            start = min(args.start_line, total)
            end = min(total, start - 1 + args.max_lines)
            body = "\n".join(
                f"{num:>6} | {line}" for num, line in enumerate(lines[start - 1 : end], start=start)
            )
            header = f"--- {safe_relpath(path, ctx.settings)} [строки {start}-{end} из {total}] ---"
            footer = ""
            if end < total:
                footer = f"\n... [ещё {total - end} строк. Продолжи: read_file(start_line={end + 1})]"
            return f"{header}\n{body}{footer}"

        return await asyncio.to_thread(_read)


def _render_notebook(path: Path, rel: str) -> str:
    """Рендерит Jupyter-ноутбук как читаемый текст: ячейки + их вывод.

    Как `Read` в Claude Code: код и markdown по ячейкам, у код-ячеек — их вывод
    (stream/text/ошибка), длинные куски подрезаются, чтобы не забить контекст.
    """
    import json

    try:
        nb = json.loads(read_text_file(path))
    except (json.JSONDecodeError, ValueError) as exc:
        raise ToolError(f"'{rel}' — не удалось разобрать как .ipynb: {exc}") from exc

    cells = nb.get("cells") or []
    if not cells:
        return f"Ноутбук '{rel}' без ячеек."

    def _text(value) -> str:
        return "".join(value) if isinstance(value, list) else str(value or "")

    def _clip(text: str, limit: int) -> str:
        text = text.rstrip()
        return text if len(text) <= limit else text[:limit] + f"\n… [+{len(text) - limit} символов]"

    out: list[str] = [f"--- {rel}: ячеек {len(cells)} ---"]
    for i, cell in enumerate(cells, 1):
        kind = cell.get("cell_type", "?")
        src = _clip(_text(cell.get("source")), 4000)
        out.append(f"\n[{i}] {kind}")
        if src:
            out.append(src)
        if kind == "code":
            for output in cell.get("outputs") or []:
                otype = output.get("output_type")
                if otype == "stream":
                    out.append("  out> " + _clip(_text(output.get("text")), 1500))
                elif otype in ("execute_result", "display_data"):
                    data = output.get("data") or {}
                    if "text/plain" in data:
                        out.append("  out> " + _clip(_text(data["text/plain"]), 1500))
                    else:
                        out.append(f"  out> [{', '.join(data.keys()) or 'нет данных'}]")
                elif otype == "error":
                    ename = output.get("ename", "Error")
                    evalue = output.get("evalue", "")
                    out.append(f"  ERR> {ename}: {_clip(str(evalue), 500)}")
    return "\n".join(out)


# --------------------------------------------------------------- write


class WriteFileArgs(BaseModel):
    path: str = Field(description="File path (parent folders are created automatically)")
    content: str = Field(description="The complete new file content")
    overwrite: bool = Field(default=True, description="Allow overwriting an existing file")


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Creates a file or replaces its entire content. Use it for new files and full rewrites; "
        "for changes to an existing file edit_file or apply_patch keep untouched code safe."
    )
    Args = WriteFileArgs
    category = "edit"
    dangerous = True
    timeout = 30.0

    def approval_reason(self, args: WriteFileArgs) -> str:
        return tr("appr.write", path=args.path, n=len(args.content))

    async def run(self, args: WriteFileArgs, ctx: ToolContext) -> str:
        path = resolve_path(args.path, settings=ctx.settings)
        if path.exists() and not args.overwrite:
            raise ToolError(f"File '{args.path}' already exists and overwrite=false.")
        if path.is_dir():
            raise ToolError(f"'{args.path}' is a directory.")

        # Снимок до записи: перезапись файла — самый частый повод для отката.
        await snapshot_before_change(ctx, path, "write")

        def _write() -> str:
            path.parent.mkdir(parents=True, exist_ok=True)
            existed = path.exists()
            path.write_text(args.content, encoding="utf-8", newline="\n")
            action = "overwritten" if existed else "created"
            return (
                f"File {safe_relpath(path, ctx.settings)} {action} "
                f"({len(args.content)} chars, {args.content.count(chr(10)) + 1} lines)."
            )

        msg = await asyncio.to_thread(_write)
        rel = safe_relpath(path, ctx.settings)
        await ctx.emitter(
            ArtifactCreated(
                path=rel,
                name=path.name,
                kind=_detect_kind(path),
                size_bytes=len(args.content.encode("utf-8")),
            )
        )
        return msg


# ---------------------------------------------------------------- edit


class EditFileArgs(BaseModel):
    path: str = Field(description="File path")
    old_text: str = Field(
        description="The exact fragment to replace, copied from the file with its indentation; "
        "must be unique in the file unless replace_all is set"
    )
    new_text: str = Field(description="The replacement fragment")
    replace_all: bool = Field(default=False, description="Replace every occurrence instead of one")


def _replace_loose(original: str, old_text: str, new_text: str) -> str | None:
    """Replaces a block that matches only when indentation/trailing spaces are ignored.

    The most common edit failure is a whitespace slip in old_text; a unique loose match
    is still unambiguous, so it is applied with new_text re-indented to the file's style.
    """
    lines = split_lines(original)
    old_lines = split_lines(old_text)
    found = find_unique_block(lines, old_lines)
    if found is None:
        return None
    pos, _ = found
    first = next((i for i, ln in enumerate(old_lines) if ln.strip()), 0)
    model_indent = old_lines[first][: len(old_lines[first]) - len(old_lines[first].lstrip())]
    file_line = lines[pos + first]
    file_indent = file_line[: len(file_line) - len(file_line.lstrip())]

    def reindent(line: str) -> str:
        if not line.strip() or model_indent == file_indent:
            return line
        if line.startswith(model_indent):
            return file_indent + line[len(model_indent) :]
        return file_indent + line.lstrip()

    lines[pos : pos + len(old_lines)] = [reindent(ln) for ln in split_lines(new_text)]
    return "\n".join(lines) + ("\n" if original.endswith("\n") else "")


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Replaces one exact text fragment in a file (old_text -> new_text). The default tool for a "
        "localized change. old_text must be unique; include a few surrounding lines if it is not. "
        "Read the file first so old_text is copied exactly."
    )
    Args = EditFileArgs
    category = "edit"
    dangerous = True
    timeout = 30.0

    def approval_reason(self, args: EditFileArgs) -> str:
        return tr("appr.edit", path=args.path, a=len(args.old_text), b=len(args.new_text))

    async def run(self, args: EditFileArgs, ctx: ToolContext) -> str:
        path = resolve_path(args.path, settings=ctx.settings, must_exist=True, must_be_file=True)

        await snapshot_before_change(ctx, path, "edit")

        def _edit() -> str:
            if looks_binary(path):
                raise ToolError(f"'{args.path}' is a binary file; editing is not allowed.")
            original = read_text_file(path)
            rel = safe_relpath(path, ctx.settings)
            count = original.count(args.old_text) if args.old_text else 0
            if count == 0:
                loose = _replace_loose(original, args.old_text, args.new_text)
                if loose is None:
                    raise ToolError(
                        f"old_text was not found in '{args.path}' (not even ignoring indentation), "
                        "or it matches several places. Re-read the file with read_file and copy "
                        "the exact text, adding surrounding lines to make it unique."
                    )
                path.write_text(loose, encoding="utf-8", newline="\n")
                return f"File {rel} updated (1 replacement, matched ignoring indentation — re-read to confirm)."
            if count > 1 and not args.replace_all:
                raise ToolError(
                    f"old_text occurs {count} times. Make it unique (add surrounding lines) "
                    "or set replace_all=true."
                )

            updated = (
                original.replace(args.old_text, args.new_text)
                if args.replace_all
                else original.replace(args.old_text, args.new_text, 1)
            )
            path.write_text(updated, encoding="utf-8", newline="\n")
            return f"File {rel} updated ({count if args.replace_all else 1} replacement(s))."

        msg = await asyncio.to_thread(_edit)
        rel = safe_relpath(path, ctx.settings)
        await ctx.emitter(
            ArtifactCreated(
                path=rel,
                name=path.name,
                kind=_detect_kind(path),
                size_bytes=path.stat().st_size,
            )
        )
        return msg


# -------------------------------------------------------------- delete


class DeletePathArgs(BaseModel):
    path: str = Field(description="Путь к удаляемому файлу или пустой директории")
    recursive: bool = Field(default=False, description="Удалить непустую директорию рекурсивно")


class DeletePathTool(Tool):
    name = "delete_path"
    description = "Удаляет файл или директорию. Опасная операция, требует осторожности."
    Args = DeletePathArgs
    category = "edit"
    dangerous = True
    irreversible = True  # удаление не откатить — спрашиваем всегда (кроме bypass)
    timeout = 30.0

    def approval_reason(self, args: DeletePathArgs) -> str:
        rec = tr("appr.recursive") if args.recursive else ""
        return tr("appr.delete", path=args.path, rec=rec)

    def auto_verdict(self, args, ctx) -> str:  # type: ignore[override]
        """Удаление необратимо — спрашиваем всегда."""
        return "ask"

    async def run(self, args: DeletePathArgs, ctx: ToolContext) -> str:
        path = resolve_path(args.path, settings=ctx.settings, must_exist=True)
        if path == ctx.settings.workspace:
            raise ToolError("Удаление корня рабочей директории запрещено.")

        # Снимок только для файлов: откат удалённого файла восстановит его.
        # Директории не снимаем — это дорого и редко нужно.
        if path.is_file():
            await snapshot_before_change(ctx, path, "delete")

        def _delete() -> str:
            import shutil

            if path.is_file():
                path.unlink()
                return f"Файл {safe_relpath(path, ctx.settings)} удалён."
            if path.is_dir():
                if args.recursive:
                    shutil.rmtree(path)
                    return f"Директория {safe_relpath(path, ctx.settings)} удалена со всем содержимым."
                try:
                    path.rmdir()
                    return f"Пустая директория {safe_relpath(path, ctx.settings)} удалена."
                except OSError as exc:
                    raise ToolError(
                        f"Директория '{args.path}' не пуста. Поставь recursive=true для полного удаления."
                    ) from exc
            raise ToolError(f"Неизвестный тип объекта: '{args.path}'.")

        return await asyncio.to_thread(_delete)
