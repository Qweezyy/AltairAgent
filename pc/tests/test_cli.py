"""The terminal client against a real backend (uvicorn in a thread, a scripted model).

Checked: one-shot runs in text / json / stream-json, exit codes (2 when an approval had
nobody to answer it), resuming a chat by its title, the chats list, and that a chat started in
the terminal is an ordinary chat of the app (the window sees it). And the backend's address
file the terminal finds a running backend by.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import socket
import threading
import time

import pytest
import uvicorn
from rich.console import Console

import server.chats as chats_module
from cli.app import Client, parse_args, print_chats
from cli.backend import Backend
from cli.texts import Texts
from core import backend_info
from core.agent.storage import SessionStore
from core.llm.base import AssistantTurn
from tests.fakes import ScriptedLLM, tool_call


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def live(monkeypatch, settings):
    """A real backend on a free port; `live.script(turns)` sets what the model answers."""
    import core.settings as settings_module
    import server.app as app_module
    import server.ws as ws_module
    from server.app import create_app

    settings.approval_mode = "bypass"
    for mod in (settings_module, app_module, ws_module):
        monkeypatch.setattr(mod, "get_settings", lambda: settings)
    holder: dict[str, ScriptedLLM] = {"llm": ScriptedLLM([])}
    monkeypatch.setattr(chats_module, "build_llm_client", lambda model=None, **kw: holder["llm"])
    port = _free_port()
    app = create_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)

    class Live:
        backend = Backend(port)
        fastapi = app
        store = SessionStore(settings=settings)

        @staticmethod
        def script(turns):
            holder["llm"] = ScriptedLLM(list(turns))

    yield Live
    server.should_exit = True
    thread.join(15)


def _client(live, argv: list[str]) -> tuple[Client, io.StringIO]:
    out = io.StringIO()
    args = parse_args(argv)
    args.cwd = os.getcwd()
    console = Console(file=out, force_terminal=False, width=120, highlight=False)
    return Client(live.backend, args, Texts("en"), console), out


async def _one_shot(live, argv: list[str], task: str) -> tuple[int, Client, io.StringIO]:
    client, out = _client(live, ["-p", *argv, task])
    await client.open()
    try:
        await client.pick_chat()
        code = await client.print_mode(task)
    finally:
        await client.close()
    return code, client, out


async def test_a_one_shot_task_prints_the_answer_and_is_a_normal_chat(live, capsys):
    live.script([AssistantTurn(tool_calls=[tool_call("list_directory", path=".")]),
                 AssistantTurn(content="Two files are here.")])
    code, client, out = await _one_shot(live, [], "what is in this folder?")
    assert code == 0
    printed = capsys.readouterr().out + out.getvalue()
    assert "Two files are here." in printed and "list_directory" in printed and "done in" in printed
    stored = live.store.load(client.session["id"])            # the window lists this chat
    assert stored is not None and any(e.get("kind") == "answer" for e in stored.timeline)


async def test_json_output_is_one_object(live, capsys):
    live.script([AssistantTurn(content="42")])
    code, client, _ = await _one_shot(live, ["--output-format", "json"], "answer")
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    result = json.loads(lines[-1])
    assert code == 0 and result["ok"] and result["result"] == "42" and result["session_id"] == client.session["id"]
    assert result["needs_human"] is False


async def test_stream_json_prints_every_event(live, capsys):
    live.script([AssistantTurn(content="streamed")])
    code, _, _ = await _one_shot(live, ["--output-format", "stream-json"], "go")
    kinds = [json.loads(ln)["type"] for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert code == 0 and "run.started" in kinds and "text.delta" in kinds and "run.finished" in kinds


async def test_an_approval_with_nobody_to_ask_is_denied_and_exits_2(live, capsys):
    live.script([AssistantTurn(tool_calls=[tool_call("write_file", path="x.txt", content="hi")]),
                 AssistantTurn(content="I could not write it.")])
    code, client, out = await _one_shot(live, ["--mode", "manual"], "write x.txt")
    assert code == 2 and client.needs_human
    assert "nobody to ask" in out.getvalue()


async def test_resume_by_title_and_list_chats(live, capsys):
    live.script([AssistantTurn(content="first answer")])
    _, first, _ = await _one_shot(live, [], "plan the garden")
    title = live.store.load(first.session["id"]).title             # as the chats list shows it
    assert title and title != "Новый диалог"

    live.script([AssistantTurn(content="second answer")])
    client, out = _client(live, ["-p", "-r", title.split()[0], "go on"])
    await client.open()
    try:
        await client.pick_chat()
        assert client.session["id"] == first.session["id"]
        assert await client.print_mode("go on") == 0
        await print_chats(client)
    finally:
        await client.close()
    assert first.session["id"] in out.getvalue()
    stored = live.store.load(first.session["id"])
    assert [e["text"] for e in stored.timeline if e.get("kind") == "user"] == ["plan the garden", "go on"]


async def test_resuming_a_missing_chat_says_so(live):
    client, _ = _client(live, ["-p", "-r", "no-such-chat-anywhere", "x"])
    await client.open()
    try:
        with pytest.raises(LookupError):
            await client.pick_chat()
    finally:
        await client.close()


def test_the_backend_advertises_itself_for_the_terminal(tmp_path):
    backend_info.advertise(8123, tmp_path)
    info = backend_info.read(tmp_path)
    assert info == {"port": 8123, "pid": os.getpid(), "version": info["version"]}
    backend_info.withdraw(tmp_path)
    assert backend_info.read(tmp_path) is None
    # A file left by a crashed backend (dead pid) is not trusted.
    (tmp_path / "backend.json").write_text(json.dumps({"port": 8123, "pid": 999_999_999}), encoding="utf-8")
    assert backend_info.read(tmp_path) is None


def test_arguments():
    args = parse_args(["-c", "--mode", "auto", "--output-format", "json", "fix", "it"])
    assert args.cont and args.mode == "auto" and args.prompt == ["fix", "it"] and args.output_format == "json"
    assert isinstance(parse_args([]), argparse.Namespace)


def test_version_flag_prints_the_product_version(capsys):
    from core.version import __version__

    with pytest.raises(SystemExit) as stop:
        parse_args(["--version"])
    assert stop.value.code == 0
    assert capsys.readouterr().out.strip() == f"altair {__version__}"


def test_the_one_file_command_carries_the_version():
    """bin/altair is one file: without VERSION inside it reported 0.0.0."""
    from pathlib import Path

    build = (Path(__file__).resolve().parents[1] / "build_app.py").read_text(encoding="utf-8")
    cli_build = build[build.index("def build_cli"):build.index("CLI_NAME}.exe")]
    assert "VERSION" in cli_build
    version_py = (Path(__file__).resolve().parents[1] / "core" / "version.py").read_text(encoding="utf-8")
    assert "_MEIPASS" in version_py


def test_the_command_starts_end_to_end(monkeypatch, capsys):
    """Found on the first server body: `altair` died at once with NameError (an edit had swallowed
    amain). The entry point itself runs: no backend reachable → a clear error and exit code 1."""
    import cli.app as app_module

    def no_backend(texts, console):
        raise RuntimeError("no backend here")

    monkeypatch.setattr(app_module, "_connect_quietly", no_backend)
    assert callable(app_module.amain)
    assert app_module.main(["-p", "hello"]) == 1
    assert "no backend here" in capsys.readouterr().out
