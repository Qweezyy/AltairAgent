"""How one tool call looks in the terminal: a header line and a short body under it.

    ● Update(core/app.py)
      ⎿  +3 −1
         12 -    old line
         12 +    new line

The body is a preview: long output is cut to a few lines with a note how many more there are,
and `/out` prints the last tool's output in full.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any

from rich.console import Group, RenderableType
from rich.text import Text

from cli import theme
from cli.texts import Texts

#: Tool name → (label, the argument shown in brackets).
LABELS: dict[str, tuple[str, str]] = {
    "read_file": ("Read", "path"),
    "write_file": ("Write", "path"),
    "edit_file": ("Update", "path"),
    "delete_path": ("Delete", "path"),
    "apply_patch": ("Patch", ""),
    "execute_command": ("Shell", "command"),
    "run_python": ("Python", "code"),
    "run_tests": ("Tests", "command"),
    "run_lint": ("Lint", "command"),
    "type_check": ("Types", "path"),
    "list_directory": ("List", "path"),
    "find_files": ("Find", "pattern"),
    "grep_search": ("Search", "query"),
    "web_search": ("Web search", "query"),
    "fetch_url": ("Fetch", "url"),
    "browse_page": ("Browse", "url"),
    "http_request": ("HTTP", "url"),
    "download_file": ("Download", "url"),
    "deep_research": ("Research", "question"),
    "spawn_subagent": ("Agent", "task"),
    "run_background": ("Background", "command"),
    "git_status": ("Git status", ""),
    "git_diff": ("Git diff", "path"),
    "git_log": ("Git log", ""),
    "git_commit": ("Git commit", "message"),
    "remember": ("Remember", "title"),
    "memory_read": ("Memory", "name"),
    "tool_search": ("Load tools", "query"),
    "read_document": ("Read", "path"),
    "view_image": ("View", "path"),
    "show_image": ("Show", "path"),
    "attach_file": ("Attach", "path"),
}

#: The first argument worth showing for a tool without a label of its own.
_ARG_KEYS = ("path", "file_path", "command", "query", "url", "name", "pattern", "task", "code", "title")

#: Lines of a preview before it is cut.
PREVIEW_LINES = 12
DIFF_LINES = 30


def one_line(value: Any, limit: int = 80) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def tool_hint(args: dict[str, Any]) -> str:
    for key in _ARG_KEYS:
        value = args.get(key)
        if value:
            return one_line(value, 70)
    return ""


def label_of(name: str, args: dict[str, Any]) -> tuple[str, str]:
    label, key = LABELS.get(name, (name, ""))
    hint = (
        one_line(args.get(key), 70) if key and args.get(key) else ("" if name in LABELS else tool_hint(args))
    )
    return label, hint


def header(name: str, args: dict[str, Any], ok: bool | None, duration_ms: int = 0) -> Text:
    label, hint = label_of(name, args)
    dot_style = theme.FAINT if ok is None else (theme.OK if ok else theme.ERR)
    line = Text()
    line.append(f"{theme.DOT} ", style=dot_style)
    line.append(label, style="bold")
    if hint:
        line.append("(", style=theme.MUTED)
        line.append(hint)
        line.append(")", style=theme.MUTED)
    if duration_ms >= 1000:
        line.append(f"  {duration_ms / 1000:.1f}s", style=theme.FAINT)
    return line


def _elbow(text: Text | str, style: str = theme.MUTED) -> Text:
    line = Text("  ")
    line.append(f"{theme.ELBOW}  ", style=theme.FAINT)
    line.append(text if isinstance(text, Text) else Text(text, style=style))
    return line


def _indent(text: Text) -> Text:
    return Text("     ").append(text)


def _more(t: Texts, n: int) -> Text:
    return _indent(Text(t("tool.more", n=n), style=theme.FAINT))


def line_of(workspace: str, path: str, fragment: str) -> int:
    """The line a fragment starts at in a file (1 when the file or the fragment is not found):
    the diff then shows the file's own line numbers."""
    if not fragment or not path:
        return 1
    target = Path(path) if Path(path).is_absolute() else Path(workspace or ".") / path
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 1
    at = text.find(fragment)
    return text.count("\n", 0, at) + 1 if at >= 0 else 1


def diff_lines(old: str, new: str, start: int = 1) -> list[tuple[str, int, str]]:
    """(mark, line number, text) for a changed fragment: '-' old, '+' new, ' ' context."""
    a, b = old.splitlines(), new.splitlines()
    shift = start - 1
    rows: list[tuple[str, int, str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i1, i2):
                rows.append((" ", shift + j1 + (k - i1) + 1, a[k]))
            continue
        for k in range(i1, i2):
            rows.append(("-", shift + k + 1, a[k]))
        for k in range(j1, j2):
            rows.append(("+", shift + k + 1, b[k]))
    return _trim_context(rows)


def _trim_context(rows: list[tuple[str, int, str]], keep: int = 2) -> list[tuple[str, int, str]]:
    """Unchanged lines far from a change are not shown."""
    changed = [i for i, r in enumerate(rows) if r[0] != " "]
    if not changed:
        return []
    near = {j for i in changed for j in range(i - keep, i + keep + 1)}
    return [r for i, r in enumerate(rows) if i in near]


def render_diff(rows: list[tuple[str, int, str]], t: Texts, limit: int = DIFF_LINES) -> list[Text]:
    out: list[Text] = []
    width = max((len(str(r[1])) for r in rows), default=1)
    for mark, number, text in rows[:limit]:
        line = Text("     ")
        if mark == " ":
            line.append(f"{number:>{width}}   {text}", style=theme.MUTED)
        else:
            body, sign = (
                (theme.ADDED, theme.ADDED_MARK) if mark == "+" else (theme.REMOVED, theme.REMOVED_MARK)
            )
            line.append(f"{number:>{width}} ", style=body)
            line.append(f"{mark} ", style=sign)
            line.append(text.expandtabs(4), style=body)
        out.append(line)
    if len(rows) > limit:
        out.append(_more(t, len(rows) - limit))
    return out


def patch_rows(patch: str) -> list[tuple[str, int, str]]:
    """A unified diff / V4A patch as display rows (file headers become context lines)."""
    rows: list[tuple[str, int, str]] = []
    new_no = 0
    for raw in patch.splitlines():
        if raw.startswith(("+++", "---", "diff --git", "index ", "*** ")):
            rows.append((" ", 0, raw))
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            new_no = int(m.group(1)) - 1 if m else 0
            rows.append((" ", 0, raw))
        elif raw.startswith("+"):
            new_no += 1
            rows.append(("+", new_no, raw[1:]))
        elif raw.startswith("-"):
            rows.append(("-", new_no, raw[1:]))
        else:
            new_no += 1
            rows.append((" ", new_no, raw[1:] if raw.startswith(" ") else raw))
    return rows


def _counts(rows: list[tuple[str, int, str]]) -> tuple[int, int]:
    return sum(r[0] == "+" for r in rows), sum(r[0] == "-" for r in rows)


def _tail(output: str, t: Texts, limit: int = 8) -> list[Text]:
    lines = [ln.rstrip() for ln in output.rstrip().splitlines()]
    if not lines:
        return [_elbow(t("tool.no_output"))]
    out: list[Text] = []
    if len(lines) > limit:
        out.append(_elbow(Text(t("tool.earlier", n=len(lines) - limit), style=theme.FAINT)))
        lines = lines[-limit:]
        out.extend(_indent(Text(one_line(ln, 200), style=theme.MUTED)) for ln in lines)
        return out
    out.append(_elbow(one_line(lines[0], 200)))
    out.extend(_indent(Text(one_line(ln, 200), style=theme.MUTED)) for ln in lines[1:])
    return out


#: The shell tool's own framing around what the command printed.
_SHELL_FRAME = re.compile(r"^(Exit code|Код возврата): \d+$|^stdout:$|^\((no output|вывод пуст)\)$")


def command_output(output: str) -> str:
    """What a command printed, without the tool's framing ("Exit code: 0", "stdout:")."""
    lines = [ln for ln in output.splitlines() if not _SHELL_FRAME.match(ln.strip())]
    return "\n".join(lines).strip("\n")


def _first_line(output: str) -> str:
    for line in output.splitlines():
        if line.strip():
            return one_line(line, 160)
    return ""


def preview(name: str, args: dict[str, Any], t: Texts, start: int = 1) -> list[Text]:
    """What a call is about to do — for an approval: the change or the command in full."""
    if name == "edit_file":
        rows = diff_lines(str(args.get("old_text") or ""), str(args.get("new_text") or ""), start)
        return render_diff(rows, t, limit=60)
    if name == "write_file":
        content = str(args.get("content") or "")
        lines = content.splitlines()
        shown = [_indent(Text(f"{i + 1:>4}  {ln}", style=theme.MUTED)) for i, ln in enumerate(lines[:20])]
        if len(lines) > 20:
            shown.append(_more(t, len(lines) - 20))
        return shown
    if name == "apply_patch":
        return render_diff(patch_rows(str(args.get("patch") or "")), t, limit=60)
    if name in ("execute_command", "run_background"):
        command = str(args.get("command") or "")
        # The header shows a short command whole; a long one is cut there, so here it is in full.
        return (
            [_indent(Text(command, style="bold"))]
            if len(one_line(command, 10_000)) > 70 or "\n" in command
            else []
        )
    if name == "run_python":
        lines = str(args.get("code") or "").splitlines()
        return [_indent(Text(ln, style=theme.MUTED)) for ln in lines[:20]] + (
            [_more(t, len(lines) - 20)] if len(lines) > 20 else []
        )
    hint = tool_hint(args)
    return [_indent(Text(hint, style=theme.MUTED))] if hint else []


def body(name: str, args: dict[str, Any], ok: bool, output: str, t: Texts, start: int = 1) -> list[Text]:
    """What happened, in a few lines."""
    if not ok:
        lines = [ln for ln in output.strip().splitlines() if ln.strip()] or [t("tool.failed")]
        out = [_elbow(Text(one_line(lines[0], 200), style=theme.ERR))]
        out.extend(_indent(Text(one_line(ln, 200), style=theme.ERR)) for ln in lines[1:4])
        if len(lines) > 4:
            out.append(_more(t, len(lines) - 4))
        return out
    if name == "read_file":
        n = sum(1 for ln in output.splitlines() if re.match(r"^\s*\d+\|", ln)) or len(output.splitlines())
        return [_elbow(t("tool.read", n=n))]
    if name == "write_file":
        n = len(str(args.get("content") or "").splitlines())
        return [_elbow(t("tool.wrote", n=n))]
    if name == "edit_file":
        rows = diff_lines(str(args.get("old_text") or ""), str(args.get("new_text") or ""), start)
        added, removed = _counts(rows)
        return [_elbow(t("tool.changed", added=added, removed=removed)), *render_diff(rows, t)]
    if name == "apply_patch":
        rows = patch_rows(str(args.get("patch") or ""))
        added, removed = _counts(rows)
        return [_elbow(t("tool.changed", added=added, removed=removed)), *render_diff(rows, t)]
    if name in ("execute_command", "run_background"):
        return _tail(command_output(output), t)
    if name in ("run_tests", "run_lint", "run_python", "type_check"):
        return _tail(output, t)
    if name in ("update_plan", "write_plan"):
        return []
    first = _first_line(output)
    return [_elbow(first)] if first else []


def render(
    name: str, args: dict[str, Any], ok: bool, output: str, duration_ms: int, t: Texts, start: int = 1
) -> RenderableType:
    return Group(header(name, args, ok, duration_ms), *body(name, args, ok, output, t, start))


def plan(steps: list[dict[str, Any]]) -> RenderableType:
    marks = {"completed": ("☒", theme.OK), "in_progress": ("◐", theme.GOLD), "failed": ("☒", theme.ERR)}
    lines = []
    for i, step in enumerate(steps):
        mark, style = marks.get(str(step.get("status")), ("☐", theme.MUTED))
        line = Text("  ")
        line.append(f"{theme.ELBOW}  " if i == 0 else "   ", style=theme.FAINT)
        line.append(f"{mark} ", style=style)
        title = str(step.get("title") or "")
        if step.get("status") == "completed":
            line.append(title, style=f"{theme.MUTED} strike")
        elif step.get("status") == "in_progress":
            line.append(title, style="bold")
        else:
            line.append(title)
        lines.append(line)
    return Group(*lines)
