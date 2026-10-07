"""self_check: the agent looks at itself — what runs, what its guardian did, what failed lately.

On a server it reads the services (the agent and its guardian), the guardian's state (a pending
update, pending system changes, its last events), the disk and memory, the Journal's recent
failures and the errors in its own log; on a PC the same without the services and the guardian.
It ends with the problems it found, so the agent can fix what is its own to fix (in the "Owner"
mode) and report the rest. Reinstalling itself is only ever from a signed release (Settings →
Servers → Update, from the PC).
"""

from __future__ import annotations

import asyncio
import json
import platform
import shutil
import time
from pathlib import Path
from typing import Any

from core.tools.base import EmptyArgs, Tool, ToolContext

LOG_ERRORS = 15
#: Guardian events younger than this count as problems now; older ones are shown as history.
RECENT_H = 24
_FAILED = ("fail", "error", "rolled_back", "rollback", "restarted", "impossible")


async def _run(*argv: str, timeout: float = 20) -> tuple[int, str]:
    if not shutil.which(argv[0]):
        return 127, ""
    proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, ""
    return proc.returncode or 0, out.decode("utf-8", errors="replace").strip()


def _log_errors(logs: Path) -> list[str]:
    path = logs / "agent.log"
    try:
        with path.open("rb") as fh:
            fh.seek(max(0, path.stat().st_size - 400_000))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [line[:300] for line in lines if " [ERROR] " in line or " [CRITICAL] " in line][-LOG_ERRORS:]


class SelfCheckTool(Tool):
    name = "self_check"
    description = (
        "Check yourself: version, your services and guardian (on a server), a pending update or system change, "
        "disk and memory, recent failures in the Journal and errors in your log — ending with the problems "
        "found. Use it when something of yours misbehaves, after an update, or when asked how you are; run it "
        "on a server with `body`."
    )
    Args = EmptyArgs
    category = "read"
    timeout = 60.0

    async def run(self, args: EmptyArgs, ctx: ToolContext) -> str:
        from core.body_status import facts, load
        from core.version import __version__

        settings = ctx.settings
        data = Path(settings.data_dir)
        problems: list[str] = []
        out: list[str] = [f"Altair {__version__} · {settings.body_kind} · {platform.system()} {platform.machine()}"]

        st = {**await asyncio.to_thread(facts), **await asyncio.to_thread(load, data)}
        free_gb = st["disk_free_mb"] / 1024
        mem_free = (st["mem_mb"] - st["mem_used_mb"]) / 1024
        out.append(f"Disk: {free_gb:.1f} GB free of {st['disk_total_mb'] / 1024:.1f} GB · memory: {mem_free:.1f} GB "
                   f"free of {st['mem_mb'] / 1024:.1f} GB · CPU {st['cpu_pct']:.0f}%")
        if free_gb < 2:
            problems.append(f"low disk: {free_gb:.1f} GB free")
        if st["mem_mb"] and mem_free / (st["mem_mb"] / 1024) < 0.1:
            problems.append(f"low memory: {mem_free:.1f} GB free")

        if platform.system() == "Linux" and shutil.which("systemctl"):
            states = []
            for unit in ("altair", "altair-guardian"):
                _code, state = await _run("systemctl", "is-active", unit)
                states.append(f"{unit}: {state or 'unknown'}")
                if unit == "altair-guardian" and settings.body_kind == "server" and state != "active":
                    problems.append("the guardian is not running: a bad update or system change would not be undone")
            out.append("Services: " + ", ".join(states))

        guardian = Path(settings.app_dir) / "guardian"      # APP_PATH, where the guardian works
        script = ""
        try:
            script = (guardian / "script.path").read_text(encoding="utf-8").strip()
        except OSError:
            script = ""
        if script and Path(script).exists():
            code, status_text = await _run("python3", script, "status")
            try:
                status: dict[str, Any] = json.loads(status_text) if code == 0 else {}
            except ValueError:
                status = {}
            if status:
                out.append(f"Release: {status.get('current')} · kept: {', '.join(status.get('releases') or [])}")
                if status.get("pending_update"):
                    out.append(f"Pending update (kept once it answers): {status['pending_update'].get('new')}")
                if status.get("pending_changes"):
                    problems.append("system changes waiting for confirmation: " + ", ".join(status["pending_changes"]))
        events_file = guardian / "events.jsonl"
        if events_file.exists():
            lines = (await asyncio.to_thread(events_file.read_text, encoding="utf-8")).splitlines()[-8:]
            shown = []
            for line in lines:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                ago = (time.time() - float(e.get("ts") or 0)) / 3600
                shown.append(f"  {ago:.0f} h ago · {e.get('kind')}: " + ", ".join(f"{k}={v}" for k, v in e.items()
                                                              if k not in ("ts", "kind") and v not in ("", [], None))[:200])
                # Only what is recent is a problem now; older events are history (already handled).
                age_h = (time.time() - float(e.get("ts") or 0)) / 3600
                if age_h < RECENT_H and any(w in str(e.get("kind")) for w in ("rolled_back", "restarted", "impossible", "error")):
                    problems.append(f"the guardian reported {e.get('kind')} {age_h:.0f} h ago")
            if shown:
                out.append("Guardian, latest:\n" + "\n".join(shown))

        if getattr(settings, "journal", True):
            from core.journal import get_journal

            records = await asyncio.to_thread(get_journal(data / "journal").read, limit=300)
            failed = [r for r in records if any(w in r.get("kind", "") for w in _FAILED)][:10]
            if failed:
                out.append("Journal, recent failures:\n" + "\n".join(
                    f"  {r['kind']}: " + json.dumps(r.get("data") or {}, ensure_ascii=False)[:200] for r in failed))

        errors = await asyncio.to_thread(_log_errors, Path(settings.logs_dir))
        if errors:
            out.append(f"Log, last {len(errors)} errors:\n" + "\n".join("  " + e for e in errors))

        out.append("Problems: " + ("; ".join(dict.fromkeys(problems)) if problems else "none found."))
        return "\n".join(out)
