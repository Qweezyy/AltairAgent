"""What a server did while you were not looking at it, shown on this PC.

Every 15 s the PC reads the new records of each connected server's Journal through its tunnel and
picks the ones a person wants to know about at once: a task there finished or failed, the guardian
rolled back an update or a system change, the agent there was restarted. A server dropping off for
more than a minute and coming back is noticed here too. Each becomes a `notice` sent to every
window (a toast and a system notification; a click opens that chat on that server). The phone
gets the same once it connects to the server (0.3.0 stage 6); no other channel for now.

The first look at a server only marks where its Journal is: what happened before is history, not
news. The marks are kept per server, so a restart of the PC does not repeat old notices.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from core.fs_atomic import atomic_write_text
from core.i18n import tr
from core.logging_setup import get_logger

logger = get_logger("server.notices")

EVERY_S = 15.0
#: Gone this long before "not connected" is worth a notice (a reconnect is not news).
OFFLINE_AFTER_S = 60.0

#: Journal kinds worth a notice → (i18n key of the title, level).
NOTABLE: dict[str, tuple[str, str]] = {
    "run.finished": ("ntc.run_finished", "info"),
    "run.failed": ("ntc.run_failed", "error"),
    "guardian.update.rolled_back": ("ntc.update_rolled_back", "error"),
    "guardian.update.rollback_impossible": ("ntc.rollback_impossible", "error"),
    "guardian.change.rolled_back": ("ntc.change_rolled_back", "error"),
    "guardian.agent.restarted": ("ntc.agent_restarted", "warning"),
    "guardian.disk.cleaned": ("ntc.disk_cleaned", "info"),
}


def plain(text: str) -> str:
    """A notification shows plain text: links become their words, markdown marks go."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[`*_#>]+", "", text)
    return " ".join(text.split())


def notice_of(record: dict[str, Any], server: str) -> dict[str, Any] | None:
    """The notice for one Journal record of a server, or None if it is not news."""
    kind = str(record.get("kind") or "")
    if kind not in NOTABLE:
        return None
    key, level = NOTABLE[kind]
    data = record.get("data") or {}
    if kind == "run.finished":
        text = plain(str(data.get("answer") or ""))[:240]
    elif kind == "run.failed":
        text = str(data.get("message") or "")[:240]
    elif kind.startswith("guardian.change"):
        text = str(data.get("title") or "")
    elif kind == "guardian.update.rolled_back":
        text = str(data.get("reason") or "")
    elif kind == "guardian.disk.cleaned":
        text = tr("ntc.freed", mb=data.get("freed_mb", 0))
    else:
        text = str(data.get("reason") or "")
    return {"type": "notice", "kind": kind, "level": level, "body": server, "title": tr(key, name=server),
            "text": text, "chat": str(record.get("chat") or ""), "at": record.get("ts") or time.time()}


class Notices:
    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.marks_file = Path(app.state.settings.data_dir) / "bodies" / "notices.json"
        self.marks: dict[str, int] = self._load()
        self.gone_since: dict[str, float] = {}
        self.told_gone: set[str] = set()

    def _load(self) -> dict[str, int]:
        try:
            data = json.loads(self.marks_file.read_text(encoding="utf-8"))
            return {str(k): int(v) for k, v in data.items()}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.info("notice marks unreadable (%s): starting from now", exc)
            return {}

    def _save(self) -> None:
        self.marks_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.marks_file, json.dumps(self.marks))

    async def _send(self, notice: dict[str, Any]) -> None:
        chats = getattr(self.app.state, "chats", None)
        if chats is not None:
            await chats.broadcast(notice)

    async def tick(self) -> None:
        tunnels = getattr(self.app.state, "tunnels", None)
        changed = False
        now = time.time()
        for tunnel in getattr(tunnels, "all", list)():
            sid, name = tunnel.record.id, tunnel.record.name or tunnel.record.host
            up = tunnel.state == "online" and tunnel.agent == "ok"
            if not up:
                self.gone_since.setdefault(sid, now)
                if sid not in self.told_gone and now - self.gone_since[sid] >= OFFLINE_AFTER_S:
                    self.told_gone.add(sid)
                    await self._send({"type": "notice", "kind": "body.offline", "level": "warning", "body": name,
                                      "title": tr("ntc.offline", name=name), "text": tunnel.error or "", "chat": ""})
                continue
            self.gone_since.pop(sid, None)
            if sid in self.told_gone:
                self.told_gone.discard(sid)
                await self._send({"type": "notice", "kind": "body.online", "level": "info", "body": name,
                                  "title": tr("ntc.online", name=name), "text": "", "chat": ""})
            try:
                changed |= await self._read(tunnel, sid, name)
            except (httpx.HTTPError, ValueError) as exc:
                logger.debug("notices from %s: %s", name, exc)
        if changed:
            await asyncio.to_thread(self._save)

    async def _read(self, tunnel: Any, sid: str, name: str) -> bool:
        base = f"http://127.0.0.1:{tunnel.local_port}/api/journal"
        async with httpx.AsyncClient(timeout=15) as http:
            if sid not in self.marks:
                # The first look: where the Journal is now; what came before is history.
                latest = (await http.get(base, params={"limit": 1})).json().get("records") or []
                self.marks[sid] = int(latest[0]["seq"]) if latest else 0
                return True
            records = (await http.get(base, params={"since": self.marks[sid], "limit": 200})).json().get("records") or []
        if not records:
            return False
        for record in sorted(records, key=lambda r: int(r.get("seq") or 0)):
            notice = notice_of(record, name)
            if notice is not None:
                notice["body_id"] = sid
                await self._send(notice)
        self.marks[sid] = max(int(r.get("seq") or 0) for r in records)
        return True


async def notices_loop(app: FastAPI) -> None:
    notices = Notices(app)
    app.state.notices = notices
    while True:
        try:
            await notices.tick()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - notices must not stop; the next round tries again
            logger.exception("notices round failed")
        await asyncio.sleep(EVERY_S)
