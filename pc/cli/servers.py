"""`altair server …`: the agent's servers from the terminal, through the same API as the window.

    altair server                       the servers added
    altair server check HOST            what the server is (read-only)
    altair server add HOST              check, then install the agent there
    altair server remove ID|NAME        remove the agent from a server

A password is asked only for a server not added before (later logins use this PC's key) and is
read without echo, or from stdin with --password-stdin for scripts.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from typing import Any

import httpx
from rich.console import Console
from rich.table import Table
from rich.text import Text

from cli.backend import Backend, connect
from cli.texts import Texts

COMMANDS = ("list", "check", "add", "remove")
MODES = ("owner", "autopilot", "careful")
STEPS = ("connect", "preflight", "key", "packages", "download", "unpack", "configure", "identity", "service",
         "health")


def wants(argv: list[str]) -> bool:
    """`altair server` and `altair server <command> …`; "altair server is slow, look" stays a task."""
    return bool(argv) and argv[0] == "server" and (len(argv) == 1 or argv[1] in COMMANDS or argv[1] in ("-h", "--help"))


def parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="altair server", description="The agent's servers (bodies on a VPS).")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("list", help="the servers added")
    for name, text in (("check", "what the server is, read-only"), ("add", "check, then install the agent there")):
        c = sub.add_parser(name, help=text)
        c.add_argument("host")
        c.add_argument("-u", "--user", default="root")
        c.add_argument("-P", "--port", type=int, default=22)
        c.add_argument("--password-stdin", action="store_true", help="read the SSH password from stdin")
        if name == "add":
            c.add_argument("--mode", choices=MODES, help="default: the one the check recommends")
            c.add_argument("--name", default="")
            c.add_argument("--no-memory", action="store_true", help="do not bring memory and skills from this PC")
            c.add_argument("-y", "--yes", action="store_true", help="do not ask before installing")
    r = sub.add_parser("remove", help="remove the agent from a server")
    r.add_argument("server", help="id, name or address")
    r.add_argument("--keep-data", action="store_true", help="leave the agent's data folder on the server")
    r.add_argument("-y", "--yes", action="store_true")
    args = p.parse_args(argv)
    args.command = args.command or "list"
    return args


class Servers:
    def __init__(self, backend: Backend, texts: Texts, console: Console,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base = backend.http
        self.transport = transport      # tests talk to the app in-process
        self.t = texts
        self.console = console

    async def _get(self, path: str) -> Any:
        async with httpx.AsyncClient(timeout=30, transport=self.transport) as http:
            r = await http.get(self.base + path)
            r.raise_for_status()
            return r.json()

    async def _post(self, path: str, payload: dict, timeout: float = 120) -> Any:
        async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as http:
            r = await http.post(self.base + path, json=payload)
            r.raise_for_status()
            return r.json()

    async def all(self) -> list[dict]:
        return (await self._get("/api/servers")).get("servers", [])

    async def show_list(self) -> int:
        servers = await self.all()
        if not servers:
            self.console.print(self.t("srv.none"))
            return 0
        table = Table(box=None, pad_edge=False, header_style="dim")
        for col in ("id", self.t("srv.c.name"), self.t("srv.c.login"), self.t("srv.c.mode"), self.t("srv.c.system"),
                    "Altair"):
            table.add_column(col)
        for s in servers:
            table.add_row(s["id"], s.get("name") or "", f"{s['user']}@{s['host']}:{s['port']}",
                          self.t(f"srv.mode.{s['mode']}"), f"{s.get('system', '')} {s.get('arch', '')}".strip(),
                          s.get("version", ""))
        self.console.print(table)
        return 0

    async def _login(self, args: argparse.Namespace) -> dict | None:
        login = {"host": args.host, "port": args.port, "user": args.user, "password": ""}
        known = any((s["host"], s["port"], s["user"]) == (args.host, args.port, args.user) for s in await self.all())
        if args.password_stdin:
            login["password"] = sys.stdin.readline().rstrip("\r\n")
        elif not known:
            if not sys.stdin.isatty():
                self.console.print(Text(self.t("srv.need_password"), style="red"))
                return None
            login["password"] = await asyncio.to_thread(getpass.getpass, self.t("srv.password", login=(
                f"{args.user}@{args.host}")))
        return login

    async def check(self, args: argparse.Namespace) -> tuple[dict | None, dict | None]:
        login = await self._login(args)
        if login is None:
            return None, None
        with self.console.status(self.t("srv.checking")):
            r = await self._post("/api/servers/preflight", login)
        if not r.get("ok"):
            self.console.print(Text(r.get("error") or "?", style="red"))
            return None, login
        self._report(r["report"])
        return r["report"], login

    def _report(self, p: dict) -> None:
        def size(mb: int) -> str:
            return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb} MB"

        rows = [(self.t("srv.f.system"), p.get("system", "")), (self.t("srv.f.cpu"), f"{p['arch']} · {p['cpus']}"),
                (self.t("srv.f.memory"), size(p["mem_mb"])), (self.t("srv.f.disk"), size(p["disk_mb"])),
                (self.t("srv.f.rights"), p["sudo"])]
        if p.get("missing"):
            rows.append((self.t("srv.f.adds"), ", ".join(p["missing"])))
        if p.get("installed"):
            rows.append((self.t("srv.f.installed"), p["installed"]))
        rows.append((self.t("srv.f.fp"), p.get("host_key_fingerprint", "")))
        table = Table.grid(padding=(0, 2))
        for key, value in rows:
            table.add_row(Text(key, style="dim"), value)
        self.console.print(table)
        for problem in p.get("problems", []):
            self.console.print(Text("✗ " + problem, style="red"))
        if p.get("can_install"):
            self.console.print(Text(self.t("srv.recommend", mode=self.t(f"srv.mode.{p['recommended_mode']}"),
                                           why=p.get("why", "")), style="dim"))

    async def add(self, args: argparse.Namespace) -> int:
        report, login = await self.check(args)
        if report is None or login is None:
            return 1
        if not report.get("can_install"):
            return 1
        mode = args.mode or report["recommended_mode"]
        if not args.yes:
            if not sys.stdin.isatty():
                self.console.print(Text(self.t("srv.need_yes"), style="red"))
                return 1
            answer = await asyncio.to_thread(input, self.t("srv.confirm", host=args.host,
                                                           mode=self.t(f"srv.mode.{mode}")))
            if answer.strip().lower() not in ("", "y", "yes", "д", "да"):
                return 1
        sudo = ""
        if report.get("sudo") == "password":
            sudo = await asyncio.to_thread(getpass.getpass, self.t("srv.sudo_password"))
        started = await self._post("/api/servers/install", {**login, "mode": mode, "name": args.name,
                                                            "sudo_password": sudo, "bring_memory": not args.no_memory})
        login["password"] = ""
        if not started.get("ok"):
            self.console.print(Text(started.get("error") or "?", style="red"))
            return 1
        return await self._follow(started["job"])

    async def _follow(self, job_id: str) -> int:
        shown: set[str] = set()
        job: dict = {}
        with self.console.status(self.t("srv.step.connect")) as status:
            while True:
                job = await self._get(f"/api/servers/jobs/{job_id}")
                # Finished between two polls: the step is already "done", every step passed.
                current = job.get("failed_step") or job["step"] if job["state"] == "error" else job["step"]
                at = len(STEPS) if job["state"] == "done" else STEPS.index(current) if current in STEPS else 0
                for step in STEPS[:at]:
                    if step not in shown:
                        shown.add(step)
                        self.console.print(Text.assemble(("✓ ", "green"), self.t(f"srv.step.{step}")))
                if job["state"] != "running":
                    break
                detail = f"  {job['detail']}" if job.get("detail") else ""
                status.update(self.t(f"srv.step.{job['step']}") + detail)
                await asyncio.sleep(0.7)
        if job["state"] == "done":
            server = job.get("server") or {}
            self.console.print(Text("✦ " + self.t("srv.done", name=server.get("name") or server.get("host", "")),
                                    style="bold"))
            return 0
        failed = job.get("failed_step") or job.get("step", "")
        self.console.print(Text("✗ " + self.t("srv.failed", step=self.t(f"srv.step.{failed}")), style="red"))
        self.console.print(Text(job.get("error", ""), style="dim"))
        return 1

    async def remove(self, args: argparse.Namespace) -> int:
        wanted = args.server.strip()
        match = [s for s in await self.all() if wanted in (s["id"], s.get("name"), s["host"])]
        if len(match) != 1:
            self.console.print(Text(self.t("srv.not_found" if not match else "srv.ambiguous", query=wanted),
                                    style="red"))
            return 1
        s = match[0]
        if not args.yes:
            if not sys.stdin.isatty():
                self.console.print(Text(self.t("srv.need_yes"), style="red"))
                return 1
            answer = await asyncio.to_thread(input, self.t("srv.confirm_remove", name=s.get("name") or s["host"]))
            if answer.strip().lower() not in ("y", "yes", "д", "да"):
                return 1
        with self.console.status(self.t("srv.removing")):
            r = await self._post(f"/api/servers/{s['id']}/uninstall", {"keep_data": args.keep_data})
        if not r.get("ok"):
            self.console.print(Text(r.get("error") or "?", style="red"))
            return 1
        self.console.print(self.t("srv.removed"))
        return 0


async def run(argv: list[str]) -> int:
    args = parse(argv[1:])
    texts = Texts()
    console = Console(highlight=False)
    try:
        backend, _joined = await asyncio.to_thread(connect)
    except (RuntimeError, OSError) as exc:
        console.print(Text(texts("no_backend", error=exc), style="red"))
        return 1
    servers = Servers(backend, texts, console)
    try:
        if args.command == "check":
            report, _ = await servers.check(args)
            return 0 if report is not None and report.get("can_install") else 1
        if args.command == "add":
            return await servers.add(args)
        if args.command == "remove":
            return await servers.remove(args)
        return await servers.show_list()
    except httpx.HTTPError as exc:
        console.print(Text(str(exc) or type(exc).__name__, style="red"))
        return 1
    finally:
        await asyncio.to_thread(backend.stop)     # a no-op when it is the window's backend
