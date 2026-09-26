"""Patch parsing: unified diff and the V4A format (`*** Begin Patch`).

Two formats because models are trained on different ones: OpenAI models write V4A
(Codex `apply_patch`) natively, most others write unified diff. Accepting both lets
every provider use the grammar it is fluent in instead of retrofitting a foreign one.

The parser is deliberately lenient with what models actually send:
  * counts in `@@ -1,5 +1,7 @@` are often wrong — we ignore them and look at the
    actual hunk lines;
  * a blank context line often arrives without its leading space;
  * the diff may be wrapped in ``` fences and chatter;
  * git headers (`diff --git`, `index`, `new file mode`) may or may not be present.

Strictness is not an option here: a strict parser means an agent that cannot fix its
own typo and loops.
"""

from __future__ import annotations

import re

from core.errors import ToolError
from core.patch.model import FileAction, FilePatch, Hunk, LineOp, PatchLine

_HUNK_RE = re.compile(r"^@@+\s*-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s*@@+(.*)$")
#: Модели часто пишут просто "@@" без номеров строк. Это валидное сокращение
#: для нас: позиция всё равно ищется по контексту, а не по номеру.
_BARE_HUNK_RE = re.compile(r"^@@+\s*@*\s*(.*)$")
_DIFF_GIT_RE = re.compile(r"^diff --git\s+(?:a/)?(.+?)\s+(?:b/)?(.+?)\s*$")
_FENCE_RE = re.compile(r"^\s*```+\s*(?:diff|patch|udiff)?\s*$", re.IGNORECASE)

DEV_NULL = "/dev/null"


def _clean_path(raw: str) -> str:
    """Strips a/ b/ prefixes, quotes and the timestamp from a header path."""
    path = raw.strip()
    if path.startswith(('"', "'")) and path.endswith(('"', "'")) and len(path) > 1:
        path = path[1:-1]
    # git добавляет таймстамп через табуляцию: "file.py\t2024-01-01 10:00:00"
    path = path.split("\t")[0].strip()
    if path in (DEV_NULL, "b/dev/null", "a/dev/null"):
        return DEV_NULL
    for prefix in ("a/", "b/", "./"):
        if path.startswith(prefix):
            path = path[len(prefix) :]
            break
    return path.replace("\\", "/").strip()


def _strip_noise(text: str) -> list[str]:
    """Drops ``` fences and any text before the first sign of a diff."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # Завершающий перевод строки даёт пустой элемент, который иначе будет
    # прочитан как пустая строка контекста и сломает совпадение хунка.
    if lines and lines[-1] == "":
        lines.pop()
    cleaned = [ln for ln in lines if not _FENCE_RE.match(ln)]

    for index, line in enumerate(cleaned):
        if (
            line.startswith(("--- ", "+++ ", "diff --git ", "@@"))
            or line.startswith("*** ")
        ):
            return cleaned[index:]
    return cleaned


_V4A_FILE_MARKERS = ("*** Update File:", "*** Add File:", "*** Delete File:")


def _is_v4a(lines: list[str]) -> bool:
    return any(line.startswith(("*** Begin Patch", *_V4A_FILE_MARKERS)) for line in lines)


def _parse_v4a(lines: list[str]) -> list[FilePatch]:
    """Parses the V4A format: file sections with context-anchored hunks, no line numbers."""
    patches: list[FilePatch] = []
    current: FilePatch | None = None
    hunk: Hunk | None = None

    def close_file() -> None:
        nonlocal current, hunk
        if current is not None:
            patches.append(current)
        current = None
        hunk = None

    for line in lines:
        if line.startswith(("*** Begin Patch", "*** End of File")):
            continue
        if line.startswith("*** End Patch"):
            close_file()
            continue
        if line.startswith("*** Add File:"):
            close_file()
            hunk = Hunk(old_start=0, new_start=0)
            current = FilePatch(path=_clean_path(line.split(":", 1)[1]), action=FileAction.CREATE)
            current.hunks.append(hunk)
            continue
        if line.startswith("*** Delete File:"):
            close_file()
            patches.append(FilePatch(path=_clean_path(line.split(":", 1)[1]), action=FileAction.DELETE))
            continue
        if line.startswith("*** Update File:"):
            close_file()
            current = FilePatch(path=_clean_path(line.split(":", 1)[1]), action=FileAction.MODIFY)
            continue
        if line.startswith("*** Move to:"):
            if current is not None:
                current.old_path = current.path
                current.path = _clean_path(line.split(":", 1)[1])
                current.action = FileAction.RENAME
            continue
        if current is None:
            continue  # chatter between file sections

        if current.action is FileAction.CREATE:
            assert hunk is not None
            hunk.lines.append(PatchLine(LineOp.ADD, line[1:] if line.startswith("+") else line))
            continue

        if line.startswith("@@"):
            anchor = line[2:].strip()
            numbered = _HUNK_RE.match(line)
            if numbered:
                # A unified-style header inside V4A: keep the position hint, no anchor.
                hunk = Hunk(old_start=int(numbered.group(1)), new_start=int(numbered.group(3)))
                current.hunks.append(hunk)
            elif hunk is not None and not hunk.lines:
                # Stacked anchors (`@@ class A` then `@@     def b`) narrow the same hunk.
                if anchor:
                    hunk.anchors.append(anchor)
            else:
                hunk = Hunk(old_start=0, new_start=0, header=anchor, anchors=[anchor] if anchor else [])
                current.hunks.append(hunk)
            continue

        if hunk is None:
            hunk = Hunk(old_start=0, new_start=0)
            current.hunks.append(hunk)
        if line.startswith("+"):
            hunk.lines.append(PatchLine(LineOp.ADD, line[1:]))
        elif line.startswith("-"):
            hunk.lines.append(PatchLine(LineOp.REMOVE, line[1:]))
        elif line.startswith(" "):
            hunk.lines.append(PatchLine(LineOp.CONTEXT, line[1:]))
        elif line == "":
            hunk.lines.append(PatchLine(LineOp.CONTEXT, ""))
        # Anything else is model commentary inside the section — ignore it.

    close_file()
    return patches


def parse_patch(text: str) -> list[FilePatch]:
    """Parses a patch in either unified diff or V4A format into per-file changes.

    Raises:
        ToolError: if the text contains no recognisable change.
    """
    if not text or not text.strip():
        raise ToolError("The patch is empty.")

    lines = _strip_noise(text)
    if _is_v4a(lines):
        patches = _parse_v4a(lines)
        if not patches:
            raise ToolError("Could not parse the patch: no '*** Update/Add/Delete File:' sections found.")
        _check_not_empty(patches)
        return patches
    return _parse_unified(lines)


def parse_unified_diff(text: str) -> list[FilePatch]:
    """Backward-compatible name: accepts both formats, see `parse_patch`."""
    return parse_patch(text)


def _check_not_empty(patches: list[FilePatch]) -> None:
    # A pure move (RENAME without hunks) is a valid change; an update without lines is not.
    for patch in patches:
        if patch.action is FileAction.MODIFY and not any(h.lines for h in patch.hunks):
            raise ToolError(f"No changes given for file '{patch.path}'.")


def _parse_unified(lines: list[str]) -> list[FilePatch]:
    patches: list[FilePatch] = []

    current: FilePatch | None = None
    hunk: Hunk | None = None
    pending_old: str | None = None
    pending_new: str | None = None
    git_paths: tuple[str, str] | None = None
    file_mode: FileAction | None = None
    rename_from: str | None = None

    def close_file() -> None:
        nonlocal current, hunk
        if current is not None and (current.hunks or current.action is not FileAction.MODIFY):
            patches.append(current)
        current = None
        hunk = None

    def open_file(old: str | None, new: str | None) -> None:
        nonlocal current, file_mode, rename_from
        close_file()

        old_path = old or DEV_NULL
        new_path = new or DEV_NULL

        if new_path == DEV_NULL and old_path != DEV_NULL:
            action, path = FileAction.DELETE, old_path
        elif old_path == DEV_NULL and new_path != DEV_NULL:
            action, path = FileAction.CREATE, new_path
        else:
            action, path = FileAction.MODIFY, new_path

        if file_mode is FileAction.CREATE:
            action = FileAction.CREATE
        elif file_mode is FileAction.DELETE:
            action = FileAction.DELETE
        elif file_mode is FileAction.RENAME or (rename_from and rename_from != path):
            action = FileAction.RENAME

        current = FilePatch(
            path=path,
            action=action,
            old_path=rename_from or (old_path if old_path != DEV_NULL else None),
        )
        file_mode = None
        rename_from = None

    index = 0
    while index < len(lines):
        line = lines[index]

        git_match = _DIFF_GIT_RE.match(line)
        if git_match:
            close_file()
            git_paths = (_clean_path(git_match.group(1)), _clean_path(git_match.group(2)))
            pending_old = pending_new = None
            index += 1
            continue

        if line.startswith("new file mode"):
            file_mode = FileAction.CREATE
            index += 1
            continue
        if line.startswith("deleted file mode"):
            file_mode = FileAction.DELETE
            index += 1
            continue
        if line.startswith("rename from "):
            rename_from = _clean_path(line[len("rename from ") :])
            file_mode = FileAction.RENAME
            index += 1
            continue
        if line.startswith("rename to "):
            git_paths = (rename_from or "", _clean_path(line[len("rename to ") :]))
            index += 1
            continue
        if line.startswith(("index ", "old mode", "new mode", "similarity index", "Binary files")):
            index += 1
            continue

        if line.startswith("--- "):
            pending_old = _clean_path(line[4:])
            index += 1
            continue

        if line.startswith("+++ "):
            pending_new = _clean_path(line[4:])
            open_file(pending_old, pending_new)
            pending_old = pending_new = None
            git_paths = None
            index += 1
            continue

        hunk_match = _HUNK_RE.match(line)
        bare_match = None if hunk_match else _BARE_HUNK_RE.match(line)
        if hunk_match or bare_match:
            if current is None:
                # Дифф без заголовков файлов — берём путь из `diff --git`.
                if git_paths:
                    open_file(git_paths[0], git_paths[1])
                else:
                    raise ToolError(
                        "The patch has an @@ hunk but no file. Add '--- a/path' and "
                        "'+++ b/path' headers before the hunk."
                    )

            if hunk_match:
                hunk = Hunk(
                    old_start=int(hunk_match.group(1)),
                    new_start=int(hunk_match.group(3)),
                    header=hunk_match.group(5).strip(),
                )
            else:
                # Position unknown (0) — the applier locates it by context. Text after
                # a bare "@@" is a scope anchor, the same idea as in V4A.
                assert bare_match is not None
                header = bare_match.group(1).strip()
                hunk = Hunk(old_start=0, new_start=0, header=header, anchors=[header] if header else [])
            assert current is not None
            current.hunks.append(hunk)
            index += 1

            # Тело хунка читаем до следующего служебного заголовка.
            while index < len(lines):
                body = lines[index]
                if (
                    _HUNK_RE.match(body)
                    or body.startswith("@@")
                ) or body.startswith(
                    ("--- ", "+++ ", "diff --git ", "index ", "new file mode", "deleted file mode")
                ):
                    break
                if body.startswith("\\"):  # "\ No newline at end of file"
                    index += 1
                    continue

                if body.startswith("+"):
                    hunk.lines.append(PatchLine(LineOp.ADD, body[1:]))
                elif body.startswith("-"):
                    hunk.lines.append(PatchLine(LineOp.REMOVE, body[1:]))
                elif body.startswith(" "):
                    hunk.lines.append(PatchLine(LineOp.CONTEXT, body[1:]))
                elif body == "":
                    # Пустая строка контекста часто приходит без ведущего пробела.
                    hunk.lines.append(PatchLine(LineOp.CONTEXT, ""))
                else:
                    # Мусор после хунка (пояснения модели) — конец хунка.
                    break
                index += 1
            continue

        index += 1

    close_file()

    if not patches:
        raise ToolError(
            "Could not parse the patch: no '@@' hunk found. Use unified diff "
            "('--- a/file', '+++ b/file', then hunks) or V4A ('*** Begin Patch' / "
            "'*** Update File: file' / '@@' / ' context' '-old' '+new' / '*** End Patch')."
        )

    for patch in patches:
        if patch.action is FileAction.MODIFY and not patch.hunks:
            raise ToolError(f"No changes given for file '{patch.path}'.")

    return patches
