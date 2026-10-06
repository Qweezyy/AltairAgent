"""Tools that watch dev servers.

The agent's usual loop:
  1. start_dev_server(command="npm run dev", name="web") — start the server;
  2. read_dev_server(name="web", wait_sec=3) — read the logs, see the error;
  3. edit_file(...) — fix the cause;
  4. read_dev_server(name="web", wait_sec=3) — make sure the rebuild went through;
  5. stop_dev_server(name="web") — when the work is done.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from core.devserver import get_manager, scan_output
from core.devserver.detect import looks_ready
from core.devserver.manager import DevServerError
from core.i18n import tr
from core.security.paths import resolve_path
from core.tools.base import EmptyArgs, Tool, ToolContext, ToolResult
from core.tools.builtin.shell import check_command

#: The longest one call waits for fresh logs.
_MAX_WAIT = 20.0


def _format_errors(lines: list[str]) -> str:
    """A block with the errors found in these lines ("" when there are none)."""
    errors = scan_output(lines)
    if not errors:
        return ""
    seen: set[str] = set()
    rows: list[str] = []
    for err in errors:
        if err.text in seen:
            continue
        seen.add(err.text)
        rows.append(f"  • [{err.source}] {err.text}")
    return "⚠️ Looks like errors:\n" + "\n".join(rows[:15])


class StartDevServerArgs(BaseModel):
    command: str = Field(description="The start command, e.g. 'npm run dev' or 'uvicorn app:app --reload'")
    name: str = Field(description="A short name to refer to the server later, e.g. 'web'")
    cwd: str = Field(default=".", description="The working folder, relative to the workspace")
    wait_sec: float = Field(default=3.0, description="Seconds to wait for the first logs (0–20)")


class StartDevServerTool(Tool):
    name = "start_dev_server"
    description = (
        "Starts a long-running dev server (npm run dev, uvicorn --reload, cargo watch…) in the "
        "background and returns its first log lines. Unlike execute_command it does not wait for "
        "the process to end — read it later with read_dev_server. Not for ordinary commands."
    )
    Args = StartDevServerArgs
    category = "execute"
    dangerous = True
    timeout = None

    def approval_reason(self, args: StartDevServerArgs) -> str:  # type: ignore[override]
        return tr("appr.dev_start", name=args.name, cmd=args.command)

    async def run(self, args: StartDevServerArgs, ctx: ToolContext) -> ToolResult:
        check_command(args.command)
        cwd = resolve_path(args.cwd, settings=ctx.settings, must_exist=True, must_be_dir=True)
        try:
            from core.secrets_store import load_env

            server = get_manager().start(
                args.name, args.command, cwd, env=load_env(ctx.settings.workspace)
            )
        except DevServerError as exc:
            return ToolResult.fail(str(exc))

        wait = max(0.0, min(args.wait_sec, _MAX_WAIT))
        if wait:
            await asyncio.sleep(wait)

        # Reading the first logs moves the read cursor, so read_dev_server returns only what
        # is new.
        first = server.read_new()
        if not server.is_running():
            body = "\n".join(first) or "(no output)"
            return ToolResult.fail(
                f"Server '{args.name}' exited at once (code {server.exit_code()}).\n{body}"
            )

        parts = [f"Server '{args.name}' started (pid {server.proc.pid}). First logs:"]
        parts.append("\n".join(first) if first else "(no logs yet)")
        errors = _format_errors(first)
        if errors:
            parts.append(errors)
        return ToolResult(content="\n\n".join(parts))


class ReadDevServerArgs(BaseModel):
    name: str = Field(description="The server's name given at start")
    wait_sec: float = Field(default=0.0, description="Seconds to wait for new logs when there are none yet (0–20)")


class ReadDevServerTool(Tool):
    name = "read_dev_server"
    description = (
        "Returns the dev server's NEW log lines since the last read and marks the ones that look "
        "like build or runtime errors. Use it after edits to check that the rebuild (HMR/reload) "
        "went through cleanly. wait_sec gives the server time to react."
    )
    Args = ReadDevServerArgs
    category = "read"
    timeout = None

    async def run(self, args: ReadDevServerArgs, ctx: ToolContext) -> ToolResult:
        try:
            server = get_manager().get(args.name)
        except DevServerError as exc:
            return ToolResult.fail(str(exc))

        wait = max(0.0, min(args.wait_sec, _MAX_WAIT))
        deadline = wait
        lines: list[str] = server.read_new()
        # Wait for logs in short steps, no longer than wait.
        while not lines and deadline > 0 and server.is_running():
            step = min(0.5, deadline)
            await asyncio.sleep(step)
            deadline -= step
            lines = server.read_new()

        header = f"Server '{args.name}'"
        if not server.is_running():
            header += f" EXITED (code {server.exit_code()})"

        if not lines:
            tail = server.tail(3)
            hint = "\nLast lines:\n" + "\n".join(tail) if tail else ""
            return ToolResult(content=f"{header}: no new logs.{hint}")

        parts = [f"{header}. New logs:", "\n".join(lines)]
        errors = _format_errors(lines)
        if errors:
            parts.append(errors)
        elif looks_ready(lines):
            parts.append("✅ The server seems to have built/restarted successfully.")
        return ToolResult(content="\n\n".join(parts))


class StopDevServerArgs(BaseModel):
    name: str = Field(description="The name of the server to stop")


class StopDevServerTool(Tool):
    name = "stop_dev_server"
    description = "Stops a dev server started earlier, by its name."
    Args = StopDevServerArgs
    category = "execute"
    dangerous = True
    timeout = None

    def approval_reason(self, args: StopDevServerArgs) -> str:  # type: ignore[override]
        return tr("appr.dev_stop", name=args.name)

    def auto_verdict(self, args: StopDevServerArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        # Stopping its own process is safe: no need to bother the user.
        return "allow"

    async def run(self, args: StopDevServerArgs, ctx: ToolContext) -> ToolResult:
        stopped = get_manager().stop(args.name)
        if not stopped:
            return ToolResult.fail(f"Server '{args.name}' was not found or is already stopped.")
        return ToolResult(content=f"Server '{args.name}' stopped.")


class ListDevServersTool(Tool):
    name = "list_dev_servers"
    description = "Lists the running dev servers: name, command, state, uptime."
    Args = EmptyArgs
    category = "read"

    async def run(self, args: EmptyArgs, ctx: ToolContext) -> ToolResult:
        servers = get_manager().all()
        if not servers:
            return ToolResult(content="No dev servers are running.")
        rows = []
        for s in servers:
            st = s.status()
            state = "running" if st["running"] else f"exited (code {st['exit_code']})"
            rows.append(
                f"• {st['name']}: {st['command']} — {state}, "
                f"uptime {st['uptime_sec']} s, {st['log_lines']} log lines"
            )
        return ToolResult(content="\n".join(rows))
