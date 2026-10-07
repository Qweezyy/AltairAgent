"""Moving a chat to a server: the work goes on there with this PC switched off.

    POST /api/sessions/{id}/move   (this PC) the chat, its reminders and, if asked, its folder go
                                   to the server; here it goes to the trash (restorable)
    POST /api/sessions/import      (the server, through the tunnel) takes the chat in and, if
                                   asked, goes on with it at once

A running chat is stopped here first and continued there: its history is the same, so the agent
picks the task up where it stopped. The folder goes over SSH (SFTP), not through the API: a
project can be large. Folders that are rebuilt from the project itself (node_modules, virtual
environments, caches) are left out, and the reply names them. Background jobs stay: they are
processes of this PC.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import tarfile
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request

from core.i18n import tr
from core.logging_setup import get_logger

logger = get_logger("server.chat_move")

#: Rebuilt from the project itself: not carried over (and named in the reply).
SKIP_DIRS = frozenset({"node_modules", ".venv", "venv", "env", "__pycache__", ".mypy_cache", ".pytest_cache",
                       ".ruff_cache", ".tox", ".gradle", ".next", ".nuxt", ".parcel-cache", ".turbo"})
CONTINUE_TEXT = ("The chat was moved to this server and goes on here. Continue the task from where it "
                 "stopped; the project folder is now {folder}.")
_LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


def pack_folder(folder: Path) -> tuple[Path, int, list[str]]:
    """The folder as a .tar.gz in a temp file: (path, bytes packed, skipped folder names)."""
    skipped: set[str] = set()
    fd, name = tempfile.mkstemp(suffix=".tar.gz", prefix="altair-move-")
    os.close(fd)
    out = Path(name)
    total = 0
    with tarfile.open(out, "w:gz") as tar:
        for root, dirs, files in os.walk(folder):
            for d in list(dirs):
                if d in SKIP_DIRS:
                    skipped.add(d)
                    dirs.remove(d)
            for f in files:
                path = Path(root) / f
                try:
                    tar.add(path, arcname=str(path.relative_to(folder)).replace("\\", "/"), recursive=False)
                    total += path.stat().st_size
                except OSError as exc:
                    logger.info("move: %s not packed (%s)", path, exc)
    return out, total, sorted(skipped)


def install(app: FastAPI) -> None:
    def local(request: Request) -> None:
        if (request.client.host if request.client else "") not in _LOOPBACK:
            raise HTTPException(status_code=403, detail=tr("api.local_only"))

    @app.post("/api/sessions/{sid}/move")
    async def session_move(request: Request, sid: str, payload: dict) -> dict:
        local(request)
        from core.agent.storage import SessionStore
        from core.reminders import ReminderStore

        settings = app.state.settings
        tunnels = getattr(app.state, "tunnels", None)
        target = str(payload.get("body") or "")
        tunnel = tunnels.get(target) if tunnels is not None else None
        if tunnel is None:
            raise HTTPException(status_code=404, detail="no such server")
        if tunnel.state != "online" or not tunnel.local_port:
            return {"ok": False, "error": tr("body.offline", name=tunnel.record.name or tunnel.record.host)}
        store: SessionStore = app.state.store
        live = app.state.chats.chats.get(sid) if hasattr(app.state, "chats") else None
        was_running = bool(live is not None and live.running)
        if live is not None and live.running:
            await live.stop()
            await asyncio.gather(live.run_task, return_exceptions=True)
        if live is not None:
            await live._save_session()
        session = await asyncio.to_thread(store.load, sid)
        if session is None:
            raise HTTPException(status_code=404, detail="no such chat")

        default_ws = str((tunnel.status or {}).get("workspace") or "/var/lib/altair/workspace").rstrip("/")
        local_folder = Path(session.workspace) if session.workspace else None
        copy = bool(payload.get("copy_folder")) and local_folder is not None and local_folder.is_dir()
        folder = str(payload.get("folder") or "").strip() or (
            f"{default_ws}/{local_folder.name}" if copy and local_folder is not None else default_ws)

        copied: dict[str, Any] = {}
        if copy and local_folder is not None:
            try:
                copied = await _copy_folder(settings, tunnel.record, local_folder, folder)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the reason is shown; nothing was moved yet
                logger.exception("move: copying the folder failed")
                return {"ok": False, "error": tr("move.copy_failed", why=str(exc) or type(exc).__name__)}

        reminders = [r for r in await asyncio.to_thread(ReminderStore(settings.data_dir).active, sid) if r.kind != "job"]
        text = str(payload.get("continue_with") or "").strip()
        if not text and (was_running or payload.get("continue")):
            text = CONTINUE_TEXT.format(folder=folder)
        body = {"session": session.to_dict(), "reminders": [asdict(r) for r in reminders], "folder": folder,
                "continue_with": text}
        try:
            async with httpx.AsyncClient(timeout=60) as http:
                r = await http.post(f"http://127.0.0.1:{tunnel.local_port}/api/sessions/import", json=body)
            reply = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        except httpx.HTTPError as exc:
            return {"ok": False, "error": tr("body.unreachable", why=str(exc) or type(exc).__name__)}
        if r.status_code != 200 or not reply.get("ok"):
            return {"ok": False, "error": reply.get("error") or reply.get("detail") or f"HTTP {r.status_code}"}

        # It lives there now: here it goes to the trash (restorable), its reminders with it.
        if live is not None:
            live.deleted = True
            for viewer in list(live.viewers):
                try:
                    await viewer.send({"type": "session.moved", "session_id": sid, "body": tunnel.record.id,
                                       "name": tunnel.record.name or tunnel.record.host})
                except (OSError, RuntimeError) as exc:
                    logger.debug("move: a window did not get the news: %s", exc)
        app.state.chats.forget(sid)
        await store.async_delete(sid)
        # All its reminders leave this PC: the moved ones live there now, and the watches of this
        # PC's background jobs have no chat here to wake any more (the jobs themselves go on).
        reminder_store = ReminderStore(settings.data_dir)
        # Not only the active ones: a fired one not yet taken by the chat would wake it here.
        here = [r for r in await asyncio.to_thread(reminder_store.all) if r.session_id == sid]
        for rem in here:
            await asyncio.to_thread(reminder_store.remove, rem.id, sid)
        dropped_watches = [r.job or r.note for r in here if r.kind == "job"]
        from core.journal import get_journal

        if getattr(settings, "journal", True):
            await asyncio.to_thread(get_journal(settings.data_dir / "journal").append, "chat.moved", chat=sid,
                                    to=tunnel.record.name or tunnel.record.host, folder=folder,
                                    copied=copied.get("bytes", 0), continued=bool(text))
        return {"ok": True, "body": tunnel.record.id, "chat": sid, "folder": reply.get("folder") or folder,
                "copied": copied, "continued": bool(text), "reminders": len(reminders),
                "dropped_watches": dropped_watches}

    @app.post("/api/sessions/import")
    async def session_import(request: Request, payload: dict) -> dict:
        """A chat moved here from another body (only through the tunnel: this machine itself)."""
        local(request)
        from core.agent.session import Session
        from core.agent.storage import SessionStore
        from core.errors import ConfigError
        from core.reminders import Reminder, ReminderStore

        settings = app.state.settings
        data = payload.get("session")
        if not isinstance(data, dict) or not data.get("id"):
            raise HTTPException(status_code=400, detail="session: the chat as to_dict() gives it")
        folder = str(payload.get("folder") or "").strip() or str(settings.workspace)
        try:
            chat_settings = await asyncio.to_thread(settings.for_workspace, folder)
        except ConfigError as exc:
            return {"ok": False, "error": f"The folder '{folder}' cannot be used here: {exc}"}
        session = Session.from_dict(data)
        session.workspace = str(chat_settings.workspace)
        session.approval_mode = ""          # this body's own mode (chosen for the server) applies
        store: SessionStore = app.state.store
        await asyncio.to_thread(store.save, session)
        reminders = ReminderStore(settings.data_dir)
        for raw in payload.get("reminders") or []:
            try:
                await asyncio.to_thread(reminders.add, Reminder(**{**raw, "session_id": session.id}))
            except (TypeError, ValueError) as exc:
                logger.info("import: a reminder was not taken (%s)", exc)
        if getattr(settings, "journal", True):
            from core.journal import get_journal

            await asyncio.to_thread(get_journal(settings.data_dir / "journal").append, "chat.arrived",
                                    chat=session.id, folder=session.workspace)
        text = str(payload.get("continue_with") or "").strip()
        if text:
            chat, _warning = await app.state.chats.open(session.id)
            if chat is not None and not chat.running:
                app.state.chats.chats[session.id] = chat      # kept alive while it works, no window
                chat.launch(text, None)
        return {"ok": True, "folder": session.workspace}


async def _copy_folder(settings: Any, record: Any, local_folder: Path, folder: str) -> dict[str, Any]:
    """Packs the folder here, puts it on the server over SFTP and unpacks it into `folder`."""
    from core.servers.preflight import run_preflight
    from core.servers.registry import ServerStore
    from core.servers.remote import Login, RemoteError, SSHRemote

    packed, size, skipped = await asyncio.to_thread(pack_folder, local_folder)
    remote_tmp = f"/tmp/altair-move-{uuid.uuid4().hex[:10]}.tar.gz"
    login = Login(host=record.host, port=record.port, user=record.user,
                  key_path=str(ServerStore(settings.data_dir).key_path(record.id)), host_key=record.host_key)
    try:
        async with SSHRemote(login) as remote:
            pre = await run_preflight(remote)
            remote.sudo = {"root": "", "nopass": "sudo -n"}.get(pre.sudo, remote.sudo)
            await remote.put(packed, remote_tmp)
            q = shlex.quote(folder)
            result = await remote.run(f"mkdir -p {q} && tar -xzf {remote_tmp} -C {q}; s=$?; rm -f {remote_tmp}; exit $s",
                                      root=True, timeout=1800)
            if not result.ok:
                raise RemoteError(result.err.strip()[-300:] or f"tar exit {result.code}")
    finally:
        packed.unlink(missing_ok=True)
    return {"bytes": size, "skipped": skipped, "to": folder}

