"""Lenient (fuzzy) application of patch hunks to text.

Models regularly get line numbers and indentation wrong but almost always reproduce
the context itself correctly. So a hunk's position is searched in four passes:

  1. exact       — the context matches exactly where stated;
  2. offset      — it matches nearby (the file shifted after earlier edits);
  3. whitespace  — it matches ignoring indentation and trailing spaces;
  4. search      — the block occurs exactly once in the file.

Hunks without line numbers (bare `@@`, V4A) start from their scope anchors, if any,
otherwise from where the previous hunk ended.

If no pass succeeds the hunk is NOT applied, and the reply explains why with the real
file content — so the model can fix the patch itself.
"""

from __future__ import annotations

from core.patch.model import FileAction, FilePatch, FileResult, Hunk, HunkResult, LineOp

#: Насколько далеко от заявленной позиции ищем контекст.
SEARCH_RADIUS = 400


def detect_newline(text: str) -> str:
    """Определяет перевод строки файла, чтобы не превратить его в кашу."""
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def split_lines(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def join_lines(lines: list[str], newline: str, trailing: bool) -> str:
    body = newline.join(lines)
    return body + newline if trailing and lines else body


def _norm(line: str) -> str:
    """Строка без учёта отступов и хвостовых пробелов."""
    return line.strip()


def _hard_norm(line: str) -> str:
    """Строка вообще без пробелов — последний шанс сопоставления."""
    return "".join(line.split())


def _match_at(lines: list[str], pos: int, block: list[str], mode: str) -> bool:
    if pos < 0 or pos + len(block) > len(lines):
        return False
    if mode == "exact":
        return lines[pos : pos + len(block)] == block
    key = _norm if mode == "soft" else _hard_norm
    return [key(x) for x in lines[pos : pos + len(block)]] == [key(y) for y in block]


def _find_position(lines: list[str], block: list[str], expected: int) -> tuple[int, str] | None:
    """Ищет место для хунка. Возвращает (позиция, стратегия)."""
    if not block:
        return max(0, min(expected, len(lines))), "insert"

    expected = max(0, min(expected, len(lines)))

    if _match_at(lines, expected, block, "exact"):
        return expected, "exact"

    # Ищем по расширяющемуся радиусу: ближайшее совпадение вероятнее верное.
    limit = min(SEARCH_RADIUS, len(lines))
    for mode, strategy in (("exact", "offset"), ("soft", "whitespace")):
        for distance in range(1, limit + 1):
            for candidate in (expected - distance, expected + distance):
                if _match_at(lines, candidate, block, mode):
                    return candidate, strategy
        if _match_at(lines, expected, block, mode) and mode == "soft":
            return expected, "whitespace"

    # Полный проход: принимаем только однозначное совпадение.
    for mode in ("exact", "soft", "hard"):
        hits = [pos for pos in range(len(lines) - len(block) + 1) if _match_at(lines, pos, block, mode)]
        if len(hits) == 1:
            return hits[0], "search"

    return None


def find_unique_block(lines: list[str], block: list[str]) -> tuple[int, str] | None:
    """Finds a block that occurs exactly once, tolerating whitespace differences.

    Used by exact-replace edits as a fallback: an ambiguous match returns None rather
    than guessing, because editing the wrong occurrence is worse than a clear error.
    """
    if not block:
        return None
    for mode in ("soft", "hard"):
        hits = [pos for pos in range(len(lines) - len(block) + 1) if _match_at(lines, pos, block, mode)]
        if len(hits) == 1:
            return hits[0], "whitespace"
        if len(hits) > 1:
            return None
    return None


def _seek_anchors(lines: list[str], anchors: list[str], start: int) -> int:
    """Returns the line after the last anchor, searching forward from `start`.

    An anchor that cannot be found is skipped rather than failing the hunk: the
    context match that follows is still authoritative.
    """
    pos = start
    for anchor in anchors:
        want = anchor.strip()
        ranges = (range(pos, len(lines)), range(0, pos))
        hit = next((i for r in ranges for i in r if lines[i].strip() == want), None)
        if hit is None:
            hit = next((i for r in ranges for i in r if lines[i].strip().startswith(want)), None)
        if hit is not None:
            pos = hit + 1
    return pos


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _shift_indent(line: str, patch_indent: str, file_indent: str) -> str:
    """Переносит строку из системы отступов патча в систему отступов файла."""
    if patch_indent == file_indent or not line.strip():
        return line
    if file_indent.startswith(patch_indent):
        return file_indent[len(patch_indent) :] + line
    if patch_indent.startswith(file_indent) and line.startswith(patch_indent[len(file_indent) :]):
        return line[len(patch_indent) - len(file_indent) :]
    return file_indent + line.lstrip()


def build_new_block(lines: list[str], position: int, hunk: Hunk) -> list[str]:
    """Собирает итоговый фрагмент файла.

    Контекстные строки берутся ИЗ ФАЙЛА, а не из патча: даже если модель
    переврала отступы или хвостовые пробелы, нетронутый код останется
    байт-в-байт прежним. Добавляемые строки подгоняются под отступ файла.
    """
    result: list[str] = []
    old_index = 0
    patch_indent = ""
    file_indent = ""

    for line in hunk.lines:
        if line.op is LineOp.CONTEXT:
            source = position + old_index
            actual = lines[source] if source < len(lines) else line.text
            if line.text.strip():
                patch_indent, file_indent = _indent_of(line.text), _indent_of(actual)
            result.append(actual)
            old_index += 1
        elif line.op is LineOp.REMOVE:
            source = position + old_index
            if source < len(lines) and line.text.strip():
                patch_indent, file_indent = _indent_of(line.text), _indent_of(lines[source])
            old_index += 1
        else:  # ADD
            result.append(_shift_indent(line.text, patch_indent, file_indent))

    return result


def _context_preview(lines: list[str], around: int, width: int = 6) -> str:
    start = max(0, around - width)
    end = min(len(lines), around + width)
    if start >= end:
        return "(file is empty)"
    return "\n".join(f"{num:>5} | {lines[num - 1]}" for num in range(start + 1, end + 1))


def apply_hunks(text: str, hunks: list[Hunk]) -> tuple[str, list[HunkResult]]:
    """Применяет хунки к тексту. Возвращает новый текст и отчёт по каждому хунку."""
    newline = detect_newline(text)
    trailing = text.endswith(("\n", "\r"))
    lines = split_lines(text)

    results: list[HunkResult] = []
    offset = 0
    #: Куда дошли после предыдущего хунка. Нужен для хунков без номеров строк
    #: («@@» без «-1,5 +1,7»): модели пишут их по порядку сверху вниз.
    cursor = 0

    for index, hunk in enumerate(hunks, start=1):
        old_block = hunk.old_block
        known_position = hunk.old_start > 0
        if known_position:
            expected = hunk.old_start - 1 + offset
        elif hunk.anchors:
            expected = _seek_anchors(lines, hunk.anchors, cursor)
        else:
            expected = cursor

        found = _find_position(lines, old_block, expected)
        if found is None:
            if known_position:
                where = f"near line {hunk.old_start}"
            else:
                where = f"after {hunk.anchors or 'the previous hunk'}"
            results.append(
                HunkResult(
                    index=index,
                    applied=False,
                    error=(
                        f"context of hunk #{index} not found (expected {where}). First line "
                        f"searched for: {old_block[0]!r}.\nWhat is actually in the file:\n"
                        f"{_context_preview(lines, expected)}"
                    ),
                )
            )
            continue

        position, strategy = found
        new_block = build_new_block(lines, position, hunk)

        lines[position : position + len(old_block)] = new_block
        cursor = position + len(new_block)
        if known_position:
            offset += len(new_block) - len(old_block) + (position - expected)
        results.append(
            HunkResult(
                index=index,
                applied=True,
                offset=(position - expected) if known_position else 0,
                strategy=strategy,
            )
        )

    return join_lines(lines, newline, trailing or not text), results


def apply_file_patch(original: str | None, patch: FilePatch) -> FileResult:
    """Применяет изменения одного файла. Файл на диск не пишется."""
    result = FileResult(path=patch.path, action=patch.action, applied=False)

    if patch.action is FileAction.DELETE:
        if original is None:
            result.error = "the file does not exist"
            return result
        result.applied = True
        result.removed = len(split_lines(original))
        result.new_content = None
        return result

    if patch.action is FileAction.CREATE:
        if original is not None and original.strip():
            result.error = (
                "the file already exists and is not empty, so it cannot be created. "
                "Use an update patch or write_file."
            )
            return result
        body = [ln.text for hunk in patch.hunks for ln in hunk.lines if ln.op.value != "-"]
        result.new_content = join_lines(body, "\n", True)
        result.applied = True
        result.added = len(body)
        result.hunks = [HunkResult(index=i, applied=True, strategy="create") for i, _ in enumerate(patch.hunks, 1)]
        return result

    if original is None:
        result.error = "file not found"
        return result

    new_text, hunk_results = apply_hunks(original, patch.hunks)
    result.hunks = hunk_results
    result.applied = all(h.applied for h in hunk_results)
    result.added = patch.added
    result.removed = patch.removed
    result.new_content = new_text if result.applied else None
    if not result.applied:
        failed = [h.index for h in hunk_results if not h.applied]
        result.error = f"hunks not applied: {', '.join(map(str, failed))}"
    return result
