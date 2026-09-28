"""General background commands (like run_in_background / BashOutput / KillShell), waits and
watches.

Unlike `execute_command` (waits for the end) and `start_dev_server` (made for dev servers),
these start ANY long command in the background and read its output as it comes: a build, a
long test run, a watcher, a backup. They reuse the process-wide manager of the dev servers
(`core/devserver/manager.py`): Popen with merged stdout/stderr, a reader thread into a ring
buffer, incremental reads and stopping the whole process tree (taskkill /T on Windows).

Waits and watches are durable (core/reminders.py): the scheduler delivers them to the chat
even when the task has ended, the user is in another chat, or the app was closed and started
again — a wait cut off by closing the app ends on the next start and wakes the chat.
"""

from __future__ import annotations

import asyncio
import itertools
import threading
import time
from pathlib import Path

from pydantic import BaseModel, Field

from core.devserver import get_manager
from core.devserver.manager import DevServerError
from core.events import LogEvent
from core.i18n import tr
from core.reminders import LIVE_WAITS, Reminder, ReminderStore, new_id
from core.tools.base import Tool, ToolContext, ToolResult
from core.tools.builtin.shell import (
    check_command,
    cwd_outside_workspace,
    is_read_only_command,
    resolve_command_cwd,
)

#: The longest a tool waits for fresh output in one call.
_MAX_WAIT = 20.0

#: The cap of one wait_for (30 minutes): longer waits are reminders (set_reminder).
_WAIT_CAP = 1800.0

#: Watches last at most this long (a day): a forgotten watch must not stay forever.
_WATCH_CAP = 86400.0

_counter = itertools.count(1)
_counter_lock = threading.Lock()


def _auto_name() -> str:
    with _counter_lock:
        return f"job-{next(_counter)}"


def _job_ref(name: str) -> str:
    """Tells this run of the job from a later one with the same name (after a restart the
    counter starts again): the pid it runs under."""
    try:
        return str(get_manager().get(name).proc.pid)
    except (DevServerError, AttributeError):
        return ""


class RunBackgroundArgs(BaseModel):
    command: str = Field(description="Any non-interactive command: a build, tests, a watcher…")
    name: str = Field(default="", description="A short name to refer to it later (empty: one is assigned)")
    cwd: str = Field(
        default=".",
        description="Folder to run in: relative to the workspace, or an absolute path (outside it needs approval)",
    )
    wait_sec: float = Field(default=1.0, description="Seconds to wait for the first output (0–20)")
    notify: bool = Field(
        default=False,
        description="Wake you in this chat when it ends (use it for builds/tests you would otherwise poll)",
    )


class RunBackgroundTool(Tool):
    name = "run_background"
    description = (
        "Starts a command in the BACKGROUND and returns at once. For long jobs (a build, a long test run, "
        "a watcher, a backup). notify=true wakes you when it ends, so you do not poll; otherwise read "
        "its output with read_background and stop it with stop_background. For quick commands use "
        "execute_command, for dev servers start_dev_server."
    )
    Args = RunBackgroundArgs
    category = "execute"
    dangerous = True
    timeout = None

    def approval_reason(self, args: RunBackgroundArgs) -> str:  # type: ignore[override]
        text = tr("appr.bg_run", name=args.name or tr("appr.auto"), cmd=args.command)
        cwd = (args.cwd or ".").strip()
        if cwd not in (".", "") and (Path(cwd).is_absolute() or ".." in cwd):
            text += tr("appr.in_folder", cwd=cwd)
        return text

    def auto_verdict(self, args: RunBackgroundArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        # Same rule as execute_command: read-only runs silently, but never outside the workspace.
        if cwd_outside_workspace(args.cwd, ctx) is not None:
            return "ask"
        return "allow" if is_read_only_command(args.command) else "ask"

    async def run(self, args: RunBackgroundArgs, ctx: ToolContext) -> ToolResult:
        check_command(args.command)
        cwd, _ = resolve_command_cwd(args.cwd, ctx)
        name = args.name.strip() or _auto_name()

        from core.secrets_store import load_env

        try:
            job = get_manager().start(
                name, args.command, cwd, env=load_env(ctx.settings.workspace)
            )
        except DevServerError as exc:
            return ToolResult.fail(str(exc))

        wait = max(0.0, min(args.wait_sec, _MAX_WAIT))
        if wait:
            await asyncio.sleep(wait)

        first = job.read_new()
        if not job.is_running():
            body = "\n".join(first) or "(no output)"
            ok = job.exit_code() == 0
            verdict = "successfully" if ok else f"with exit code {job.exit_code()}"
            return ToolResult(content=f"Background command '{name}' has already finished {verdict}.\n{body}", ok=ok)

        parts = [f"Background command '{name}' started (pid {job.proc.pid}). Read its output: "
                 f"read_background name=\"{name}\"."]
        if args.notify:
            _watch_job(ctx, name, "")
            parts[0] += " You will be woken in this chat when it ends — no need to poll."
        parts.append("\n".join(first) if first else "(no output yet)")
        return ToolResult(content="\n\n".join(parts))


def _watch_job(ctx: ToolContext, name: str, note: str) -> Reminder:
    reminder = Reminder(
        id=new_id(), kind="job", note=note, session_id=ctx.run_id, job=name, value=_job_ref(name),
        expires_at=time.time() + _WATCH_CAP,
    )
    return ReminderStore(ctx.settings.data_dir).add(reminder)


class ReadBackgroundArgs(BaseModel):
    name: str = Field(description="The background command's name, given at start")
    wait_sec: float = Field(default=0.0, description="Seconds to wait for new output (0–20)")


class ReadBackgroundTool(Tool):
    name = "read_background"
    description = (
        "Returns the NEW output of a background command since the last read and its status "
        "(running / finished with a code). Use it to follow progress and catch the result."
    )
    Args = ReadBackgroundArgs
    category = "read"
    timeout = None

    async def run(self, args: ReadBackgroundArgs, ctx: ToolContext) -> ToolResult:
        try:
            job = get_manager().get(args.name)
        except DevServerError as exc:
            return ToolResult.fail(str(exc))

        deadline = max(0.0, min(args.wait_sec, _MAX_WAIT))
        lines = job.read_new()
        while not lines and deadline > 0 and job.is_running():
            step = min(0.5, deadline)
            await asyncio.sleep(step)
            deadline -= step
            lines = job.read_new()

        header = f"Command '{args.name}'"
        header += f" FINISHED (exit code {job.exit_code()})" if not job.is_running() else " is running"

        if not lines:
            tail = job.tail(3)
            hint = "\nLast lines:\n" + "\n".join(tail) if tail else ""
            return ToolResult(content=f"{header}: no new output.{hint}")

        return ToolResult(content=f"{header}. New output:\n" + "\n".join(lines))


class WaitForArgs(BaseModel):
    seconds: float = Field(default=0.0, description="Wait this many seconds (a timer). 0 = not by time")
    background: str = Field(default="", description="A background job's name: wait until it ends (instead of a timer)")
    reason: str = Field(default="", description="Why we wait — shown to the user, and to you if the wait is cut off")
    timeout: float = Field(default=_WAIT_CAP, ge=1, le=_WAIT_CAP, description="The longest to wait, seconds")


class WaitForTool(Tool):
    name = "wait_for"
    description = (
        "Pauses the task, then continues it. Either wait N seconds (\"check again in 2 minutes\") or until "
        "a background job ends (background=name, e.g. a long build). Then you go on and check the result. "
        "The wait survives switching chats and closing the app: if the app closes meanwhile, you are woken "
        "on the next start when the time is up. For hours or days use set_reminder instead."
    )
    Args = WaitForArgs
    category = "read"
    timeout = None

    async def _note(self, ctx: ToolContext, text: str) -> None:
        try:
            await ctx.emitter(LogEvent(text=text, level="info"))
        except Exception:  # noqa: BLE001 - a status line must not break the wait
            return

    async def run(self, args: WaitForArgs, ctx: ToolContext) -> ToolResult:
        cap = max(1.0, min(args.timeout, _WAIT_CAP))
        reason = args.reason.strip()
        tag = f" — {reason}" if reason else ""
        store = ReminderStore(ctx.settings.data_dir)

        if args.background.strip():
            name = args.background.strip()
            try:
                job = get_manager().get(name)
            except DevServerError as exc:
                return ToolResult.fail(str(exc))
            # Durable: if the app closes during the wait, the chat is told on the next start.
            record = store.add(Reminder(
                id=new_id(), kind="job", note=reason or "continue the task", session_id=ctx.run_id,
                job=name, value=_job_ref(name),
            ))
            await self._note(ctx, f"⏳ Waiting for '{name}' to finish{tag}…")
            waited = await self._sleep(record, ctx, cap, lambda: not job.is_running())
            if job.is_running():
                tail = "\n".join(job.tail(5))
                return ToolResult(content=(
                    f"'{name}' is still running after {int(waited)} s (the wait's limit). Wait again with "
                    f"wait_for, or check read_background. Last lines:\n{tail}"))
            code = job.exit_code()
            tail = "\n".join(job.tail(8))
            verdict = "successfully" if code == 0 else f"with exit code {code}"
            return ToolResult(
                content=(f"'{name}' finished {verdict} after ~{int(waited)} s of waiting. Check the result "
                         f"now.\nLast lines of its output:\n{tail or '(empty)'}"),
                ok=code == 0,
            )

        delay = min(args.seconds, cap)
        if delay <= 0:
            return ToolResult.fail("give seconds (a timer) or background (a job to wait for); both are empty")
        record = store.add(Reminder(
            id=new_id(), kind="wait", note=reason or "continue the task", session_id=ctx.run_id,
            fire_at=time.time() + delay,
        ))
        await self._note(ctx, f"⏳ Waiting {int(delay)} s{tag}…")
        await self._sleep(record, ctx, delay, lambda: False)
        return ToolResult(content=f"{int(delay)} s have passed{tag}. Go on: check what you waited for.")

    async def _sleep(self, record: Reminder, ctx: ToolContext, cap: float, done) -> float:
        """Sleeps until `done()` or the cap. The durable record goes when the wait ends here; it
        stays when the app closes mid-wait, so the scheduler ends the wait after the restart."""
        store = ReminderStore(ctx.settings.data_dir)
        LIVE_WAITS.add(record.id)
        started = time.monotonic()
        try:
            while not done():
                left = cap - (time.monotonic() - started)
                if left <= 0:
                    break
                await asyncio.sleep(min(1.0, left))
        except asyncio.CancelledError:
            if not ctx.scratch.get("_shutdown"):
                store.remove(record.id)  # the user stopped the task: the wait goes with it
            raise
        finally:
            LIVE_WAITS.discard(record.id)
        store.remove(record.id)
        return time.monotonic() - started


class WatchBackgroundArgs(BaseModel):
    seconds: float = Field(default=0.0, description="Notify after this many seconds (a timer). 0 = not by time")
    background: str = Field(default="", description="A background job's name: notify when it ends")
    note: str = Field(default="", description="What to check or do when it fires")
    timeout: float = Field(default=_WATCH_CAP, ge=1, le=_WATCH_CAP, description="The longest to watch, seconds")


class WatchBackgroundTool(Tool):
    name = "watch_background"
    description = (
        "Does NOT block: sets a background watch and returns at once, so you keep working. When the time "
        "passes (seconds) OR a background job ends (background=name), you get a notice at the next step "
        "boundary — or, if you have finished by then, you are woken in this chat with it (also after "
        "the user switched chats or the app was restarted). Unlike wait_for, which stops you and waits."
    )
    Args = WatchBackgroundArgs
    category = "read"
    timeout = None

    async def run(self, args: WatchBackgroundArgs, ctx: ToolContext) -> ToolResult:
        note = args.note.strip()
        tail = f" Note: {note}." if note else ""

        if args.background.strip():
            name = args.background.strip()
            try:
                get_manager().get(name)  # the job must exist
            except DevServerError as exc:
                return ToolResult.fail(str(exc))
            _watch_job(ctx, name, note)
            return ToolResult(content=f"Watching '{name}' in the background — you will be told when it ends; "
                                      f"keep working.{tail}")

        delay = min(args.seconds, max(1.0, min(args.timeout, _WATCH_CAP)))
        if delay <= 0:
            return ToolResult.fail("give seconds (a timer) or background (a job to watch)")
        ReminderStore(ctx.settings.data_dir).add(Reminder(
            id=new_id(), kind="time", note=note or f"the {int(delay)} s background timer is up",
            session_id=ctx.run_id, fire_at=time.time() + delay,
        ))
        return ToolResult(content=f"Background timer set for {int(delay)} s — you will be told when it is up; "
                                  f"keep working.{tail}")


class StopBackgroundArgs(BaseModel):
    name: str = Field(description="The background command's name to stop")


class StopBackgroundTool(Tool):
    name = "stop_background"
    description = "Stops a background command by its name (the whole process tree)."
    Args = StopBackgroundArgs
    category = "execute"
    dangerous = True
    timeout = None

    def approval_reason(self, args: StopBackgroundArgs) -> str:  # type: ignore[override]
        return tr("appr.bg_stop", name=args.name)

    def auto_verdict(self, args: StopBackgroundArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        # Stopping one's own process is safe.
        return "allow"

    async def run(self, args: StopBackgroundArgs, ctx: ToolContext) -> ToolResult:
        stopped = get_manager().stop(args.name)
        if not stopped:
            return ToolResult.fail(f"Background command '{args.name}' not found or already stopped.")
        # A stopped job is not "finished": its watches would only wake the chat for nothing.
        store = ReminderStore(ctx.settings.data_dir)
        for r in store.active(ctx.run_id):
            if r.kind == "job" and r.job == args.name:
                store.remove(r.id)
        return ToolResult(content=f"Background command '{args.name}' stopped.")
