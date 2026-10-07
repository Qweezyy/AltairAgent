"""One agent, several bodies: a tool that works with a machine can run on a server instead.

The tools that touch a machine — commands, files, search, git, tests, background processes, dev
servers, databases — get an optional `body` argument once at least one server is added. Empty
or "pc": here, as before. A server's name: the call goes through that server's tunnel to
`POST /api/tools/run` there (server/bodies.py) and the agent on the server runs its own tool.
"build-1:/srv/app" also names the folder there; the chat remembers it (the last folder given
for that body in this chat's history), so later calls can say just "build-1".

Approval stays where the user is: the card is shown in this chat before the call leaves, with
the stricter of this chat's mode and the mode chosen for that server (a "Careful" server asks
even from a chat with no confirmations). The server then runs it without asking again, but its
own hard blocks (catastrophic commands) still hold.

The tool list shown to the model changes only when the first server is added or the last one
removed: users without servers send exactly the schemas they sent before.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field, create_model

from core.errors import ToolError
from core.logging_setup import get_logger
from core.tools.base import Tool, ToolContext, ToolResult

logger = get_logger("bodies_routing")

#: The tools that make sense on another machine.
ROUTED = frozenset({
    "execute_command", "run_python", "run_background", "read_background", "stop_background",
    "watch_background", "read_file", "write_file", "edit_file", "apply_patch", "delete_path",
    "list_directory", "find_files", "grep_search", "code_map", "git_status", "git_diff", "git_log",
    "git_commit", "git_branch", "git_restore", "git_blame", "run_tests", "run_lint", "test_coverage",
    "start_dev_server", "stop_dev_server", "list_dev_servers", "read_dev_server", "db_query",
    "db_schema", "http_request", "download_file",
})

BODY_HELP = ("Where to run it: leave out for this machine; a server's name (the `bodies` tool lists "
             "them) runs it there, 'name:/folder' also sets the folder there (remembered in this chat).")

#: Stricter first: the mode a remote call is judged by is the stricter of the chat's and the server's.
_STRICTNESS = {"plan": 0, "manual": 1, "allowlist": 1, "auto": 2, "accept_edits": 3, "bypass": 4}
#: The server's mode → the approval mode it stands for (core/servers/install.py MODE_APPROVAL).
_SERVER_APPROVAL = {"owner": "bypass", "autopilot": "accept_edits", "careful": "manual"}

_LOCAL_NAMES = {"", "pc", "local", "this", "here"}
_PATH_START = re.compile(r"(/|~|[A-Za-z]:[/\\])")


def stricter(a: str, b: str) -> str:
    return a if _STRICTNESS.get(a, 1) <= _STRICTNESS.get(b, 1) else b


# ---------------------------------------------------------------- where the bodies are


class _Router:
    """The tunnels (core/servers/tunnel.py), set by the app when they start."""

    tunnels: Any = None
    data_dir: Any = None

    def servers(self) -> list[Any]:
        if self.tunnels is None:
            return []
        return self.tunnels.all()


router = _Router()


def set_tunnels(tunnels: Any, data_dir: Any = None) -> None:
    router.tunnels = tunnels
    router.data_dir = data_dir


def routing_enabled() -> bool:
    return bool(router.servers())


def split_body(value: str) -> tuple[str, str]:
    """'build-1:/srv/app' → ('build-1', '/srv/app'); 'build-1' → ('build-1', ''). The folder
    starts after the first colon followed by a path — '/', '~' or a drive ('win-box:C:/work')."""
    value = (value or "").strip()
    at = value.find(":")
    if at > 0 and _PATH_START.match(value[at + 1:]):
        return value[:at].strip(), value[at + 1:].strip()
    return value, ""


def find_server(name: str) -> Any | None:
    """The tunnel of the server by name, address or id (case-insensitive); None if unknown."""
    q = name.strip().lower()
    tunnels = router.servers()
    for t in tunnels:
        r = t.record
        if q in {str(r.id).lower(), str(r.name or "").lower(), str(r.host).lower(), str(r.body_id or "").lower()} - {""}:
            return t
    return None


def remembered_folder(messages: Iterable[dict[str, Any]], body: str) -> str:
    """The last folder this chat gave for `body` ("name:/folder" in a routed call's arguments)."""
    folder = ""
    want = body.strip().lower()
    for message in messages:
        for call in message.get("tool_calls") or ():
            fn = call.get("function") if isinstance(call, dict) else None
            if not isinstance(fn, dict) or fn.get("name") not in ROUTED:
                continue
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (TypeError, ValueError):
                continue
            name, where = split_body(str(args.get("body") or "")) if isinstance(args, dict) else ("", "")
            if where and name.lower() == want:
                folder = where
    return folder


# ---------------------------------------------------------------- the routed tool


async def call_remote(tunnel: Any, tool: str, args: dict[str, Any], folder: str, chat: str,
                      timeout: float | None) -> ToolResult:
    import httpx

    if tunnel.state != "online" or not tunnel.local_port:
        raise ToolError(f"The server '{tunnel.record.name or tunnel.record.host}' is not connected right now "
                        f"({tunnel.error or tunnel.state}); run it here or try again later.")
    payload = {"tool": tool, "args": args, "folder": folder, "chat": chat}
    limit = httpx.Timeout(30, read=None if timeout is None else timeout + 30)
    try:
        async with httpx.AsyncClient(timeout=limit) as http:
            r = await http.post(f"http://127.0.0.1:{tunnel.local_port}/api/tools/run", json=payload)
    except httpx.HTTPError as exc:
        raise ToolError(f"The server '{tunnel.record.name or tunnel.record.host}' did not answer: "
                        f"{exc or type(exc).__name__}") from exc
    if r.status_code != 200:
        raise ToolError(f"The server refused the call ({r.status_code}): {r.text[:300]}")
    data = r.json()
    meta = dict(data.get("metadata") or {})
    meta["body"] = tunnel.record.name or tunnel.record.host
    meta["body_folder"] = data.get("folder") or folder
    return ToolResult(content=str(data.get("content") or ""), ok=bool(data.get("ok")), metadata=meta)


class RoutedTool(Tool):
    """A machine tool that can run on another body. Built per wrapped tool by `routed()`, which
    gives each its tool's name, description and arguments; this base is never registered."""

    name = "routed_tool"
    description = "A machine tool that can also run on a server."
    inner: Tool

    def _inner_args(self, args: BaseModel) -> BaseModel:
        return self.inner.Args.model_validate(args.model_dump(exclude={"body"}))

    def schema(self) -> dict[str, Any]:
        # Without servers the model sees the very same tool as before (and the same cache).
        if not routing_enabled():
            return self.inner.schema()
        return super().schema()

    def auto_verdict(self, args: BaseModel, ctx: ToolContext) -> str:
        return self.inner.auto_verdict(self._inner_args(args), ctx)

    def approval_reason(self, args: BaseModel) -> str:
        reason = self.inner.approval_reason(self._inner_args(args))
        name, folder = split_body(getattr(args, "body", "") or "")
        if name.lower() not in _LOCAL_NAMES:
            from core.i18n import tr

            reason += "\n" + tr("appr.on_body", body=name + (f" ({folder})" if folder else ""))
        return reason

    async def invoke(self, raw_args: dict[str, Any] | str | None, ctx: ToolContext) -> ToolResult:
        body = ""
        if isinstance(raw_args, dict):
            body = str(raw_args.get("body") or "")
        elif isinstance(raw_args, str) and '"body"' in raw_args:
            try:
                body = str((json.loads(raw_args) or {}).get("body") or "")
            except (TypeError, ValueError, AttributeError):
                body = ""
        name, _folder = split_body(body)
        if name.lower() not in _LOCAL_NAMES:
            tunnel = find_server(name)
            if tunnel is not None:
                # The server's own mode counts too: whichever asks more.
                server_mode = _SERVER_APPROVAL.get(tunnel.record.mode, "manual")
                mode = stricter(ctx.settings.approval_mode, server_mode)
                if mode != ctx.settings.approval_mode:
                    ctx = dataclasses.replace(ctx, settings=ctx.settings.model_copy(update={"approval_mode": mode}))
        return await super().invoke(raw_args, ctx)

    async def run(self, args: Any, ctx: ToolContext) -> str | ToolResult:
        name, folder = split_body(getattr(args, "body", "") or "")
        inner_args = self._inner_args(args)
        if name.lower() in _LOCAL_NAMES:
            # The wrapper has no limit of its own (a remote call waits for the server's): the
            # tool's own limit holds here, with the usual message.
            limit = self.inner.timeout
            try:
                if limit:
                    return await asyncio.wait_for(self.inner.run(inner_args, ctx), timeout=limit)
                return await self.inner.run(inner_args, ctx)
            except asyncio.TimeoutError:
                return ToolResult.fail(f"'{self.name}' exceeded its {limit} s limit and was stopped.", timeout=True)
        tunnel = find_server(name)
        if tunnel is None:
            known = ", ".join(t.record.name or t.record.host for t in router.servers()) or "none"
            raise ToolError(f"No body named '{name}'. Known servers: {known}; leave `body` out for this machine.")
        if not folder and ctx.session is not None:
            folder = remembered_folder(getattr(ctx.session, "messages", []) or [], name)
        chat = str(getattr(ctx.session, "id", "") or "")
        return await call_remote(tunnel, self.inner.name, inner_args.model_dump(mode="json"), folder, chat,
                                 self.inner.timeout)


def routed(tool: Tool) -> Tool:
    """The same tool, able to run on another body (only the tools in ROUTED)."""
    if tool.name not in ROUTED or isinstance(tool, RoutedTool):
        return tool
    inner_cls = type(tool)
    if "body" in inner_cls.Args.model_fields:
        return tool
    args = create_model(f"{inner_cls.Args.__name__}OnBody", __base__=inner_cls.Args,
                        body=(str, Field(default="", description=BODY_HELP)))
    attrs = {
        "name": tool.name, "description": tool.description, "Args": args, "dangerous": tool.dangerous,
        "category": tool.category, "timeout": None,
        "max_output_chars": tool.max_output_chars, "irreversible": tool.irreversible,
        "__module__": __name__,
    }
    cls = type(f"Routed{inner_cls.__name__}", (RoutedTool,), attrs)
    wrapper = cls()
    wrapper.inner = tool
    return wrapper


def routed_all(tools: Iterable[Tool]) -> list[Tool]:
    return [routed(t) for t in tools]


def prompt_section() -> str:
    """What the agent should know about its bodies, for the system prompt. Only what rarely
    changes (names, what each machine has, labels): the live state is the `bodies` tool's, so the
    cached prompt stays the same while servers come and go online."""
    servers = router.servers()
    if not servers:
        return ""
    from core.body_labels import BodyLabels

    lines = []
    for t in servers:
        r = t.record
        st = t.status or {}
        facts = [st.get("system") or r.system, st.get("arch") or r.arch]
        if st.get("cpus"):
            facts.append(f"{st['cpus']} CPU")
        if st.get("mem_mb"):
            facts.append(f"{round(st['mem_mb'] / 1024)} GB RAM")
        if st.get("gpus"):
            facts.append("GPU " + ", ".join(st["gpus"]))
        if st.get("docker"):
            facts.append("Docker")
        try:
            labels = BodyLabels(router.data_dir).get(r.id) if router.data_dir else []
        except OSError:
            labels = []
        line = f"- {r.name or r.host}: " + ", ".join(f for f in facts if f) + f"; mode {r.mode}"
        if st.get("workspace"):
            line += f"; default folder {st['workspace']}"
        if labels:
            line += "; labels: " + ", ".join(labels)
        lines.append(line)
    return "\n".join([
        "<bodies>",
        "You are one agent with several bodies. This chat runs on this PC; these servers are yours too, and "
        "the machine tools (commands, files, search, git, tests, background processes, dev servers) take a "
        "`body` argument to run there — 'name' or 'name:/folder'.",
        *lines,
        "Pick the body by: hard needs first (GPU, Docker, architecture, memory); then where the files are; "
        "long work or work meant to go on while the PC is off goes to a server; then the lighter load "
        "(`bodies` shows it live and ranks them with action='pick'); then the owner's labels. Short local "
        "work stays here. Say in one line where and why. Files on one body are not on another: copy what "
        "is needed (write_file on the target, or git).",
        "</bodies>",
    ])
