"""How the terminal shows a run: the answer as Markdown streamed live, tool steps as short dim
lines, and the end of the run as one summary line."""

from __future__ import annotations

import json
import sys
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

from cli.texts import Texts

#: The first argument worth showing for a tool (a path, a command, a query…).
_ARG_KEYS = ("path", "file_path", "command", "query", "url", "name", "pattern", "task", "code")


def tool_hint(args: dict[str, Any]) -> str:
    for key in _ARG_KEYS:
        value = args.get(key)
        if value:
            text = " ".join(str(value).split())
            return text if len(text) <= 70 else text[:67] + "…"
    return ""


class Renderer:
    """Turns the backend's events into terminal output (text mode)."""

    def __init__(self, texts: Texts, console: Console | None = None, *, thinking: bool = False) -> None:
        self.t = texts
        self.console = console or Console(highlight=False, soft_wrap=False)
        self.live_ok = self.console.is_terminal
        self.thinking = thinking
        self._text = ""
        self._live: Live | None = None
        self._args: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------ the answer

    def _flush_text(self) -> None:
        if self._live is not None:
            self._live.update(Markdown(self._text))
            self._live.stop()
            self._live = None
        elif self._text and not self.live_ok:
            sys.stdout.write("\n")
        self._text = ""

    def text(self, chunk: str) -> None:
        self._text += chunk
        if not self.live_ok:
            sys.stdout.write(chunk)
            sys.stdout.flush()
            return
        if self._live is None:
            self._live = Live(Markdown(self._text), console=self.console, refresh_per_second=8,
                              vertical_overflow="visible", transient=False)
            self._live.start()
        else:
            self._live.update(Markdown(self._text))

    def pause(self) -> None:
        """Before a prompt: the streamed part stays on screen, the next part starts below."""
        self._flush_text()

    # ------------------------------------------------------------ events

    def event(self, m: dict[str, Any]) -> None:
        kind = m.get("type")
        if kind == "text.delta":
            self.text(m.get("text") or "")
        elif kind == "reasoning.delta" and self.thinking:
            self._flush_text()
            self.console.print(Text(m.get("text") or "", style="dim italic"), end="")
        elif kind == "tool.started":
            self._flush_text()
            self._args[m.get("call_id", "")] = m.get("args") or {}
        elif kind == "tool.finished":
            args = self._args.pop(m.get("call_id", ""), m.get("args") or {})
            mark, style = ("✓", "green") if m.get("ok") else ("✗", "red")
            hint = tool_hint(args)
            line = Text("  ")
            line.append(mark + " ", style=style)
            line.append(str(m.get("name")), style="bold dim")
            if hint:
                line.append("  " + hint, style="dim")
            ms = int(m.get("duration_ms") or 0)
            if ms >= 1000:
                line.append(f"  {ms / 1000:.1f} s", style="dim")
            self.console.print(line)
        elif kind == "run.finished":
            self._flush_text()
            cost = m.get("cost_usd")
            cost_text = f" · ${cost:.4f}" if isinstance(cost, (int, float)) and cost > 0 else ""
            sec = round((m.get("duration_ms") or 0) / 1000, 1)
            self.console.print(Text(self.t("done", sec=sec, steps=m.get("steps", 0), cost=cost_text), style="dim"))
        elif kind == "run.failed":
            self._flush_text()
            self.console.print(Text(self.t("failed", message=m.get("message", "")), style="red"))
        elif kind == "run.cancelled":
            self._flush_text()
            self.console.print(Text(self.t("cancelled"), style="yellow"))
        elif kind == "log" and m.get("level") in ("warning", "error"):
            self._flush_text()
            self.console.print(Text(str(m.get("text", "")), style="yellow" if m.get("level") == "warning" else "red"))
        elif kind == "steering.queued":
            self.console.print(Text(self.t("steering"), style="dim"))
        elif kind == "reminder.fired":
            self._flush_text()
            self.console.print(Text(self.t("reminder", title=m.get("title") or "", text=m.get("note") or m.get("text") or ""),
                                    style="magenta"))
        elif kind == "model.routed":
            self.console.print(Text(f"  → {m.get('model')}", style="dim"))

    def close(self) -> None:
        self._flush_text()


def json_line(m: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(m, ensure_ascii=False) + "\n")
    sys.stdout.flush()
