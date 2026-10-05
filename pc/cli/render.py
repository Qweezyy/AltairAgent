"""How the terminal shows a run.

The answer is Markdown printed block by block as it streams (a finished paragraph, list or code
block appears at once, the one being written waits for its end), tool calls are a header and a
short preview (tools_view), and the end of the run is one summary line. Output goes into the
terminal's own scrollback: nothing is redrawn, so it can be scrolled, searched and copied.

The renderer also keeps what the status line shows: what the agent is doing, tokens, cost and
how full the context is.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from rich.console import Console, ConsoleOptions, RenderableType, RenderResult
from rich import box
from rich.markdown import CodeBlock, Markdown, TableElement
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from cli import theme, tools_view
from cli.texts import Texts

CODE_THEME = "ansi_dark"


class _Code(CodeBlock):
    """A code block without rich's blank padding lines around it: in a terminal conversation
    they read as a gap in the answer."""

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        code = str(self.text).rstrip()
        yield Syntax(
            code,
            self.lexer_name,
            theme=self.theme,
            word_wrap=True,
            padding=(0, 1),
            background_color="default",
        )


class _Table(TableElement):
    """A table without its blank top and bottom edge rows."""

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        for part in super().__rich_console__(console, options):
            if isinstance(part, Table):
                part.show_edge = False
                part.box = box.SIMPLE_HEAD
                part.border_style = theme.FAINT
            yield part


class AnswerMarkdown(Markdown):
    elements = {**Markdown.elements, "fence": _Code, "code_block": _Code, "table_open": _Table}


def split_ready(buf: str) -> tuple[str, str]:
    """(the finished blocks, the rest): a block ends at a blank line outside a code fence."""
    lines = buf.split("\n")
    fence = False
    cut = -1
    for i, line in enumerate(lines[:-1]):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        elif not line.strip() and not fence:
            cut = i
    if cut < 0:
        return "", buf
    return "\n".join(lines[:cut]), "\n".join(lines[cut + 1 :])


def human_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def money(usd: float) -> str:
    if usd <= 0:
        return ""
    return "<$0.01" if usd < 0.01 else f"${usd:.2f}"


class Renderer:
    """Turns the backend's events into terminal output (text mode)."""

    def __init__(self, texts: Texts, console: Console | None = None, *, thinking: bool = False) -> None:
        self.t = texts
        self.console = console or Console(highlight=False)
        self.rich = self.console.is_terminal
        self.thinking = thinking
        self._text = ""
        self._reason = ""
        self._first_block = True
        self._args: dict[str, dict[str, Any]] = {}
        self._blank = True  # the last printed line was empty: no second gap
        self._last_plan: list[dict[str, Any]] = []
        self.workspace = ""  # where relative paths of the tools point (for line numbers)
        # --- what the status line shows ---
        self.phase = ""  # "" idle | thinking | reasoning | writing | tool | pending | retry
        self.detail = ""
        self.run_started = 0.0
        self.run_id = ""
        self.tokens = 0
        self.usd = 0.0
        self.ctx_tokens = 0
        self.session_usd = 0.0
        self.last_tool: dict[str, Any] = {}
        self.last_run_id = ""

    # ------------------------------------------------------------ printing

    def print(self, renderable: RenderableType = "", *, gap: bool = False) -> None:
        if gap and not self._blank:
            self.console.print()
        self.console.print(renderable)
        self._blank = renderable == ""

    def gap(self) -> None:
        if not self._blank:
            self.console.print()
            self._blank = True

    def _gutter(self, mark: str, style: str, body: RenderableType) -> RenderableType:
        grid = Table.grid(padding=0, expand=True)
        grid.add_column(width=2, no_wrap=True)
        grid.add_column(ratio=1)
        grid.add_row(Text(mark, style=style), body)
        return grid

    def markdown(self, text: str, first: bool) -> None:
        if not text.strip():
            return
        md = AnswerMarkdown(text, code_theme=CODE_THEME, hyperlinks=True)
        self.print(self._gutter(f"{theme.DOT} " if first else "  ", theme.INK, md), gap=True)

    # ------------------------------------------------------------ the answer

    def _flush_text(self, final: bool = True) -> None:
        if not self.rich:
            if self._text and final:
                sys.stdout.write("\n")
                sys.stdout.flush()
                self._text = ""
            return
        if final:
            ready, self._text = self._text, ""
        else:
            ready, self._text = split_ready(self._text)
        if ready.strip():
            self.markdown(ready, self._first_block)
            self._first_block = False

    def _flush_reasoning(self) -> None:
        text, self._reason = self._reason.strip(), ""
        if text and self.thinking:
            self.print(self._gutter("✻ ", theme.FAINT, Text(text, style=f"{theme.MUTED} italic")), gap=True)

    def text(self, chunk: str) -> None:
        if self._reason:
            self._flush_reasoning()
        self._text += chunk
        if not self.rich:
            sys.stdout.write(chunk)
            sys.stdout.flush()
            return
        self._flush_text(final=False)

    def pause(self) -> None:
        """Before a prompt: what streamed so far is printed, the rest goes below."""
        self._flush_reasoning()
        self._flush_text()

    def _end_segment(self) -> None:
        self.pause()
        self._first_block = True

    # ------------------------------------------------------------ events

    def event(self, m: dict[str, Any]) -> None:
        kind = m.get("type")
        handler = getattr(self, "_on_" + str(kind).replace(".", "_"), None)
        if handler is not None:
            handler(m)

    def _on_run_started(self, m: dict[str, Any]) -> None:
        self.phase, self.detail = "thinking", ""
        self.run_started = time.monotonic()
        self.run_id = str(m.get("run_id") or "")
        self.tokens, self.usd = 0, 0.0
        self._first_block = True

    def _on_step_started(self, m: dict[str, Any]) -> None:
        self.phase, self.detail = "thinking", ""

    def _on_text_delta(self, m: dict[str, Any]) -> None:
        self.phase, self.detail = "writing", ""
        self.text(m.get("text") or "")

    def _on_reasoning_delta(self, m: dict[str, Any]) -> None:
        self.phase, self.detail = "reasoning", ""
        if self.thinking:
            if self._text:
                self._end_segment()
            self._reason += m.get("text") or ""

    def _on_tool_pending(self, m: dict[str, Any]) -> None:
        self.phase = "pending"
        label, _ = tools_view.label_of(str(m.get("name") or ""), {})
        chars = int(m.get("chars") or 0)
        self.detail = f"{label} · {human_tokens(chars)} chars" if chars else label

    def _on_tool_started(self, m: dict[str, Any]) -> None:
        self._end_segment()
        args = m.get("args") or {}
        self._args[m.get("call_id", "")] = args
        label, hint = tools_view.label_of(str(m.get("name") or ""), args)
        self.phase, self.detail = "tool", f"{label}({hint})" if hint else label

    def _on_tool_finished(self, m: dict[str, Any]) -> None:
        self._end_segment()
        name = str(m.get("name") or "")
        args = self._args.pop(m.get("call_id", ""), m.get("args") or {})
        output = str(m.get("output") or "")
        ok = bool(m.get("ok"))
        self.last_tool = {"name": name, "args": args, "output": output, "ok": ok}
        if not self._args:
            self.phase, self.detail = "thinking", ""
        if name in ("update_plan", "write_plan", "ask") and ok:
            return  # shown by plan.updated / the question picker
        if self.rich:
            start = 1
            if name == "edit_file" and ok:
                start = tools_view.line_of(
                    self.workspace, str(args.get("path") or ""), str(args.get("new_text") or "")
                )
            self.print(
                tools_view.render(name, args, ok, output, int(m.get("duration_ms") or 0), self.t, start),
                gap=True,
            )
        else:
            mark = "✓" if ok else "✗"
            hint = tools_view.tool_hint(args)
            self.print(f"  {mark} {name}" + (f"  {hint}" if hint else ""))

    def _on_plan_updated(self, m: dict[str, Any]) -> None:
        steps = [s for s in (m.get("steps") or []) if isinstance(s, dict)]
        if not steps or steps == self._last_plan:
            return
        self._last_plan = steps
        self._end_segment()
        head = Text(f"{theme.DOT} ", style=theme.GOLD).append(self.t("plan"), style="bold")
        self.print(head, gap=True)
        self.print(tools_view.plan(steps))

    def _on_usage_updated(self, m: dict[str, Any]) -> None:
        self.tokens = int(m.get("tokens") or 0)
        self.usd = float(m.get("usd") or 0.0)

    def _on_context_usage(self, m: dict[str, Any]) -> None:
        self.ctx_tokens = int(m.get("tokens") or 0)

    def _on_reconnecting(self, m: dict[str, Any]) -> None:
        self.phase = "retry"
        self.detail = f"{m.get('attempt')}/{m.get('max_attempts')}"
        reason = tools_view.one_line(m.get("reason") or "", 120)
        self.print(
            Text(
                f"  ↻ {self.t('retrying', n=self.detail)}" + (f" — {reason}" if reason else ""),
                style=theme.WARN,
            ),
            gap=True,
        )

    def _on_research_progress(self, m: dict[str, Any]) -> None:
        self.detail = tools_view.one_line(m.get("text") or "", 60)

    def _done_line(self, m: dict[str, Any]) -> Text:
        sec = round((m.get("duration_ms") or 0) / 1000, 1)
        cost = float(m.get("cost_usd") or 0.0)
        cost_text = f" · {money(cost)}" if cost > 0 else ""
        return Text(
            f"{theme.STAR} " + self.t("done", sec=sec, steps=m.get("steps", 0), cost=cost_text),
            style=theme.FAINT,
        )

    def _on_run_finished(self, m: dict[str, Any]) -> None:
        self._end_segment()
        self.session_usd += float(m.get("cost_usd") or 0.0)
        self.last_run_id = str(m.get("run_id") or self.run_id)
        self.phase, self.detail = "", ""
        self.print(self._done_line(m), gap=True)

    def _on_run_failed(self, m: dict[str, Any]) -> None:
        self._end_segment()
        self.phase, self.detail = "", ""
        self.print(
            Text(f"{theme.DOT} " + self.t("failed", message=m.get("message", "")), style=theme.ERR), gap=True
        )

    def _on_run_cancelled(self, m: dict[str, Any]) -> None:
        self._end_segment()
        self.last_run_id = str(m.get("run_id") or self.run_id)
        self.phase, self.detail = "", ""
        self.print(Text(f"  {theme.ELBOW}  " + self.t("cancelled"), style=theme.WARN), gap=True)

    def _on_state(self, m: dict[str, Any]) -> None:
        if m.get("state") == "idle":
            self.phase, self.detail = "", ""

    def _on_log(self, m: dict[str, Any]) -> None:
        level = m.get("level")
        if level in ("warning", "error"):
            self._end_segment()
            self.print(
                Text(f"  ! {m.get('text', '')}", style=theme.WARN if level == "warning" else theme.ERR)
            )

    def _on_steering_queued(self, m: dict[str, Any]) -> None:
        self.print(Text(f"  ↳ {self.t('steering')}", style=theme.FAINT))

    def _on_reminder_fired(self, m: dict[str, Any]) -> None:
        self._end_segment()
        text = m.get("note") or m.get("text") or ""
        self.print(Text(f"⏰ {m.get('title') or ''}: {text}", style="magenta"), gap=True)

    def _on_model_routed(self, m: dict[str, Any]) -> None:
        self.print(Text(f"  → {m.get('model')}", style=theme.FAINT))

    def _file_line(self, path: str, caption: str = "") -> None:
        self._end_segment()
        line = Text(f"{theme.DOT} ", style=theme.INFO)
        line.append(path, style=f"underline {theme.INFO}")
        if caption:
            line.append(f"  {caption}", style=theme.MUTED)
        self.print(line, gap=True)

    def _on_show_image(self, m: dict[str, Any]) -> None:
        self._file_line(str(m.get("path") or ""), str(m.get("caption") or ""))

    def _on_show_file(self, m: dict[str, Any]) -> None:
        self._file_line(str(m.get("path") or ""), str(m.get("caption") or ""))

    def _on_show_html(self, m: dict[str, Any]) -> None:
        self._end_segment()
        self.print(Text(f"  {theme.ELBOW}  " + self.t("widget"), style=theme.FAINT), gap=True)

    def _on_run_rollback(self, m: dict[str, Any]) -> None:
        if m.get("error"):
            self.print(Text(f"  ! {m['error']}", style=theme.WARN), gap=True)
            return
        restored = m.get("restored") or []
        self.print(Text(f"{theme.DOT} " + self.t("undone", n=len(restored)), style=theme.OK), gap=True)
        for path in restored[:20]:
            self.print(Text(f"     {path}", style=theme.MUTED))

    def close(self) -> None:
        self._end_segment()


def json_line(m: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(m, ensure_ascii=False) + "\n")
    sys.stdout.flush()
