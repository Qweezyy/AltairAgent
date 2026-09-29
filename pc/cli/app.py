"""`altair`: the terminal client.

    altair                      talk in a new chat (the folder is the current directory)
    altair "task"               the same, starting with this task
    altair -p "task"            one task, print the answer, exit (for scripts and CI)
    echo task | altair -p       the task from stdin
    altair -c                   continue the most recent chat
    altair -r <id|title>        resume a chat (also one started in the window)
    altair --chats              list the chats

Exit codes: 0 done, 1 failed or stopped, 2 it needed a person (an approval or a question) and
there was nobody to ask.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from typing import Any

import httpx
import websockets
from rich.console import Console
from rich.text import Text

from cli.backend import Backend, connect
from cli.render import Renderer, json_line
from cli.texts import Texts

MODES = ("manual", "auto", "bypass")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="altair", description="Altair in the terminal: the same chats as the desktop app.")
    p.add_argument("prompt", nargs="*", help="the task (interactive mode starts with it)")
    p.add_argument("-p", "--print", dest="print_mode", action="store_true",
                   help="run one task, print the answer and exit (the task from the arguments or stdin)")
    p.add_argument("-c", "--continue", dest="cont", action="store_true", help="continue the most recent chat")
    p.add_argument("-r", "--resume", metavar="CHAT", help="resume a chat by id or by words from its title")
    p.add_argument("--chats", action="store_true", help="list the chats and exit")
    p.add_argument("--model", help="the model for this run")
    p.add_argument("--mode", choices=MODES, help="the chat's approval mode (kept by the chat)")
    p.add_argument("--cwd", "--workspace", dest="cwd", help="the folder of a new chat (default: the current one)")
    p.add_argument("--output-format", choices=("text", "json", "stream-json"), default="text",
                   help="text (default), json (one object at the end) or stream-json (every event as a line)")
    p.add_argument("--thinking", action="store_true", help="show the model's reasoning")
    return p.parse_args(argv)


class Client:
    """One terminal session: a socket to the backend showing one chat."""

    def __init__(self, backend: Backend, args: argparse.Namespace, texts: Texts, console: Console) -> None:
        self.backend = backend
        self.args = args
        self.t = texts
        self.console = console
        self.fmt = args.output_format
        self.render = Renderer(texts, console, thinking=args.thinking)
        self.ws: Any = None
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.lines: asyncio.Queue[str | None] = asyncio.Queue()
        self.session: dict[str, Any] = {}
        self.running = False
        self.needs_human = False
        self.last: dict[str, Any] = {}
        self.interactive = not args.print_mode
        self._reader: asyncio.Task | None = None
        self._input_task: asyncio.Task | None = None
        self._last_sigint = 0.0
        self._exit = asyncio.Event()

    # ------------------------------------------------------------ socket

    async def open(self) -> None:
        self.ws = await websockets.connect(self.backend.ws, max_size=None, ping_interval=30, open_timeout=20)
        self._reader = asyncio.create_task(self._read())
        ready = await self._wait_for("ready")
        self.session = ready.get("session") or {}
        await self.send({"type": "ui_lang", "lang": self.t.lang})

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                if isinstance(raw, str):
                    await self.events.put(json.loads(raw))
        except websockets.ConnectionClosed:
            pass
        await self.events.put({"type": "_closed"})

    async def send(self, payload: dict[str, Any]) -> None:
        await self.ws.send(json.dumps(payload, ensure_ascii=False))

    async def _wait_for(self, kind: str, timeout: float = 30.0) -> dict[str, Any]:
        """The next event of this kind; others that come first are handled as usual."""
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(kind)
            m = await asyncio.wait_for(self.events.get(), left)
            if m.get("type") == kind:
                return m
            if m.get("type") == "_closed":
                raise ConnectionError("the backend closed the connection")
            await self.handle(m)

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
        if self._reader:
            await asyncio.gather(self._reader, return_exceptions=True)

    # ------------------------------------------------------------ chats

    async def chats(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=15) as http:
            return (await http.get(f"{self.backend.http}/api/sessions")).json().get("sessions", [])

    async def pick_chat(self) -> None:
        """New chat in the folder, the latest one (-c) or the one asked for (-r)."""
        if self.args.cont or self.args.resume:
            chats = await self.chats()
            chosen = None
            if self.args.cont:
                chosen = chats[0] if chats else None
            else:
                query = self.args.resume.strip().lower()
                chosen = next((c for c in chats if c.get("id", "").startswith(query)), None) or next(
                    (c for c in chats if query in (c.get("title") or "").lower()), None)
            if chosen is None:
                raise LookupError(self.t("not_found", query=self.args.resume or "-c") if chats else self.t("no_chats"))
            await self.send({"type": "load_session", "session_id": chosen["id"]})
        else:
            await self.send({"type": "new_session", "workspace": self.args.cwd})
        loaded = await self._wait_for("session.loaded")
        self.session = loaded.get("session") or {}
        self.running = bool(loaded.get("running"))
        if self.args.mode:
            await self.send({"type": "set_mode", "mode": self.args.mode})
            await self._wait_for("mode.updated")
        if self.fmt == "text":
            title = self.session.get("title") or ""
            self.console.print(Text(self.t("chat", title=title, id=self.session.get("id", "")), style="dim"))
            self.console.print(Text(self.t("folder", path=loaded.get("workspace", "")), style="dim"))
            self.console.print(Text(self.t("mode", mode=self.args.mode or loaded.get("mode", "")), style="dim"))

    # ------------------------------------------------------------ one event

    async def handle(self, m: dict[str, Any]) -> None:
        kind = m.get("type")
        if self.fmt == "stream-json" and not kind.startswith("_"):
            json_line(m)
        elif self.fmt == "text":
            self.render.event(m)
        if kind == "state":
            self.running = m.get("state") in ("running", "waiting_approval")
        elif kind == "run.finished":
            self.last = m
        elif kind == "run.failed":
            self.last = {**m, "failed": True}
        elif kind == "run.cancelled":
            self.last = {**m, "cancelled": True}
        elif kind == "approval.requested":
            await self._approve(m)
        elif kind == "question.asked":
            await self._answer(m)
        elif kind == "session.title" and m.get("session_id") == self.session.get("id"):
            self.session["title"] = m.get("title")

    async def _ask_line(self) -> str | None:
        """The next line the user types (None: no terminal or stdin closed)."""
        if not sys.stdin.isatty():
            return None
        if self._input_task is None:
            return await asyncio.to_thread(sys.stdin.readline)
        return await self.lines.get()

    async def _approve(self, m: dict[str, Any]) -> None:
        name = m.get("name", "")
        self.render.pause()
        if not sys.stdin.isatty():
            self.needs_human = True
            if self.fmt == "text":
                self.console.print(Text(self.t("denied_no_tty", name=name), style="yellow"))
            await self.send({"type": "approval", "request_id": m["request_id"], "scope": "deny"})
            return
        self.console.print(Text(self.t("approval", name=name, reason=m.get("reason", "")), style="bold yellow"))
        args = m.get("args") or {}
        if args:
            shown = json.dumps(args, ensure_ascii=False)
            self.console.print(Text(shown if len(shown) < 400 else shown[:397] + "…", style="dim"))
        self.console.print(Text(self.t("approval_keys"), style="yellow"))
        answer = ((await self._ask_line()) or "n").strip().lower()[:1]
        scope = {"y": "once", "д": "once", "p": "project", "g": "global"}.get(answer, "deny")
        await self.send({"type": "approval", "request_id": m["request_id"], "scope": scope})

    async def _answer(self, m: dict[str, Any]) -> None:
        self.render.pause()
        answers: dict[str, list[str]] = {}
        if not sys.stdin.isatty():
            self.needs_human = True
            await self.send({"type": "answer", "request_id": m["request_id"], "answers": {}})
            return
        for i, q in enumerate(m.get("questions") or []):
            self.console.print(Text(q.get("question", ""), style="bold cyan"))
            options = q.get("options") or []
            for n, o in enumerate(options, 1):
                star = " ★" if o.get("recommended") else ""
                desc = f" — {o['description']}" if o.get("description") else ""
                self.console.print(f"  {n}. {o.get('label', '')}{star}{desc}")
            self.console.print(self.t("question_pick"), end="")
            raw = ((await self._ask_line()) or "").strip()
            picked = []
            for part in [x.strip() for x in raw.split(",") if x.strip()]:
                if part.isdigit() and 1 <= int(part) <= len(options):
                    picked.append(options[int(part) - 1].get("label", ""))
                else:
                    picked.append(part)
            answers[str(i)] = picked
        await self.send({"type": "answer", "request_id": m["request_id"], "answers": answers})

    # ------------------------------------------------------------ a run

    async def run_task(self, task: str) -> None:
        self.last = {}
        payload: dict[str, Any] = {"type": "run", "task": task}
        if self.args.model:
            payload["model"] = self.args.model
        await self.send(payload)
        await self.until_idle()

    async def until_idle(self) -> None:
        """Handles events until the run ends (the chat goes idle)."""
        started = False
        while True:
            m = await self._next_event_or_hint()
            if m is None:
                continue
            if m.get("type") == "_closed":
                raise ConnectionError("the backend closed the connection")
            await self.handle(m)
            if m.get("type") == "state":
                if m.get("state") in ("running", "waiting_approval"):
                    started = True
                elif m.get("state") == "idle" and (started or self.last):
                    self.render.close()
                    return
            if m.get("type") == "run.failed" and not m.get("run_id") and not started:
                self.render.close()
                return

    async def _next_event_or_hint(self) -> dict[str, Any] | None:
        """The next event; in interactive mode a line typed meanwhile goes to the running task
        as a hint (steering), `/stop` stops it. None = a line was handled, no event yet."""
        if self._input_task is None:
            return await self.events.get()
        event_task = asyncio.create_task(self.events.get())
        line_task = asyncio.create_task(self.lines.get())
        done, _ = await asyncio.wait({event_task, line_task}, return_when=asyncio.FIRST_COMPLETED)
        if line_task in done:
            line = line_task.result()
            if line is not None and line.strip():
                text = line.strip()
                await self.send({"type": "stop"} if text in ("/stop", "/стоп") else {"type": "run", "task": text})
            if event_task not in done:
                event_task.cancel()
                return None
        else:
            line_task.cancel()
        return event_task.result()

    # ------------------------------------------------------------ Ctrl+C

    def on_sigint(self) -> None:
        now = time.monotonic()
        if self.running and now - self._last_sigint > 2.0:
            self._last_sigint = now
            self.render.pause()
            self.console.print(Text(self.t("stopping"), style="yellow"))
            asyncio.ensure_future(self.send({"type": "stop"}))
            return
        if now - self._last_sigint <= 2.0 or not self.interactive:
            self._exit.set()
            return
        self._last_sigint = now
        self.console.print(Text(self.t("again_to_exit"), style="dim"))

    # ------------------------------------------------------------ the loops

    async def print_mode(self, task: str) -> int:
        await self.run_task(task)
        text = self.last.get("text", "")
        if self.fmt == "json":
            json_line({
                "ok": bool(self.last) and not self.last.get("failed") and not self.last.get("cancelled"),
                "result": text, "session_id": self.session.get("id"), "steps": self.last.get("steps"),
                "duration_ms": self.last.get("duration_ms"), "cost_usd": self.last.get("cost_usd"),
                "usage": self.last.get("usage"), "needs_human": self.needs_human,
                "error": self.last.get("message") if self.last.get("failed") else None,
            })
        if self.last.get("failed") or self.last.get("cancelled") or not self.last:
            return 1
        return 2 if self.needs_human else 0

    async def _read_lines(self) -> None:
        while True:
            try:
                line = await asyncio.to_thread(sys.stdin.readline)
            except (EOFError, KeyboardInterrupt, OSError):
                line = ""
            if line == "":  # EOF (or Ctrl+Z/Ctrl+D)
                if time.monotonic() - self._last_sigint < 1.0:
                    continue  # Ctrl+C broke the read on Windows: keep reading
                await self.lines.put(None)
                return
            await self.lines.put(line.rstrip("\r\n"))

    async def interactive_loop(self, first: str | None) -> int:
        self.console.print(Text(self.t("help"), style="dim"))
        self._input_task = asyncio.create_task(self._read_lines())
        pending = first
        while not self._exit.is_set():
            if pending is None:
                if not self.running:
                    self.console.print(Text(f"{self.t('prompt')} › ", style="bold"), end="")
                line_task = asyncio.create_task(self.lines.get())
                event_task = asyncio.create_task(self.events.get())
                exit_task = asyncio.create_task(self._exit.wait())
                done, _ = await asyncio.wait({line_task, event_task, exit_task}, return_when=asyncio.FIRST_COMPLETED)
                for task in (line_task, event_task, exit_task):
                    if task not in done:
                        task.cancel()
                if event_task in done:
                    m = event_task.result()
                    if m.get("type") == "_closed":
                        return 1
                    await self.handle(m)          # a reminder woke the chat, a steering answer…
                    if line_task in done:
                        pending = line_task.result()
                    continue
                if exit_task in done:
                    break
                pending = line_task.result()
                if pending is None:
                    break
            line, pending = pending.strip(), None
            if not line:
                continue
            if line.startswith("/"):
                if await self.command(line):
                    break
                continue
            if self.running:
                await self.send({"type": "run", "task": line})   # a hint to the running task
                continue
            await self.run_task(line)
        return 0

    async def command(self, line: str) -> bool:
        """A slash command; True = exit."""
        cmd, _, rest = line[1:].partition(" ")
        cmd, rest = cmd.lower(), rest.strip()
        if cmd in ("exit", "quit", "q"):
            return True
        if cmd == "stop":
            await self.send({"type": "stop"})
        elif cmd == "new":
            self.args.cont, self.args.resume = False, None
            await self.pick_chat()
        elif cmd in ("chats", "list"):
            await print_chats(self, limit=20)
        elif cmd == "resume" and rest:
            self.args.cont, self.args.resume = False, rest
            try:
                await self.pick_chat()
            except LookupError as exc:
                self.console.print(Text(str(exc), style="yellow"))
        elif cmd == "mode" and rest in MODES:
            await self.send({"type": "set_mode", "mode": rest})
            await self._wait_for("mode.updated")
            self.console.print(Text(self.t("mode", mode=rest), style="dim"))
        elif cmd == "model" and rest:
            self.args.model = rest
        else:
            self.console.print(Text(self.t("help"), style="dim"))
        return False


async def print_chats(client: Client, limit: int = 30) -> None:
    chats = await client.chats()
    if not chats:
        client.console.print(client.t("no_chats"))
        return
    for c in chats[:limit]:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(c.get("updated_at") or 0))
        busy = f"  {client.t('running_bg')}" if c.get("running") else ""
        line = Text(f"{c.get('id', '')}  ", style="cyan")
        line.append(f"{when}  ", style="dim")
        line.append(c.get("title") or "")
        line.append(busy, style="yellow")
        client.console.print(line)


async def amain(args: argparse.Namespace) -> int:
    texts = Texts()
    console = Console(highlight=False, stderr=args.output_format != "text")
    task = " ".join(args.prompt).strip()
    if args.print_mode and (not task or task == "-") and not sys.stdin.isatty():
        task = sys.stdin.read().strip()
    if args.print_mode and not task:
        console.print(texts("empty_task"))
        return 1

    try:
        backend, joined = await asyncio.to_thread(_connect_quietly, texts, console)
    except (RuntimeError, OSError) as exc:
        console.print(Text(texts("no_backend", error=exc), style="red"))
        return 1
    if args.output_format == "text" and joined and not args.print_mode:
        console.print(Text(texts("joined"), style="dim"))

    client = Client(backend, args, texts, console)
    loop = asyncio.get_running_loop()
    previous = signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(client.on_sigint))
    try:
        await client.open()
        if args.chats:
            await print_chats(client)
            return 0
        await client.pick_chat()
        if client.running:            # resumed a chat that works in the background: follow it
            await client.until_idle()
        if args.print_mode:
            runner = asyncio.create_task(client.print_mode(task))
            exit_wait = asyncio.create_task(client._exit.wait())
            done, _ = await asyncio.wait({runner, exit_wait}, return_when=asyncio.FIRST_COMPLETED)
            if runner in done:
                exit_wait.cancel()
                return runner.result()
            runner.cancel()
            return 1
        return await client.interactive_loop(task or None)
    except (LookupError, ConnectionError, TimeoutError, OSError) as exc:
        console.print(Text(str(exc), style="red"))
        return 1
    finally:
        signal.signal(signal.SIGINT, previous)
        await client.close()
        await asyncio.to_thread(backend.stop)


def _connect_quietly(texts: Texts, console: Console) -> tuple[Backend, bool]:
    from cli.backend import find_running

    found = find_running()
    if found is not None:
        return found, True
    with console.status(texts("starting")):
        return connect()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                continue
    args = parse_args(argv)
    args.cwd = os.path.abspath(args.cwd or os.getcwd())  # a new chat's folder
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 130
