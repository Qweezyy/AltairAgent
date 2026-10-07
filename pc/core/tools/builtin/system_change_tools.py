"""safe_system_change: a change that could cut the way in, with its undo armed first.

sshd, the firewall, the network, sudoers: one wrong line and nobody can log in again — not the
user, not the agent. So such a change goes like a router's "commit confirmed":
  1. the files it will touch are copied aside and the undo is handed to the guardian (a separate
     process, core/servers/guardian.py) *before* anything is applied;
  2. the commands run, then the checks (`sshd -t`, `nft -c …`); a failure undoes it at once;
  3. it stays only if a *new* SSH login from the PC works afterwards (the PC does that by itself
     right after this tool, core/bodies_routing.py). Nobody confirms within the window — the
     guardian undoes it, even if the agent itself is gone.

Linux with the guardian installed (a server body). On other machines it refuses and says why.
"""

from __future__ import annotations

import asyncio
import json
import platform
import shlex
import shutil
import time
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from core.errors import ToolError
from core.tools.base import Tool, ToolContext, ToolResult

MIN_WINDOW_S, MAX_WINDOW_S = 60, 600


class SafeChangeArgs(BaseModel):
    title: str = Field(description="What the change does, in a few words (shown to the user and in the Journal).")
    apply: list[str] = Field(description="Shell commands that make the change, run in order.")
    files: list[str] = Field(default_factory=list,
                             description="Every file the commands create or change: copied aside first, put back on undo.")
    rollback: list[str] = Field(default_factory=list,
                                description="Commands that undo it after the files are put back, e.g. 'systemctl reload ssh'.")
    check: list[str] = Field(default_factory=list,
                             description="Commands that must succeed after applying, e.g. 'sshd -t'. A failure undoes it at once.")
    confirm_within: int = Field(default=120, description=f"Seconds to confirm it with a new login ({MIN_WINDOW_S}–{MAX_WINDOW_S}).")


async def _sh(command: str, timeout: float = 300) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec("sh", "-c", command, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, f"timed out after {int(timeout)} s"
    return proc.returncode or 0, out.decode("utf-8", errors="replace")[-1500:]


def _guardian(app_dir: Path) -> tuple[Path, str]:
    """The guardian's folder and script, or ToolError when there is none to rely on. The guardian
    works in APP_PATH (the app folder), not in the data folder under it."""
    folder = app_dir / "guardian"
    try:
        script = (folder / "script.path").read_text(encoding="utf-8").strip()
    except OSError:
        script = ""
    if not script or not Path(script).exists():
        raise ToolError("No guardian runs on this machine, so nothing could undo the change if it cut the way in. "
                        "safe_system_change is for the user's servers (where Altair installed its guardian); "
                        "here, make the change with execute_command after telling the user the risk.")
    return folder, script


class SafeSystemChangeTool(Tool):
    name = "safe_system_change"
    description = (
        "Change sshd, the firewall, the network or sudoers on a Linux server without risking the way in: "
        "the files are copied aside and the undo is armed in the guardian first, then the commands and checks "
        "run, and the change is kept only if a new SSH login from the PC works afterwards — otherwise it is "
        "undone by itself within confirm_within seconds. Use it instead of execute_command for such changes "
        "(run it on the server with `body`)."
    )
    Args = SafeChangeArgs
    category = "execute"
    dangerous = True
    timeout = 900.0

    def auto_verdict(self, args: BaseModel, ctx: ToolContext) -> str:
        return "ask"            # a system change is the owner's call, unless they chose "no confirmations"

    def approval_reason(self, args: SafeChangeArgs) -> str:
        from core.i18n import tr

        return tr("appr.system_change", title=args.title, commands="; ".join(args.apply)[:400],
                  seconds=max(MIN_WINDOW_S, min(MAX_WINDOW_S, args.confirm_within)))

    async def run(self, args: SafeChangeArgs, ctx: ToolContext) -> ToolResult:
        if platform.system() != "Linux":
            raise ToolError("safe_system_change works on Linux servers: run it there with `body`.")
        if not args.apply:
            raise ToolError("Nothing to apply: give the commands in `apply`.")
        folder, script = _guardian(Path(ctx.settings.app_dir))
        window = max(MIN_WINDOW_S, min(MAX_WINDOW_S, int(args.confirm_within)))
        cid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        changes = folder / "changes"
        backup_dir = changes / cid
        backup_dir.mkdir(parents=True, exist_ok=True)
        backups: dict[str, str] = {}
        for i, name in enumerate(dict.fromkeys(args.files)):
            path = Path(name)
            if path.exists():
                copy = backup_dir / f"{i}-{path.name}"
                await asyncio.to_thread(shutil.copy2, path, copy)
                backups[str(path)] = str(copy)
            else:
                backups[str(path)] = ""             # created by the change: removed on undo
        deadline = time.time() + window
        record = {"title": args.title, "deadline": deadline, "backups": backups, "rollback": args.rollback,
                  "apply": args.apply, "created": time.time()}
        # Armed before anything changes: if the agent dies half-way, the guardian still undoes it.
        (changes / f"{cid}.json").write_text(json.dumps(record, indent=1), encoding="utf-8")

        log = []
        for command in args.apply:
            code, out = await _sh(command)
            log.append(f"$ {command}\n{out.strip()}\n(exit {code})")
            if code != 0:
                return await self._undo_now(script, cid, log, f"'{command}' failed (exit {code})")
        for command in args.check:
            code, out = await _sh(command, timeout=120)
            log.append(f"check $ {command}\n{out.strip()}\n(exit {code})")
            if code != 0:
                return await self._undo_now(script, cid, log, f"the check '{command}' failed (exit {code})")
        text = (f"Applied: {args.title}. It is NOT kept yet: it must be confirmed with a new SSH login within "
                f"{window} s, or the guardian undoes it by itself (change {cid}).\n\n" + "\n\n".join(log))
        return ToolResult(content=text, metadata={"change_id": cid, "needs_confirmation": True,
                                                  "confirm_by": deadline, "guardian": script})

    @staticmethod
    async def _undo_now(script: str, cid: str, log: list[str], why: str) -> ToolResult:
        code, out = await _sh(f"python3 {shlex.quote(script)} undo {cid} {shlex.quote(why)}")
        state = "undone: the files are back and the rollback commands ran" if code == 0 else f"NOT undone ({out.strip()})"
        return ToolResult.fail(f"Not applied — {why}; the change was {state}.\n\n" + "\n\n".join(log),
                               change_id=cid, undone=code == 0)
