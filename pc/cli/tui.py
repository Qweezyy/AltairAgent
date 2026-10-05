"""The interactive terminal: the conversation flows into the terminal's scrollback, the input
stays at the bottom with a status line under it (the model, the approval mode, how full the
context is, the cost), and the agent's questions and approvals are picked with the arrow keys.

One loop owns the screen: it shows the input, and everything that needs the user while a run
goes on (an approval, a question, a secret) is queued to it as a "modal" — the input steps
aside, the picker is shown, and the input comes back with the draft that was in it.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application, get_app_or_none
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.filters import has_completions, is_done
from prompt_toolkit.layout import ConditionalContainer, Dimension, ScrollOffsets
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.layout.menus import CompletionsMenuControl
from prompt_toolkit.layout.processors import AppendAutoSuggestion
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import ANSI, FormattedText, to_formatted_text
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich.console import Console, ConsoleDimensions, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from cli import media, theme, tools_view
from cli.app import MODES, Client
from cli.render import human_tokens, money
from cli.texts import Texts
from core.version import __version__

_MODAL = object()
_EXIT = object()

REASONING = ("adaptive", "low", "medium", "high", "default")

#: /init: what the model is asked (in English, like every prompt the model gets).
INIT_TASK = (
    "Explore this project and write AGENTS.md at its root (update it if it exists): what the project "
    "is, how to install, build, run and test it, the layout of the code, and the conventions a coding "
    "agent must follow here. Short and factual: only what you verified in the files."
)
DEFAULT_CONTEXT = 128_000

#: Folders never offered after '@' (they are huge and never what one means).
_SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    "target",
    ".next",
    ".gradle",
    "logs",
}
_MAX_FILES = 20_000


class _Out:
    """stdout as it is at each write: inside patch_stdout it is the proxy that prints above the
    input, so the output never tears the line being typed."""

    def write(self, s: str) -> int:
        return sys.stdout.write(s) if s else 0

    def flush(self) -> None:
        sys.stdout.flush()

    def isatty(self) -> bool:
        return True


class FitConsole(Console):
    """A console as wide as the terminal is now (it is resized while the agent works)."""

    @property
    def size(self) -> ConsoleDimensions:  # type: ignore[override]
        cols, rows = shutil.get_terminal_size((100, 30))
        return ConsoleDimensions(max(cols - 1, 20), rows)

    @size.setter
    def size(self, value: tuple[int, int]) -> None:  # noqa: ARG002 - the terminal decides
        return None


@dataclass
class Option:
    label: str
    value: Any = None
    description: str = ""
    recommended: bool = False


@dataclass
class Command:
    name: str
    help_key: str
    run: Callable[[str], Awaitable[bool | None]]
    aliases: tuple[str, ...] = ()
    args: Callable[[], Iterable[str]] | None = None
    template: str = ""  # a saved quick command (from the app's settings)
    description: str = ""


@dataclass
class _Modal:
    show: Callable[..., Awaitable[Any]]
    args: tuple[Any, ...]
    done: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())


class TuiClient(Client):
    """The socket client whose approvals and questions are answered through the screen."""

    def __init__(self, *args: Any, tui: Tui, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.tui = tui

    async def _approve(self, m: dict[str, Any]) -> None:
        scope = await self.tui.modal(self.tui.approval, m)
        await self.send({"type": "approval", "request_id": m["request_id"], "scope": scope or "deny"})

    async def _answer(self, m: dict[str, Any]) -> None:
        answers = await self.tui.modal(self.tui.questions, m)
        await self.send({"type": "answer", "request_id": m["request_id"], "answers": answers or {}})

    async def _handoff(self, m: dict[str, Any]) -> None:
        await self.tui.modal(self.tui.handoff, m)
        await self.send({"type": "handoff_done", "request_id": m["request_id"]})

    async def _secret(self, m: dict[str, Any]) -> None:
        await self.tui.modal(self.tui.secret, str(m.get("name") or ""), str(m.get("purpose") or ""))

    async def on_loaded(self, m: dict[str, Any]) -> None:
        await self.tui.on_loaded()


class Tui:
    def __init__(self, backend: Any, args: Any, texts: Texts) -> None:
        self.t = texts
        self.console = FitConsole(file=_Out(), force_terminal=True, highlight=False, soft_wrap=False)
        self.client = TuiClient(backend, args, texts, self.console, tui=self)
        self.r = self.client.render
        self.args = args
        self.http = backend.http
        self.style = Style.from_dict(theme.PT_STYLE)
        self._modals: list[_Modal] = []
        self._draft = ""
        self._last_ctrl_c = 0.0
        self._exit = False
        self._closed = False
        self._files: list[str] | None = None
        self._files_root = ""
        self.settings: dict[str, Any] = {}
        self.providers: list[dict[str, Any]] = []
        self.context_window = DEFAULT_CONTEXT
        self.commands: dict[str, Command] = {}
        self.custom: dict[str, Command] = {}
        self.box: InputBox | None = None
        self._spin = 0
        self._started = False
        self.picking = ""  # the title of the picker on screen ("" = none)
        self._bg: asyncio.Task | None = None
        self.attachments: list[str] = []  # files going with the next message

    # ================================================================ state

    @property
    def running(self) -> bool:
        return self.client.running

    @property
    def mode(self) -> str:
        return str(self.client.mode or "manual")

    @property
    def workspace(self) -> str:
        return str(self.client.workspace or self.args.cwd or os.getcwd())

    @property
    def model(self) -> str:
        return str(
            self.args.model or self.client.session.get("model") or self.settings.get("default_model") or ""
        )

    async def _get(self, path: str, **params: Any) -> Any:
        async with httpx.AsyncClient(timeout=20) as http:
            response = await http.get(f"{self.http}{path}", params=params or None)
            response.raise_for_status()
            return response.json()

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(timeout=20) as http:
            response = await http.post(f"{self.http}{path}", json=payload)
            response.raise_for_status()
            return response.json()

    async def _load_config(self) -> None:
        """Settings, providers and quick commands: what the status line and the pickers show."""
        try:
            self.settings = await self._get("/api/settings")
        except (httpx.HTTPError, ValueError):
            self.settings = {}
        try:
            self.providers = (await self._get("/api/providers")).get("providers") or []
        except (httpx.HTTPError, ValueError):
            self.providers = []
        try:
            cmds = (await self._get("/api/commands", lang=self.t.lang)).get("commands") or []
        except (httpx.HTTPError, ValueError):
            cmds = []
        self.custom = {}
        for c in cmds:
            name = str(c.get("name") or "").strip()
            if name and name not in self.commands:
                self.custom[name] = Command(
                    name,
                    "",
                    self._run_custom,
                    template=str(c.get("template") or ""),
                    description=str(c.get("description") or ""),
                )
        # The catalog may be fetched from the network: the input does not wait for it.
        self._bg = asyncio.create_task(self._fit_context())

    async def _fit_context(self) -> None:
        """The context window of the current model: its entry in the providers list, else the
        catalog, else 128K (the same order the app window uses)."""
        model = self.model
        for p in self.providers:
            for m in p.get("models") or []:
                if m.get("id") == model and int(m.get("context") or 0) > 0:
                    self.context_window = int(m["context"])
                    return
        self.context_window = DEFAULT_CONTEXT
        try:
            catalog = (await self._get("/api/models")).get("models") or []
        except (httpx.HTTPError, ValueError):
            catalog = []
        for m in catalog:
            if m.get("id") == model and int(m.get("context") or 0) > 0:
                self.context_window = int(m["context"])
                return
        self.context_window = DEFAULT_CONTEXT

    # ================================================================ the input line

    def _width(self) -> int:
        app = get_app_or_none()
        if app is not None:
            try:
                return max(app.output.get_size().columns - 1, 20)
            except (OSError, ValueError):
                pass
        return max(shutil.get_terminal_size((100, 30)).columns - 1, 20)

    def _verb(self) -> str:
        phase = self.r.phase or "thinking"
        detail = self.r.detail
        key = {
            "thinking": "st.thinking",
            "reasoning": "st.reasoning",
            "writing": "st.writing",
            "tool": "st.tool",
            "pending": "st.pending",
            "retry": "st.retry",
            "compacting": "st.compacting",
        }.get(phase, "st.thinking")
        return self.t(key, detail=detail) if detail else self.t(key + ".plain")

    def _message(self) -> FormattedText:
        parts: list[tuple[str, str]] = []
        if self.running or self.r.phase == "compacting":
            self._spin = (self._spin + 1) % (len(theme.SPINNER) * 2)
            frame = theme.SPINNER[self._spin // 2]
            elapsed = int(time.monotonic() - self.r.run_started) if self.r.run_started else 0
            facts = [f"{elapsed // 60}m {elapsed % 60:02d}s" if elapsed >= 60 else f"{elapsed}s"]
            if self.r.tokens:
                facts.append(f"↓ {human_tokens(self.r.tokens)}")
            if self.running:
                facts.append(self.t("esc_stop"))
            verb = tools_view.one_line(self._verb(), max(self._width() - 40, 20))
            parts += [
                ("class:spinner", f"{frame} "),
                ("class:verb", verb + "…"),
                ("class:dim", f"  ({' · '.join(facts)})"),
                ("", "\n"),
            ]
        if self.attachments:
            names = "  ·  ".join(Path(a).name for a in self.attachments)
            parts += [
                ("class:verb", f"  ⧉ {names}"),
                ("class:faint", "   " + self.t("detach_hint")),
                ("", "\n"),
            ]
        parts.append(("class:rule", "─" * self._width()))
        return FormattedText(parts)

    def _toolbar(self) -> FormattedText:
        width = self._width()
        key, style = theme.MODE_STYLE.get(self.mode, theme.MODE_STYLE["manual"])
        left: list[tuple[str, str]] = []
        if self.mode != "manual":
            left.append((f"class:{key}", self.t(key)))
            left.append(("class:faint", "  " + self.t("cycle_mode")))
        else:
            left.append(("class:hint", self.t("shortcuts_hint")))
        right: list[tuple[str, str]] = []
        model = self.model
        if model:
            right.append(("class:dim", tools_view.one_line(model, 32)))
        if self.r.ctx_tokens:
            frac = self.r.ctx_tokens / max(self.context_window, 1)
            level = "ctx.full" if frac >= 0.9 else "ctx.warn" if frac >= 0.7 else "ctx.ok"
            right += [("class:faint", " · "), (f"class:{level}", self.t("ctx", pct=round(frac * 100)))]
        spent = self.r.session_usd + (self.r.usd if self.running else 0.0)
        if spent > 0:
            right += [("class:faint", " · "), ("class:dim", money(spent))]
        used = sum(len(s) for _, s in left) + sum(len(s) for _, s in right)
        pad = max(width - used - 1, 1)
        return FormattedText([("class:rule", "─" * width), ("", "\n "), *left, ("", " " * pad), *right])

    def _bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("enter")
        def _(event: KeyPressEvent) -> None:
            buf = event.current_buffer
            state = buf.complete_state
            if state and state.current_completion:
                buf.apply_completion(state.current_completion)
                if not buf.text.startswith("/") or " " in buf.text:
                    return
            if buf.document.text_before_cursor.endswith("\\"):
                buf.delete_before_cursor(1)
                buf.insert_text("\n")
                return
            buf.validate_and_handle()

        @kb.add("escape", "enter")
        @kb.add("c-j")
        def _(event: KeyPressEvent) -> None:
            event.current_buffer.insert_text("\n")

        @kb.add("escape")
        def _(event: KeyPressEvent) -> None:
            buf = event.current_buffer
            if buf.complete_state:
                buf.cancel_completion()
            elif self.running:
                asyncio.ensure_future(self._stop())
            elif buf.text:
                buf.reset()

        @kb.add("c-c")
        def _(event: KeyPressEvent) -> None:
            buf = event.current_buffer
            now = time.monotonic()
            if buf.text:
                buf.reset()
            elif self.running:
                asyncio.ensure_future(self._stop())
            elif now - self._last_ctrl_c < 2.0:
                event.app.exit(result=_EXIT)
            else:
                self._last_ctrl_c = now
                self.r.print(Text(self.t("again_to_exit"), style=theme.FAINT))

        @kb.add("c-d")
        def _(event: KeyPressEvent) -> None:
            if not event.current_buffer.text:
                event.app.exit(result=_EXIT)
            else:
                event.current_buffer.delete()

        @kb.add("s-tab")
        def _(event: KeyPressEvent) -> None:
            nxt = MODES[(MODES.index(self.mode) + 1) % len(MODES)] if self.mode in MODES else "auto"
            asyncio.ensure_future(self.client.send({"type": "set_mode", "mode": nxt}))
            self.client.mode = nxt

        @kb.add("c-t")
        def _(event: KeyPressEvent) -> None:
            self.r.thinking = not self.r.thinking
            self.r.print(
                Text(self.t("thinking_on" if self.r.thinking else "thinking_off"), style=theme.FAINT)
            )

        @kb.add("c-l")
        def _(event: KeyPressEvent) -> None:
            event.app.renderer.clear()

        @kb.add("escape", "v")
        @kb.add("c-v")
        def _(event: KeyPressEvent) -> None:
            # The terminal itself pastes text (Ctrl+V / right click arrive as text); what comes
            # here is the key, so the clipboard is checked for an image or copied files.
            self.paste_files()

        @kb.add("?")
        def _(event: KeyPressEvent) -> None:
            if event.current_buffer.text:
                event.current_buffer.insert_text("?")
            else:
                self._print_shortcuts()

        return kb

    def _make_input(self) -> InputBox:
        try:
            history: Any = FileHistory(str(Path(self.client.app_dir) / "cli_history.txt"))
        except OSError:
            history = InMemoryHistory()
        return InputBox(self, history)

    # ================================================================ modals and pickers

    async def modal(self, show: Callable[..., Awaitable[Any]], *args: Any) -> Any:
        """Asks the screen loop to show `show(*args)` and waits for its result."""
        item = _Modal(show, args)
        self._modals.append(item)
        app = self.box.app if self.box else None
        if app is not None and app.is_running:
            self._draft = self.box.buffer.text
            app.exit(result=_MODAL)
        return await item.done

    async def _run_modals(self) -> None:
        while self._modals:
            item = self._modals.pop(0)
            try:
                result = await item.show(*item.args)
            except (EOFError, KeyboardInterrupt):
                result = None
            if not item.done.done():
                item.done.set_result(result)

    def _ansi(self, renderables: list[Any]) -> ANSI:
        """Rich renderables as prompt_toolkit text: a picker shows what it asks about inside
        itself, so the preview leaves the screen together with the picker."""
        import io

        buf = io.StringIO()
        Console(
            file=buf,
            force_terminal=True,
            color_system="truecolor",
            width=self._width(),
            highlight=False,
            soft_wrap=False,
        ).print(Group(*renderables))  # Group: print(a, b) would join them with a space
        return ANSI(buf.getvalue())

    async def select(
        self,
        title: str,
        options: list[Option],
        *,
        multi: bool = False,
        allow_text: bool = False,
        default: int = 0,
        preview: list[Any] | None = None,
        own: str = "",
    ) -> Any:
        """A picker under the conversation: arrows (or j/k) move, Enter picks, digits pick at once,
        Esc cancels (None). `multi`: Space ticks, Enter confirms a list. `preview`: rich
        renderables shown above the question."""
        opts = list(options)
        shown = self._ansi(preview) if preview else None
        if allow_text:
            opts.append(Option(own or self.t("type_own"), _TYPE_OWN))
        state = {"i": max(0, min(default, len(opts) - 1)), "checked": set()}
        kb = KeyBindings()

        def text() -> FormattedText:
            parts: list[tuple[str, str]] = list(to_formatted_text(shown)) if shown else []
            parts.append(("class:select.title", f" {title}\n"))
            for n, o in enumerate(opts):
                cur = n == state["i"]
                parts.append(("class:select.cursor", f" {'❯' if cur else ' '} "))
                if multi and o.value is not _TYPE_OWN:
                    mark = "◉" if n in state["checked"] else "○"
                    parts.append(
                        ("class:select.checked" if n in state["checked"] else "class:dim", f"{mark} ")
                    )
                parts.append(("class:select.keys", f"{n + 1}. " if n < 9 else "   "))
                parts.append(("class:select.current" if cur else "class:select.option", o.label))
                if o.recommended:
                    parts.append(("class:verb", " ★"))
                if o.description:
                    parts.append(("class:select.desc", f"  {o.description}"))
                parts.append(("", "\n"))
            keys = self.t("select_keys_multi" if multi else "select_keys")
            parts.append(("class:select.keys", f" {keys}"))
            return FormattedText(parts)

        @kb.add("up")
        @kb.add("k")
        @kb.add("s-tab")
        def _(event: KeyPressEvent) -> None:
            state["i"] = (state["i"] - 1) % len(opts)

        @kb.add("down")
        @kb.add("j")
        @kb.add("tab")
        def _(event: KeyPressEvent) -> None:
            state["i"] = (state["i"] + 1) % len(opts)

        @kb.add(" ")
        def _(event: KeyPressEvent) -> None:
            if multi and opts[state["i"]].value is not _TYPE_OWN:
                state["checked"] ^= {state["i"]}

        @kb.add("enter")
        def _(event: KeyPressEvent) -> None:
            chosen = opts[state["i"]]
            if multi and chosen.value is not _TYPE_OWN:
                picked = sorted(state["checked"]) or [state["i"]]
                event.app.exit(result=[opts[n] for n in picked])
            else:
                event.app.exit(result=chosen)

        for digit in range(1, min(len(opts), 9) + 1):

            @kb.add(str(digit))
            def _(event: KeyPressEvent, n: int = digit - 1) -> None:
                if multi and opts[n].value is not _TYPE_OWN:
                    state["checked"] ^= {n}
                    state["i"] = n
                else:
                    event.app.exit(result=opts[n])

        @kb.add("escape", eager=True)
        @kb.add("c-c")
        def _(event: KeyPressEvent) -> None:
            event.app.exit(result=None)

        app: Application = Application(
            layout=Layout(
                Window(
                    FormattedTextControl(text, focusable=True, show_cursor=False),
                    dont_extend_height=True,
                    wrap_lines=True,
                )
            ),
            key_bindings=kb,
            style=self.style,
            full_screen=False,
            erase_when_done=True,
            mouse_support=False,
        )
        self.picking = title
        try:
            result = await app.run_async()
        finally:
            self.picking = ""
        if result is None:
            return None
        if isinstance(result, Option) and result.value is _TYPE_OWN:
            typed = await self.ask_text((own or self.t("type_own")).rstrip("…") + ": ")
            return Option(typed, typed) if typed else None
        return result

    async def ask_text(self, message: str, *, secret: bool = False) -> str:
        session: PromptSession = PromptSession(style=self.style, erase_when_done=True)
        try:
            return (await session.prompt_async([("class:prompt", message)], is_password=secret)).strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    async def approval(self, m: dict[str, Any]) -> str:
        name = str(m.get("name") or "")
        args = m.get("args") or {}
        self.r.pause()
        start = 1
        if name == "edit_file":
            start = tools_view.line_of(
                self.workspace, str(args.get("path") or ""), str(args.get("old_text") or "")
            )
        shown: list[Any] = [
            Text(""),
            tools_view.header(name, args, None),
            *tools_view.preview(name, args, self.t, start),
        ]
        reason = str(m.get("reason") or "").strip()
        command = str(args.get("command") or "")
        if reason and not (command and command in reason):
            shown.append(Text(f"     {reason}", style=theme.MUTED))
        shown.append(Text(""))
        options = [
            Option(self.t("ap.once"), "once"),
            Option(self.t("ap.project"), "project", self.t("ap.project_desc")),
            Option(self.t("ap.global"), "global", self.t("ap.global_desc")),
            Option(self.t("ap.deny"), "deny", self.t("ap.deny_desc")),
        ]
        label, hint = tools_view.label_of(name, args)
        picked = await self.select(self.t("ap.title", name=label), options, preview=shown)
        scope = picked.value if isinstance(picked, Option) else "deny"
        line = Text("  ")
        line.append("✗ " if scope == "deny" else "✓ ", style=theme.ERR if scope == "deny" else theme.OK)
        line.append(self.t(f"ap.done.{scope}"), style=theme.MUTED)
        line.append(f" · {label}" + (f"({hint})" if hint else ""), style=theme.FAINT)
        self.r.print(line, gap=True)
        return scope

    async def questions(self, m: dict[str, Any]) -> dict[str, list[str]]:
        self.r.pause()
        answers: dict[str, list[str]] = {}
        for i, q in enumerate(m.get("questions") or []):
            options = [
                Option(
                    str(o.get("label") or ""),
                    str(o.get("label") or ""),
                    str(o.get("description") or ""),
                    bool(o.get("recommended")),
                )
                for o in q.get("options") or []
            ]
            multi = q.get("kind") in ("multiple", "ranking")
            default = next((n for n, o in enumerate(options) if o.recommended), 0)
            picked = await self.select(
                str(q.get("question") or ""), options, multi=multi, allow_text=True, default=default
            )
            if picked is None:
                labels: list[str] = []
            elif isinstance(picked, list):
                labels = [o.label for o in picked]
            else:
                labels = [picked.label]
            answers[str(i)] = labels
            line = Text(f"{theme.DOT} ", style=theme.GOLD).append(str(q.get("question") or ""), style="bold")
            self.r.print(line, gap=True)
            self.r.print(
                Text(
                    f"  {theme.ELBOW}  " + (", ".join(labels) or self.t("no_answer")),
                    style=theme.MUTED if labels else theme.WARN,
                )
            )
        return answers

    async def handoff(self, m: dict[str, Any]) -> None:
        self.r.pause()
        self.r.print(
            Text(f"{theme.DOT} " + self.t("handoff", reason=m.get("reason") or ""), style=theme.WARN),
            gap=True,
        )
        if m.get("hint"):
            self.r.print(Text(f"  {theme.ELBOW}  {m['hint']}", style=theme.MUTED))
        await self.select(self.t("handoff_title"), [Option(self.t("handoff_done"), True)])

    async def secret(self, name: str, purpose: str = "") -> None:
        self.r.pause()
        self.r.print(Text(f"{theme.DOT} " + self.t("secret_asked", name=name), style=theme.GOLD), gap=True)
        if purpose:
            self.r.print(Text(f"  {theme.ELBOW}  {purpose}", style=theme.MUTED))
        value = await self.ask_text(self.t("secret_prompt", name=name), secret=True)
        if not value:
            self.r.print(Text(f"  {theme.ELBOW}  " + self.t("secret_skipped", name=name), style=theme.FAINT))
            return
        try:
            await self._post("/api/secrets", {"name": name, "value": value, "workspace": self.workspace})
            self.r.print(Text(f"  {theme.ELBOW}  " + self.t("secret_saved", name=name), style=theme.OK))
        except httpx.HTTPError as exc:
            self.r.print(Text(f"  ! {exc}", style=theme.ERR))

    # ================================================================ the conversation

    def welcome(self, profile_name: str = "") -> None:
        star = Text("\n  ╷\n──✦──\n  ╵", style=f"bold {theme.GOLD}", justify="center")
        info = Table.grid(padding=(0, 2))
        info.add_column(style=theme.MUTED, no_wrap=True)
        info.add_column()
        hello = self.t("welcome_name", name=profile_name) if profile_name else self.t("welcome")
        info.add_row("", Text(hello, style="bold"))
        info.add_row(self.t("w.model"), Text(self.model or "—", style=theme.GOLD))
        info.add_row(self.t("w.folder"), self.workspace)
        title = self.chat_title()
        if title:
            info.add_row(self.t("w.chat"), Text(title))
        grid = Table.grid(padding=(0, 3))
        grid.add_column(width=7)
        grid.add_column()
        grid.add_row(star, info)
        tips = Text(self.t("tips"), style=theme.FAINT)
        panel = Panel(
            Group(grid, Text(""), tips),
            title=Text(f" {theme.STAR} Altair {__version__} ", style=f"bold {theme.GOLD}"),
            title_align="left",
            border_style=theme.GOLD_DIM,
            padding=(0, 2),
            expand=False,
        )
        self.console.print(panel)
        warnings = [w for w in (self.client.warnings or []) if w]
        for w in warnings[:3]:
            self.console.print(Text(f"  ! {w}", style=theme.WARN))
        self.console.print()

    def echo_user(self, text: str, attachments: list[str] | None = None) -> None:
        self.r.pause()
        lines = text.splitlines() or [""]
        out = Text(f"{theme.PROMPT} ", style=f"bold {theme.GOLD}")
        out.append(lines[0], style="bold")
        for line in lines[1:]:
            out.append("\n  " + line, style="bold")
        self.r.print(out, gap=True)
        for path in attachments or []:
            line = Text(f"  {theme.ELBOW}  ⧉ ", style=theme.FAINT)
            line.append(Path(path).name, style=theme.MUTED)
            self.r.print(line)

    def attach(self, paths: list[str]) -> None:
        for path in paths:
            if path not in self.attachments:
                self.attachments.append(path)

    def paste_files(self) -> None:
        found = media.clipboard_files(Path(self.client.app_dir) / "cli_paste")
        if not found:
            self.r.print(Text("  " + self.t("clipboard_empty"), style=theme.FAINT))
            return
        self.attach(found)
        for path in found:
            if Path(path).suffix.lower() in media.IMAGE_SUFFIXES:
                thumb = media.thumbnail(path, cols=36, rows=10)
                if thumb is not None:
                    self.r.print(thumb, gap=True)

    def show_history(self, everything: bool = False) -> None:
        entries = [
            e for e in (self.client.session.get("timeline") or []) if e.get("kind") in ("user", "answer")
        ]
        if not entries:
            return
        shown = entries if everything else entries[-8:]
        if len(entries) > len(shown):
            self.r.print(Text(self.t("earlier", n=len(entries) - len(shown)), style=theme.FAINT), gap=True)
        for e in shown:
            if e.get("kind") == "user":
                self.echo_user(str(e.get("text") or ""))
            else:
                self.r.markdown(str(e.get("full") or e.get("text") or ""), True)
        self.r.print(Text("─" * 12, style=theme.FAINT), gap=True)

    def chat_title(self) -> str:
        """The chat's title; a chat not named yet is "New chat" (the backend's default is Russian)."""
        title = str(self.client.session.get("title") or "").strip()
        return self.t("new_chat") if title in ("", "Новый диалог", "New chat") else title

    async def on_loaded(self) -> None:
        """Another chat was opened (/new, /resume): its name and the end of its conversation."""
        self.r.workspace = self.workspace
        self.r.media_dir = Path(self.client.app_dir) / "cli_widgets"
        if not self._started:
            return
        self.r.session_usd = 0.0
        self.r.ctx_tokens = int(self.client.session.get("context_tokens") or 0)
        title = self.chat_title()
        line = Text(f"{theme.STAR} ", style=theme.GOLD).append(title or self.t("new_chat"), style="bold")
        line.append(f"  {self.client.session.get('id', '')}", style=theme.FAINT)
        self.r.print(line, gap=True)
        self.show_history()
        await self._fit_context()

    async def _stop(self) -> None:
        await self.client.send({"type": "stop"})

    async def submit(self, text: str) -> None:
        if self.running:
            self.echo_user(text)
            await self.client.send({"type": "run", "task": text})  # a hint to the running task
            if self.attachments:
                self.r.print(Text("  " + self.t("attach_later"), style=theme.FAINT))
            return
        # '@file' mentions and dropped paths go along as attachments: the agent gets the file
        # itself (a picture to look at, a document's text), not only its name.
        self.attach(media.mentioned_files(text, self.workspace))
        files, self.attachments = self.attachments, []
        self.echo_user(text, files)
        self.r.phase, self.r.run_started, self.r.tokens = "thinking", time.monotonic(), 0
        self.client.running = True
        payload: dict[str, Any] = {"type": "run", "task": text}
        if files:
            payload["options"] = {"attachments": files}
        if self.args.model:
            payload["model"] = self.args.model
        await self.client.send(payload)

    async def _pump(self) -> None:
        """Every event from the backend, as it comes; the input stays usable meanwhile."""
        while True:
            m = await self.client.events.get()
            if m.get("type") == "_closed":
                self._closed = True
                self.r.print(Text(self.t("closed"), style=theme.ERR), gap=True)
                app = self.box.app if self.box else None
                if app is not None and app.is_running:
                    app.exit(result=_EXIT)
                return
            try:
                await self.client.handle(m)
            except (KeyError, ValueError, TypeError) as exc:
                self.r.print(Text(f"  ! {exc}", style=theme.ERR))

    # ================================================================ commands

    def _register(self) -> None:
        def add(
            name: str,
            run: Callable[[str], Awaitable[bool | None]],
            *aliases: str,
            args: Callable[[], Iterable[str]] | None = None,
        ) -> None:
            self.commands[name] = Command(name, f"c.{name}", run, aliases, args)

        add("help", self.c_help, "?")
        add("new", self.c_new, "clear")
        add("resume", self.c_resume, "chats")
        add("rename", self.c_rename)
        add("model", self.c_model, args=self._model_ids)
        add("mode", self.c_mode, args=lambda: MODES)
        add("reasoning", self.c_reasoning, args=lambda: REASONING)
        add("thinking", self.c_thinking)
        add("context", self.c_context)
        add("cost", self.c_cost)
        add("diff", self.c_diff)
        add("undo", self.c_undo)
        add("init", self.c_init)
        add("memory", self.c_memory)
        add("skills", self.c_skills)
        add("mcp", self.c_mcp)
        add("secret", self.c_secret)
        add("out", self.c_out)
        add("open", self.c_open)
        add("attach", self.c_attach, args=self._attach_args)
        add("detach", self.c_detach)
        add("compact", self.c_compact)
        add("history", self.c_history)
        add("stop", self.c_stop)
        add("exit", self.c_exit, "quit", "q")

    def lookup(self, name: str) -> Command | None:
        name = name.lower()
        for c in self.commands.values():
            if name == c.name or name in c.aliases:
                return c
        return self.custom.get(name)

    def all_commands(self) -> list[Command]:
        return [*self.commands.values(), *self.custom.values()]

    def command_help(self, c: Command) -> str:
        return c.description if c.template else self.t(c.help_key)

    async def command(self, line: str) -> bool:
        """A slash command; True = exit."""
        name, _, rest = line[1:].partition(" ")
        c = self.lookup(name)
        if c is None:
            self.r.print(Text(self.t("unknown_cmd", name=name), style=theme.WARN), gap=True)
            return False
        try:
            return bool(
                await c.run(rest.strip()) if not c.template else await self._run_custom_cmd(c, rest.strip())
            )
        except httpx.HTTPError as exc:
            self.r.print(Text(f"  ! {exc}", style=theme.ERR))
            return False

    async def _run_custom(self, rest: str) -> bool:  # registered on Command; replaced per call
        return False

    async def _run_custom_cmd(self, c: Command, rest: str) -> bool:
        template = c.template
        if any(p in template for p in ("{{input}}", "{{ввод}}")):
            task = template.replace("{{input}}", rest).replace("{{ввод}}", rest)
        else:
            task = f"{template}\n\n{rest}".strip()
        await self.submit(task)
        return False

    def _print_shortcuts(self) -> None:
        rows = Table.grid(padding=(0, 3))
        rows.add_column(style=theme.GOLD, no_wrap=True)
        rows.add_column(style=theme.MUTED)
        for keys, desc in self.t.pairs("shortcuts"):
            rows.add_row(keys, desc)
        self.r.print(rows, gap=True)

    async def c_help(self, rest: str) -> bool:
        table = Table.grid(padding=(0, 3))
        table.add_column(style=theme.GOLD, no_wrap=True)
        table.add_column(style=theme.MUTED)
        for c in self.commands.values():
            names = "/" + c.name + ("".join(f", /{a}" for a in c.aliases if a != "?"))
            table.add_row(names, self.t(c.help_key))
        if self.custom:
            table.add_row("", "")
            for c in self.custom.values():
                table.add_row("/" + c.name, c.description)
        self.r.print(Text(self.t("commands"), style="bold"), gap=True)
        self.r.print(table)
        self.r.print(Text(self.t("shortcuts_title"), style="bold"), gap=True)
        self._print_shortcuts()
        return False

    async def c_new(self, rest: str) -> bool:
        self.args.cont, self.args.resume = False, None
        await self.client.send({"type": "new_session", "workspace": self.workspace})
        return False

    async def c_resume(self, rest: str) -> bool:
        chats = await self.client.chats()
        if not chats:
            self.r.print(Text(self.t("no_chats"), style=theme.MUTED), gap=True)
            return False
        chosen = None
        if rest:
            q = rest.lower()
            chosen = next((c for c in chats if str(c.get("id", "")).startswith(q)), None) or next(
                (c for c in chats if q in str(c.get("title") or "").lower()), None
            )
            if chosen is None:
                self.r.print(Text(self.t("not_found", query=rest), style=theme.WARN), gap=True)
                return False
        else:
            options = []
            for c in chats[:30]:
                when = time.strftime("%d.%m %H:%M", time.localtime(c.get("updated_at") or 0))
                busy = f" · {self.t('running_bg')}" if c.get("running") else ""
                options.append(
                    Option(tools_view.one_line(c.get("title") or c.get("id"), 60), c, f"{when}{busy}")
                )
            picked = await self.select(self.t("pick_chat"), options)
            if picked is None:
                return False
            chosen = picked.value
        await self.client.send({"type": "load_session", "session_id": chosen["id"]})
        return False

    async def c_rename(self, rest: str) -> bool:
        title = rest or await self.ask_text(self.t("rename_prompt"))
        if title:
            await self.client.send(
                {"type": "rename_session", "session_id": self.client.session.get("id"), "title": title}
            )
            self.client.session["title"] = title
            self.r.print(Text(f"  {theme.ELBOW}  " + self.t("renamed", title=title), style=theme.MUTED))
        return False

    def _model_ids(self) -> list[str]:
        ids: list[str] = []
        for p in self.providers:
            ids += [str(m.get("id")) for m in p.get("models") or [] if m.get("id")]
        for v in [
            self.settings.get("default_model"),
            *[
                t.get("model")
                for t in (self.settings.get("model_tiers") or {}).values()
                if isinstance(t, dict)
            ],
        ]:
            if v and v not in ids:
                ids.append(str(v))
        return ids

    async def c_model(self, rest: str) -> bool:
        model = rest
        if not model:
            ids = self._model_ids()
            options = [Option(i, i, self.t("current") if i == self.model else "") for i in ids]
            default = next((n for n, o in enumerate(options) if o.value == self.model), 0)
            picked = await self.select(
                self.t("pick_model"), options, allow_text=True, default=default, own=self.t("other_model")
            )
            if picked is None:
                return False
            model = str(picked.value)
        self.args.model = model
        await self._fit_context()
        self.r.print(Text(f"  {theme.ELBOW}  " + self.t("model_set", model=model), style=theme.MUTED))
        return False

    async def c_mode(self, rest: str) -> bool:
        mode = rest if rest in MODES else ""
        if not mode:
            options = [
                Option(self.t(theme.MODE_STYLE[m][0]), m, self.t(f"{theme.MODE_STYLE[m][0]}.desc"))
                for m in MODES
            ]
            picked = await self.select(
                self.t("pick_mode"), options, default=MODES.index(self.mode) if self.mode in MODES else 0
            )
            if picked is None:
                return False
            mode = picked.value
        await self.client.send({"type": "set_mode", "mode": mode})
        self.client.mode = mode
        return False

    async def c_reasoning(self, rest: str) -> bool:
        level = rest if rest in REASONING else ""
        current = str(self.settings.get("llm_reasoning") or "adaptive")
        if not level:
            options = [Option(self.t(f"r.{r}"), r, self.t(f"r.{r}.desc")) for r in REASONING]
            picked = await self.select(
                self.t("pick_reasoning"),
                options,
                default=REASONING.index(current) if current in REASONING else 0,
            )
            if picked is None:
                return False
            level = picked.value
        await self._post("/api/settings", {"llm_reasoning": level})
        self.settings["llm_reasoning"] = level
        self.r.print(
            Text(
                f"  {theme.ELBOW}  " + self.t("reasoning_set", level=self.t(f"r.{level}")), style=theme.MUTED
            )
        )
        return False

    async def c_thinking(self, rest: str) -> bool:
        self.r.thinking = not self.r.thinking
        self.r.print(
            Text(self.t("thinking_on" if self.r.thinking else "thinking_off"), style=theme.FAINT), gap=True
        )
        return False

    async def c_context(self, rest: str) -> bool:
        used, total = self.r.ctx_tokens, self.context_window
        frac = min(used / max(total, 1), 1.0)
        width = 30
        filled = round(frac * width)
        bar = Text("  ")
        bar.append(
            "█" * filled, style=theme.ERR if frac >= 0.9 else theme.WARN if frac >= 0.7 else theme.GOLD
        )
        bar.append("░" * (width - filled), style=theme.FAINT)
        bar.append(
            f"  {human_tokens(used)} / {human_tokens(total)}  ({round(frac * 100)}%)", style=theme.MUTED
        )
        self.r.print(Text(self.t("context_title"), style="bold"), gap=True)
        self.r.print(bar)
        self.r.print(Text("  " + self.t("context_note"), style=theme.FAINT))
        return False

    async def c_cost(self, rest: str) -> bool:
        sid = self.client.session.get("id")
        total, runs, tokens = 0.0, 0, 0
        try:
            data = await self._get(f"/api/sessions/{sid}")
            for e in (data.get("session") or data).get("timeline") or []:
                if e.get("kind") == "answer":
                    runs += 1
                    total += float(e.get("cost_usd") or 0.0)
                    usage = e.get("usage") or {}
                    tokens += int(usage.get("total_tokens") or 0) or int(
                        usage.get("prompt_tokens") or 0
                    ) + int(usage.get("completion_tokens") or 0)
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        self.r.print(Text(self.t("cost_title"), style="bold"), gap=True)
        self.r.print(
            Text(
                "  " + self.t("cost_chat", usd=money(total) or "$0", runs=runs, tokens=human_tokens(tokens)),
                style=theme.MUTED,
            )
        )
        self.r.print(
            Text("  " + self.t("cost_here", usd=money(self.r.session_usd) or "$0"), style=theme.MUTED)
        )
        return False

    async def c_diff(self, rest: str) -> bool:
        data = await self._get("/api/git/diff", workspace=self.workspace)
        if not data.get("available"):
            self.r.print(Text(f"  {data.get('reason') or self.t('no_git')}", style=theme.MUTED), gap=True)
            return False
        diff = str(data.get("diff") or "")
        untracked = data.get("untracked") or []
        if not diff and not untracked:
            self.r.print(Text(f"{theme.DOT} " + self.t("clean_tree"), style=theme.OK), gap=True)
            return False
        self.r.print(
            Text(f"{theme.DOT} " + self.t("diff_title", branch=data.get("branch") or ""), style="bold"),
            gap=True,
        )
        rows = tools_view.patch_rows(diff)
        for line in tools_view.render_diff(rows, self.t, limit=len(rows) or 1):
            self.r.print(line)
        for path in untracked:
            self.r.print(Text(f"     + {path}", style=theme.OK))
        return False

    async def c_undo(self, rest: str) -> bool:
        run_id = self.r.last_run_id
        if not run_id:
            self.r.print(Text(self.t("nothing_to_undo"), style=theme.MUTED), gap=True)
            return False
        picked = await self.select(
            self.t("undo_title"),
            [Option(self.t("undo_yes"), True), Option(self.t("undo_no"), False)],
            default=1,
        )
        if picked is not None and picked.value:
            await self.client.send({"type": "rollback_run", "run_id": run_id})
        return False

    async def c_init(self, rest: str) -> bool:
        await self.submit(INIT_TASK + (f"\n\n{rest}" if rest else ""))
        return False

    async def c_memory(self, rest: str) -> bool:
        facts = (await self._get("/api/memory")).get("facts") or []
        self.r.print(Text(self.t("memory_title", n=len(facts)), style="bold"), gap=True)
        for f in facts:
            self.r.print(
                Text(f"  · {f.get('title') or tools_view.one_line(f.get('text'), 90)}", style=theme.MUTED)
            )
        return False

    async def c_skills(self, rest: str) -> bool:
        skills = (await self._get("/api/skills")).get("skills") or []
        self.r.print(Text(self.t("skills_title", n=len(skills)), style="bold"), gap=True)
        for s in skills:
            line = Text(f"  {s.get('name')}", style=theme.GOLD)
            if s.get("description"):
                line.append("  " + tools_view.one_line(s["description"], 90), style=theme.MUTED)
            self.r.print(line)
        return False

    async def c_mcp(self, rest: str) -> bool:
        servers = (await self._get("/api/mcp")).get("servers") or []
        self.r.print(Text(self.t("mcp_title", n=len(servers)), style="bold"), gap=True)
        for s in servers:
            ok = s.get("connected") or s.get("status") == "connected"
            line = Text(f"  {theme.DOT} ", style=theme.OK if ok else theme.FAINT)
            line.append(str(s.get("name")))
            tools = s.get("tools")
            count = len(tools) if isinstance(tools, list) else tools
            if count:
                line.append(f"  {self.t('mcp_tools', n=count)}", style=theme.MUTED)
            if s.get("error"):
                line.append("  " + tools_view.one_line(s["error"], 80), style=theme.ERR)
            self.r.print(line)
        return False

    async def c_secret(self, rest: str) -> bool:
        name = rest or await self.ask_text(self.t("secret_name"))
        if name:
            await self.secret(name.strip().upper())
        return False

    async def c_out(self, rest: str) -> bool:
        tool = self.r.last_tool
        if not tool:
            self.r.print(Text(self.t("no_tool_yet"), style=theme.MUTED), gap=True)
            return False
        self.r.print(tools_view.header(tool["name"], tool["args"], tool["ok"]), gap=True)
        self.r.print(Text(tool["output"] or self.t("tool.no_output"), style=theme.MUTED))
        return False

    async def c_open(self, rest: str) -> bool:
        target = media.absolute(rest, self.workspace) if rest else self.r.last_shown
        if target is None or not target.exists():
            self.r.print(Text(self.t("nothing_to_open"), style=theme.MUTED), gap=True)
            return False
        ok = await asyncio.to_thread(media.open_path, target)
        self.r.print(
            Text(
                f"  {theme.ELBOW}  " + self.t("opened" if ok else "open_failed", path=target.name),
                style=theme.MUTED if ok else theme.WARN,
            )
        )
        return False

    def _attach_args(self) -> list[str]:
        return self.files()[:2000]

    async def c_attach(self, rest: str) -> bool:
        if not rest:
            self.paste_files()
            return False
        paths = media.mentioned_files("@" + rest if not rest.startswith("@") else rest, self.workspace)
        if not paths:
            self.r.print(Text(self.t("not_a_file", path=rest), style=theme.WARN), gap=True)
            return False
        self.attach(paths)
        return False

    async def c_detach(self, rest: str) -> bool:
        self.attachments = []
        return False

    async def c_compact(self, rest: str) -> bool:
        if self.running:
            self.r.print(Text(self.t("compact_busy"), style=theme.WARN), gap=True)
            return False
        self.r.phase, self.r.run_started = "compacting", time.monotonic()
        await self.client.send({"type": "compact", "focus": rest})
        return False

    async def c_history(self, rest: str) -> bool:
        sid = self.client.session.get("id")
        data = await self._get(f"/api/sessions/{sid}")
        self.client.session = data.get("session") or data
        self.show_history(everything=True)
        return False

    async def c_stop(self, rest: str) -> bool:
        if self.running:
            await self._stop()
        return False

    async def c_exit(self, rest: str) -> bool:
        return True

    # ================================================================ files for '@'

    def files(self) -> list[str]:
        root = self.workspace
        if self._files is not None and self._files_root == root:
            return self._files
        found: list[str] = []
        for base, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
            rel = os.path.relpath(base, root)
            for n in names:
                found.append(n if rel == "." else os.path.join(rel, n).replace("\\", "/"))
                if len(found) >= _MAX_FILES:
                    break
            if len(found) >= _MAX_FILES:
                break
        self._files, self._files_root = found, root
        return found

    # ================================================================ the loop

    async def run(self, first: str | None) -> int:
        self._register()
        await self._load_config()
        profile = ""
        try:
            profile = str((await self._get("/api/profile")).get("name") or "")
        except (httpx.HTTPError, ValueError):
            pass
        self.box = self._make_input()
        with patch_stdout(raw=True):
            self.r.ctx_tokens = int(self.client.session.get("context_tokens") or 0)
            self.r.workspace = self.workspace
            self.r.media_dir = Path(self.client.app_dir) / "cli_widgets"
            self.welcome(profile)
            self.show_history()
            self._started = True
            pump = asyncio.create_task(self._pump())
            try:
                if self.client.running:
                    self.r.run_started = time.monotonic()
                pending = first
                while not self._exit and not self._closed:
                    await self._run_modals()
                    if pending:
                        text, pending = pending, None
                    else:
                        try:
                            text = await self.box.read(self._draft)
                        except (EOFError, KeyboardInterrupt):
                            break
                    if text is _MODAL:
                        continue
                    self._draft = ""
                    if text is _EXIT:
                        break
                    text = str(text).strip()
                    if not text:
                        continue
                    if text.startswith("/") and self.lookup(text[1:].partition(" ")[0]) is not None:
                        if await self.command(text):
                            break
                        continue
                    if text.startswith("/") and " " not in text and len(text) > 1:
                        self.r.print(Text(self.t("unknown_cmd", name=text[1:]), style=theme.WARN), gap=True)
                        continue
                    await self.submit(text)
            finally:
                pump.cancel()
                if self._bg is not None:
                    self._bg.cancel()
                await asyncio.gather(pump, *([self._bg] if self._bg else []), return_exceptions=True)
                for item in self._modals:
                    if not item.done.done():
                        item.done.set_result(None)
        return 0


_TYPE_OWN = object()


class InputBox:
    """The input with what surrounds it, top to bottom: what the agent is doing, a rule, the
    input itself, the completions, a rule and the status line — all right under the
    conversation (a prompt_toolkit PromptSession would pin its toolbar to the screen's bottom)."""

    def __init__(self, tui: Tui, history: Any) -> None:
        self.tui = tui
        self.buffer = Buffer(
            multiline=True,
            history=history,
            auto_suggest=AutoSuggestFromHistory(),
            completer=_Completer(tui),
            complete_while_typing=True,
            accept_handler=self._accept,
        )

        def prefix(line: int, wrap: int) -> list[tuple[str, str]]:
            return [("class:prompt", f"{theme.PROMPT} ")] if line == 0 and wrap == 0 else [("", "  ")]

        kb = tui._bindings()

        @kb.add("c-x", "c-e")
        def _(event: KeyPressEvent) -> None:
            event.current_buffer.open_in_editor()

        self.app: Application = Application(
            layout=Layout(
                HSplit(
                    [
                        Window(FormattedTextControl(tui._message), dont_extend_height=True),
                        Window(
                            BufferControl(self.buffer, input_processors=[AppendAutoSuggestion()]),
                            get_line_prefix=prefix,
                            dont_extend_height=True,
                            wrap_lines=True,
                        ),
                        ConditionalContainer(
                            Window(
                                CompletionsMenuControl(),
                                height=self._menu_height,
                                scroll_offsets=ScrollOffsets(top=1, bottom=1),
                                right_margins=[ScrollbarMargin(display_arrows=True)],
                                dont_extend_width=True,
                                style="class:completion-menu",
                            ),
                            filter=has_completions & ~is_done,
                        ),
                        Window(FormattedTextControl(tui._toolbar), dont_extend_height=True),
                    ]
                )
            ),
            key_bindings=kb,
            style=tui.style,
            include_default_pygments_style=False,
            full_screen=False,
            erase_when_done=True,
            refresh_interval=0.12,
            mouse_support=False,
        )
        # Alt+key arrives as Esc + key: a lone Esc (stop the agent) is told apart by a short
        # pause after it — short enough not to feel like a delay.
        self.app.ttimeoutlen = 0.12

    def _menu_height(self) -> Dimension:
        """As many rows as there are completions (up to 8): no empty rows under a short list."""
        state = self.buffer.complete_state
        n = len(state.completions) if state else 0
        return Dimension.exact(max(1, min(n, 8)))

    def _accept(self, buf: Buffer) -> bool:
        self.app.exit(result=buf.text)
        return False

    async def read(self, default: str = "") -> Any:
        self.buffer.reset(Document(default, len(default)))
        self.app.layout.focus(self.buffer)
        return await self.app.run_async()


class _Completer(Completer):
    """'/' at the start: the commands (and what a command takes); '@': files of the folder."""

    def __init__(self, tui: Tui) -> None:
        self.tui = tui

    def get_completions(self, document: Document, complete_event: Any) -> Iterable[Completion]:
        text = document.text_before_cursor
        if text.startswith("/") and "\n" not in text:
            name, space, arg = text[1:].partition(" ")
            if not space:
                for c in self.tui.all_commands():
                    for n in (c.name, *[a for a in c.aliases if a != "?"]):
                        if n.startswith(name.lower()):
                            yield Completion(
                                n,
                                start_position=-len(name),
                                display="/" + n,
                                display_meta=self.tui.command_help(c),
                            )
                            break
                return
            c = self.tui.lookup(name)
            if c is not None and c.args is not None:
                for value in c.args():
                    if value.lower().startswith(arg.lower()):
                        yield Completion(value, start_position=-len(arg))
            return
        word = text.split()[-1] if text and not text[-1].isspace() else ""
        if word.startswith("@"):
            query = word[1:].lower().replace("\\", "/")
            hits = 0
            files = self.tui.files()
            starts = [f for f in files if f.lower().startswith(query)]
            contains = [f for f in files if query in f.lower() and f not in starts] if query else []
            for f in starts + contains:
                yield Completion("@" + f, start_position=-len(word), display=f)
                hits += 1
                if hits >= 40:
                    return


async def run_tui(backend: Any, args: Any, texts: Texts, first: str | None) -> int:
    tui = Tui(backend, args, texts)
    client = tui.client
    await client.open()
    try:
        await client.pick_chat(quiet=True)
        return await tui.run(first)
    finally:
        await client.close()
