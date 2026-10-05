"""The interactive terminal (cli/tui.py) against a real backend and a scripted model.

Keys go in through a prompt_toolkit pipe, as a person would type them: a task and Enter, a digit
in a picker, Esc, Shift+Tab. Checked is what happens for real — the file the approved edit
changed (and the denied one did not), the answers the model received, the run stopped by Esc,
the mode the chat got — and what the screen showed. Plus the pieces: completions, diffs with
the file's line numbers, the command output without its framing, Markdown streamed in blocks.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

import server.chats as chats_module
from cli import tools_view
from cli.app import parse_args, use_tui
from cli.backend import Backend
from cli.render import Renderer, split_ready
from cli.texts import Texts
from cli.tui import Tui, _Completer
from core.llm.base import AssistantTurn
from tests.fakes import HangingLLM, ScriptedLLM, tool_call
from tests.test_cli import live  # noqa: F401 - the fixture: a real backend on a free port

ESC = "\x1b"
SHIFT_TAB = "\x1b[Z"


async def wait_until(cond: Callable[[], bool], timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.05)


class Screen:
    """A TUI session: `type()` sends keys, `out` is what the conversation printed."""

    def __init__(self, tui: Tui, inp: Any, buf: io.StringIO, task: asyncio.Task) -> None:
        self.tui, self.inp, self.buf, self.task = tui, inp, buf, task

    @property
    def out(self) -> str:
        return self.buf.getvalue()

    async def ready(self) -> None:
        await wait_until(lambda: self.tui.box is not None and self.tui.box.app.is_running)

    async def type(self, keys: str) -> None:
        self.inp.send_text(keys)
        await asyncio.sleep(0.15)

    async def task_line(self, text: str) -> None:
        await self.ready()
        await self.type(text + "\r")

    async def picker(self, starts: str) -> None:
        await wait_until(lambda: self.tui.picking.startswith(starts))

    async def idle(self) -> None:
        await wait_until(lambda: not self.tui.running and self.tui.r.phase == "")


@pytest.fixture()
def screen(live, settings, monkeypatch):  # noqa: F811
    """Starts the TUI on the live backend: `async with screen(mode) as s:`."""

    async def no_catalog(self: Tui) -> None:
        return None

    monkeypatch.setattr(Tui, "_fit_context", no_catalog)

    @contextlib.asynccontextmanager
    async def start(mode: str = "manual") -> AsyncIterator[Screen]:
        settings.approval_mode = mode
        with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
            args = parse_args([])
            args.cwd = str(settings.workspace)
            tui = Tui(live.backend, args, Texts("en"))
            buf = io.StringIO()
            tui.console = Console(
                file=buf, force_terminal=True, color_system=None, width=120, highlight=False
            )
            tui.r.console = tui.console
            tui.client.console = tui.console
            await tui.client.open()
            await tui.client.pick_chat(quiet=True)
            s = Screen(tui, inp, buf, asyncio.create_task(tui.run(None)))
            try:
                await s.ready()
                yield s
                await s.type("/exit\r")
                await asyncio.wait_for(asyncio.shield(s.task), 10)
            finally:
                if not s.task.done():
                    tui._exit = True
                    if tui.box is not None and tui.box.app.is_running:
                        tui.box.app.exit(result=None)
                    await asyncio.wait({s.task}, timeout=5)
                    s.task.cancel()
                await tui.client.close()

    return start


# ------------------------------------------------------------------ a whole run


async def test_an_approved_edit_changes_the_file_and_shows_the_diff(live, settings, screen):  # noqa: F811
    calc = Path(settings.workspace) / "calc.py"
    calc.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    live.script(
        [
            AssistantTurn(content="Looking.", tool_calls=[tool_call("read_file", path="calc.py")]),
            AssistantTurn(
                tool_calls=[
                    tool_call(
                        "edit_file", path="calc.py", old_text="    return a - b", new_text="    return a + b"
                    )
                ]
            ),
            AssistantTurn(content="Fixed **add**.\n\n```python\nreturn a + b\n```"),
        ]
    )
    async with screen("manual") as s:
        await s.task_line("fix add")
        await s.picker("Allow Update")
        assert calc.read_text(encoding="utf-8").endswith("a - b\n"), "nothing changes before the answer"
        await s.type("1")
        await s.idle()
        assert calc.read_text(encoding="utf-8").endswith("a + b\n")
        out = s.out
        assert "› fix add" in out and "Read(calc.py)" in out and "Read 2 lines" in out
        assert "✓ Allowed · Update(calc.py)" in out
        # The diff shows the file's own line number (2), not the fragment's (1).
        assert "2 -     return a - b" in out and "2 +     return a + b" in out and "+1 −1" in out
        assert "Fixed add." in out and "return a + b" in out and "done in" in out


async def test_a_denied_edit_leaves_the_file_alone(live, settings, screen):  # noqa: F811
    calc = Path(settings.workspace) / "calc.py"
    calc.write_text("x = 1\n", encoding="utf-8")
    live.script(
        [
            AssistantTurn(tool_calls=[tool_call("write_file", path="calc.py", content="x = 2\n")]),
            AssistantTurn(content="Left it as is."),
        ]
    )
    async with screen("manual") as s:
        await s.task_line("change x")
        await s.picker("Allow Write")
        await s.type("4")
        await s.idle()
        assert calc.read_text(encoding="utf-8") == "x = 1\n"
        assert "✗ Denied · Write(calc.py)" in s.out and "Left it as is." in s.out


async def test_the_answers_picked_reach_the_model(live, settings, screen, monkeypatch):  # noqa: F811
    holder: dict[str, ScriptedLLM] = {}
    llm = ScriptedLLM(
        [
            AssistantTurn(
                tool_calls=[
                    tool_call(
                        "ask",
                        questions=[
                            {
                                "question": "Which database?",
                                "kind": "single",
                                "options": [
                                    {"label": "SQLite", "recommended": True},
                                    {"label": "PostgreSQL"},
                                ],
                            },
                            {
                                "question": "Which extras?",
                                "kind": "multiple",
                                "options": [
                                    {"label": "Auth"},
                                    {"label": "Admin panel"},
                                    {"label": "API docs"},
                                ],
                            },
                        ],
                    )
                ]
            ),
            AssistantTurn(content="Noted."),
        ]
    )
    holder["llm"] = llm
    live.script([])
    monkeypatch.setattr(chats_module, "build_llm_client", lambda model=None, **kw: holder["llm"])
    async with screen("bypass") as s:
        await s.task_line("set it up")
        await s.picker("Which database?")
        await s.type("2")  # PostgreSQL at once
        await s.picker("Which extras?")
        await s.type("1")  # tick Auth
        await s.type("3")  # tick API docs
        await s.type("\r")
        await s.idle()
        answer = [m for m in llm.calls[-1]["messages"] if m.get("role") == "tool"][-1]["content"]
        assert "PostgreSQL" in answer and "Auth" in answer and "API docs" in answer
        assert "SQLite" not in answer and "Admin panel" not in answer
        assert "Which extras?" in s.out and "Auth, API docs" in s.out


async def test_esc_stops_a_running_task(live, monkeypatch, screen):  # noqa: F811
    monkeypatch.setattr(chats_module, "build_llm_client", lambda model=None, **kw: HangingLLM())
    async with screen("bypass") as s:
        await s.task_line("think forever")
        await wait_until(lambda: s.tui.running)
        await asyncio.sleep(0.3)
        await s.type(ESC)
        await wait_until(lambda: "Stopped." in s.out)
        await s.idle()


async def test_a_line_typed_during_a_run_goes_to_the_agent(live, monkeypatch, screen):  # noqa: F811
    monkeypatch.setattr(chats_module, "build_llm_client", lambda model=None, **kw: HangingLLM())
    async with screen("bypass") as s:
        await s.task_line("long job")
        await wait_until(lambda: s.tui.running)
        await s.type("also check the docs\r")
        await wait_until(lambda: "passed to the agent" in s.out)
        stored = live.store.load(s.tui.client.session["id"])
        assert [e["text"] for e in stored.timeline if e.get("kind") == "user"] == [
            "long job",
            "also check the docs",
        ]
        await s.type(ESC)
        await s.idle()


# ------------------------------------------------------------------ modes, commands, chats


async def test_shift_tab_and_mode_change_the_chats_approval_mode(live, screen):  # noqa: F811
    async with screen("manual") as s:
        await s.type(SHIFT_TAB)
        await wait_until(lambda: s.tui.client.mode == "auto")
        await s.type("/mode\r")
        await s.picker("Approval mode")
        await s.type("3")
        await wait_until(lambda: s.tui.client.mode == "bypass")
        live.script([AssistantTurn(content="ok")])
        await s.type("hi\r")  # a chat is stored once it has a message
        await s.idle()
        assert live.store.load(s.tui.client.session["id"]).approval_mode == "bypass"


async def test_a_saved_quick_command_expands_into_its_prompt(live, screen, monkeypatch):  # noqa: F811
    holder = {"llm": ScriptedLLM([AssistantTurn(content="ran them")])}
    monkeypatch.setattr(chats_module, "build_llm_client", lambda model=None, **kw: holder["llm"])
    async with screen("bypass") as s:
        assert "tests" in s.tui.custom  # the built-in quick commands are offered
        await s.task_line("/tests only the parser")
        await s.idle()
        sent = str(holder["llm"].calls[0]["messages"])
        assert "run_tests" in sent and "only the parser" in sent


async def test_new_and_resume_switch_chats(live, screen):  # noqa: F811
    live.script([AssistantTurn(content="first answer")])
    async with screen("bypass") as s:
        await s.task_line("plan the garden")
        await s.idle()
        first = s.tui.client.session["id"]
        await s.type("/new\r")
        await wait_until(lambda: s.tui.client.session.get("id") != first)
        await s.type(f"/resume {first}\r")
        await wait_until(lambda: s.tui.client.session.get("id") == first)
        # The resumed chat shows its conversation.
        await wait_until(lambda: s.out.count("plan the garden") >= 2)


async def test_an_unknown_command_is_not_sent_to_the_model(live, screen, monkeypatch):  # noqa: F811
    holder = {"llm": ScriptedLLM([])}
    monkeypatch.setattr(chats_module, "build_llm_client", lambda model=None, **kw: holder["llm"])
    async with screen("bypass") as s:
        await s.task_line("/frobnicate")
        await wait_until(lambda: "Unknown command /frobnicate" in s.out)
        assert holder["llm"].calls == []


# ------------------------------------------------------------------ the pieces


def _tui(tmp_path: Path) -> Tui:
    args = parse_args([])
    args.cwd = str(tmp_path)
    tui = Tui(Backend(1), args, Texts("en"))
    tui._register()
    tui.client.workspace = str(tmp_path)
    return tui


def test_completions_offer_commands_their_values_and_files(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text("", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "calc.js").write_text("", encoding="utf-8")
    tui = _tui(tmp_path)
    comp = _Completer(tui)

    def words(text: str) -> list[str]:
        return [c.text for c in comp.get_completions(Document(text), None)]

    assert {"model", "mode", "memory", "mcp"} <= set(words("/m"))
    assert words("/mode a") == ["auto"]
    assert words("look at @calc") == ["@src/calc.py"], "node_modules is never offered"
    assert words("plain text") == []


def test_diffs_carry_the_files_line_numbers(tmp_path):
    (tmp_path / "a.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    start = tools_view.line_of(str(tmp_path), "a.py", "three\n")
    assert start == 3
    rows = tools_view.diff_lines("three\nfour", "THREE\nfour", start)
    assert ("-", 3, "three") in rows and ("+", 3, "THREE") in rows and (" ", 4, "four") in rows
    assert tools_view.line_of(str(tmp_path), "missing.py", "x") == 1
    patch = "--- a/x\n+++ b/x\n@@ -10,2 +10,2 @@\n ctx\n-old\n+new\n"
    p_rows = tools_view.patch_rows(patch)
    assert ("+", 11, "new") in p_rows and ("-", 10, "old") in p_rows


def test_command_output_drops_the_tools_framing():
    out = "Exit code: 0\n\nstdout:\n5\nsix"
    assert tools_view.command_output(out) == "5\nsix"
    assert tools_view.command_output("Exit code: 0\n\n(no output)") == ""


def test_a_long_preview_says_how_much_more_there_is():
    t = Texts("en")
    old = "\n".join(f"line {i}" for i in range(80))
    new = "\n".join(f"LINE {i}" for i in range(80))
    lines = tools_view.render_diff(tools_view.diff_lines(old, new), t, limit=30)
    assert len(lines) == 31 and "130 more lines" in lines[-1].plain


def test_markdown_streams_in_finished_blocks():
    assert split_ready("para one\n\npara tw") == ("para one", "para tw")
    # A blank line inside a code fence does not end the block.
    assert split_ready("```py\na = 1\n\nb = 2\n") == ("", "```py\na = 1\n\nb = 2\n")
    buf = io.StringIO()
    r = Renderer(Texts("en"), Console(file=buf, force_terminal=True, color_system=None, width=80))
    r.event({"type": "run.started", "run_id": "r1"})
    r.event({"type": "text.delta", "text": "First paragraph.\n\nSecond"})
    assert "First paragraph." in buf.getvalue() and "Second" not in buf.getvalue()
    r.event({"type": "text.delta", "text": " one."})
    r.event({"type": "run.finished", "run_id": "r1", "steps": 1, "duration_ms": 1200, "cost_usd": 0.02})
    text = buf.getvalue()
    assert "Second one." in text and "done in 1.2 s" in text and "$0.02" in text
    assert r.session_usd == pytest.approx(0.02) and r.last_run_id == "r1" and r.phase == ""


def test_a_repeated_plan_is_shown_once():
    buf = io.StringIO()
    r = Renderer(Texts("en"), Console(file=buf, force_terminal=True, color_system=None, width=80))
    steps = [{"title": "Read", "status": "completed"}, {"title": "Fix", "status": "in_progress"}]
    r.event({"type": "plan.updated", "steps": steps})
    r.event({"type": "plan.updated", "steps": steps})
    assert buf.getvalue().count("Plan") == 1 and "☒ Read" in buf.getvalue() and "◐ Fix" in buf.getvalue()


def test_the_interactive_ui_is_for_terminals_only(monkeypatch):
    import sys

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    assert use_tui(parse_args([]))
    assert not use_tui(parse_args(["-p", "x"])) and not use_tui(parse_args(["--output-format", "json"]))
    monkeypatch.setenv("ALTAIR_PLAIN", "1")
    assert not use_tui(parse_args([]))
    monkeypatch.delenv("ALTAIR_PLAIN")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
    assert not use_tui(parse_args([])), "a pipe gets the plain line mode"


def test_every_text_has_both_languages():
    from cli.texts import PAIRS, TEXTS

    missing = [k for k, v in TEXTS.items() if set(v) != {"en", "ru"}]
    assert missing == []
    assert all(set(v) == {"en", "ru"} and len(v["en"]) == len(v["ru"]) for v in PAIRS.values())
