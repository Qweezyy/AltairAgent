"""The PC ↔ server channel: one SSH tunnel per server, kept up by itself.

The server listens only on its own 127.0.0.1 (core/servers/install.py); the PC reaches it by
forwarding a local port over the SSH connection the installer set up (this PC's key, the pinned host
key). Nothing is opened to the outside, so it works behind NAT and any firewall that lets SSH in.

The tunnel reconnects by itself with a growing pause (a sleeping laptop, a reboot of the server, a
dropped network); keepalives notice a dead link. Every 15 s it asks the agent there for its status
(core/body_status.py): a tunnel can be up while the agent is not (it is being updated, it crashed
and systemd restarts it) — the two are reported apart.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from typing import Any

from core.logging_setup import get_logger
from core.servers import remote as remote_module
from core.servers.registry import ServerRecord, ServerStore
from core.servers.remote import Login, RemoteError, SSHRemote

logger = get_logger("servers.tunnel")

HEARTBEAT_S = 15.0
#: Pauses between reconnects; the last one repeats.
BACKOFF_S = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)

FetchStatus = Callable[[int], Awaitable[dict[str, Any]]]


async def fetch_status(port: int) -> dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=8) as http:
        r = await http.get(f"http://127.0.0.1:{port}/api/body/status")
        r.raise_for_status()
        return r.json()


class Tunnel:
    def __init__(self, store: ServerStore, record: ServerRecord,
                 remote_factory: Callable[[Login], SSHRemote] | None = None,
                 fetch: FetchStatus | None = None, heartbeat_s: float = HEARTBEAT_S,
                 backoff: tuple[float, ...] = BACKOFF_S) -> None:
        self.store = store
        self.record = record
        self.remote_factory = remote_factory
        self.fetch = fetch or fetch_status
        self.heartbeat_s = heartbeat_s
        self.backoff = backoff
        #: "connecting" | "online" | "offline"
        self.state = "connecting"
        #: Whether the agent there answers: "ok" | "down" | "" (not known yet).
        self.agent = ""
        self.error = ""
        self.local_port = 0
        self.status: dict[str, Any] | None = None
        self.last_seen = 0.0
        self.online_since = 0.0
        self.attempts = 0
        self._wake = asyncio.Event()
        self._changed: list[Callable[[Tunnel], None]] = []

    def on_change(self, callback: Callable[[Tunnel], None]) -> None:
        self._changed.append(callback)

    def _notify(self) -> None:
        for callback in self._changed:
            try:
                callback(self)
            except Exception:  # noqa: BLE001 - a listener must not take the tunnel down
                logger.exception("tunnel listener failed")

    def reconnect_now(self) -> None:
        """Skips the pause before the next attempt (the user pressed "reconnect")."""
        self._wake.set()

    def public(self) -> dict[str, Any]:
        return {"state": self.state, "agent": self.agent, "error": self.error, "port": self.local_port,
                "status": self.status, "last_seen": self.last_seen, "online_since": self.online_since}

    def _login(self) -> Login:
        r = self.record
        return Login(host=r.host, port=r.port, user=r.user, key_path=str(self.store.key_path(r.id)),
                     host_key=r.host_key)

    async def run(self) -> None:
        while True:
            self.state = "connecting"
            self._notify()
            try:
                factory = self.remote_factory or remote_module.SSHRemote
                async with factory(self._login()) as remote:
                    await self._serve(remote)
                self.error = ""
            except asyncio.CancelledError:
                raise
            except (RemoteError, OSError) as exc:
                self.error = str(exc) or type(exc).__name__
            except Exception as exc:  # noqa: BLE001 - asyncssh's own errors: shown, then retried
                self.error = str(exc) or type(exc).__name__
            self.state, self.agent, self.online_since = "offline", "", 0.0
            self._notify()
            pause = self.backoff[min(self.attempts, len(self.backoff) - 1)]
            self.attempts += 1
            logger.info("tunnel to %s down (%s); again in %.0f s", self.record.host, self.error or "closed", pause)
            self._wake.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=pause)

    async def _serve(self, remote: SSHRemote) -> None:
        try:
            # The same local port as before when it is free: a page still open keeps working.
            self.local_port = await remote.forward_local(self.record.port_remote, self.local_port)
        except OSError:
            self.local_port = await remote.forward_local(self.record.port_remote, 0)
        self.state, self.error, self.attempts, self.online_since = "online", "", 0, time.time()
        self._notify()
        logger.info("tunnel to %s up on 127.0.0.1:%d", self.record.host, self.local_port)
        beat = asyncio.create_task(self._heartbeat(), name=f"tunnel-heartbeat-{self.record.id}")
        try:
            await remote.wait_closed()
        finally:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat

    async def _heartbeat(self) -> None:
        while True:
            try:
                self.status = await self.fetch(self.local_port)
                self.agent, self.last_seen = "ok", time.time()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the agent is down; the tunnel is not
                logger.debug("heartbeat %s: %s", self.record.host, exc)
                self.agent = "down"
            self._notify()
            await asyncio.sleep(self.heartbeat_s)


class TunnelManager:
    """One tunnel per server added; follows the server list (added → up, removed → down)."""

    def __init__(self, store: ServerStore, remote_factory: Callable[[Login], SSHRemote] | None = None,
                 fetch: FetchStatus | None = None, **tunnel_options: Any) -> None:
        self.store = store
        self.remote_factory = remote_factory
        self.fetch = fetch
        self.options = tunnel_options
        self._tunnels: dict[str, tuple[Tunnel, asyncio.Task]] = {}
        self.listeners: list[Callable[[Tunnel], None]] = []

    def get(self, server_id: str) -> Tunnel | None:
        entry = self._tunnels.get(server_id)
        return entry[0] if entry else None

    def all(self) -> list[Tunnel]:
        return [t for t, _ in self._tunnels.values()]

    async def sync(self) -> None:
        records = {r.id: r for r in await asyncio.to_thread(self.store.all)}
        for sid in [s for s in self._tunnels if s not in records]:
            await self._stop(sid)
        for sid, record in records.items():
            entry = self._tunnels.get(sid)
            if entry is not None and (entry[0].record.host, entry[0].record.port, entry[0].record.host_key) == (
                    record.host, record.port, record.host_key):
                entry[0].record = record      # name, mode, version: no reconnect needed
                continue
            if entry is not None:
                await self._stop(sid)
            tunnel = Tunnel(self.store, record, self.remote_factory, self.fetch, **self.options)
            for listener in self.listeners:
                tunnel.on_change(listener)
            self._tunnels[sid] = (tunnel, asyncio.create_task(tunnel.run(), name=f"tunnel-{sid}"))

    async def _stop(self, sid: str) -> None:
        _tunnel, task = self._tunnels.pop(sid)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def stop(self) -> None:
        for sid in list(self._tunnels):
            await self._stop(sid)
