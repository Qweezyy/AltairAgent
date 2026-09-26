"""Network entrance for the phone bridge: extra listeners on the PC's LAN / Tailscale addresses.

The app itself always listens on 127.0.0.1 (the app window). When "access over the network" is
on, this module adds listeners on each of the PC's network addresses, on the same port, serving
the same app. Why not simply 0.0.0.0:
  * switching it on or off works live — binding 0.0.0.0 next to 127.0.0.1 on the same port is
    refused on Windows, so a 0.0.0.0 setup needs a restart;
  * nothing listens on the network while the bridge is off;
  * it no longer matters how the app was launched (the desktop shell passes --host 127.0.0.1,
    which used to keep the server loopback-only even with the bridge on).
Remote requests still need the bridge token (server/remote_auth.py). Addresses are re-read now
and then, so a new Wi-Fi or a DHCP change is picked up without touching anything.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import Callable
from typing import Any

from core.logging_setup import get_logger

logger = get_logger("server.lan")

RESCAN_SECONDS = 20.0

#: The port the main server listens on — set by main.py before uvicorn starts. 0 = unknown
#: (tests, TestClient): no network listeners then.
_PORT = {"value": 0}


def configure(port: int) -> None:
    _PORT["value"] = int(port)


def _usable(ip: str) -> bool:
    return bool(ip) and not ip.startswith(("127.", "169.254.", "0.")) and ":" not in ip


def network_addresses() -> list[str]:
    """IPv4 addresses of this PC other than loopback/link-local (Wi-Fi, Ethernet, Tailscale…)."""
    found: list[str] = []
    probe = None
    try:  # the address of the default route: the likeliest one for the phone
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))
        found.append(probe.getsockname()[0])
    except OSError:
        logger.debug("no default route for the LAN probe")
    finally:
        if probe is not None:
            probe.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(str(info[4][0]))
    except OSError:
        logger.debug("host name lookup failed", exc_info=True)
    unique: list[str] = []
    for ip in found:
        if _usable(ip) and ip not in unique:
            unique.append(ip)
    return unique


class _Listener:
    """One uvicorn server on one address, run as a task in the app's own event loop."""

    def __init__(self, app: Any, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self._app = app
        self._server: Any = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        import uvicorn

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((self.host, self.port))
        except OSError:
            sock.close()
            raise
        sock.setblocking(False)
        # lifespan="off": the main server already ran startup; this is just another door.
        config = uvicorn.Config(self._app, lifespan="off", log_level="warning", ws_ping_interval=30)
        self._server = uvicorn.Server(config)
        # startup/main_loop/shutdown instead of serve(): serve() installs signal handlers,
        # which belong to the main server. These two lines are what serve() does first.
        config.load()
        self._server.lifespan = config.lifespan_class(config)
        await self._server.startup(sockets=[sock])
        self._task = asyncio.create_task(self._run(), name=f"lan-listener-{self.host}")

    async def _run(self) -> None:
        try:
            await self._server.main_loop()
        finally:
            await self._server.shutdown()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._task
        self._task = None
        self._server = None


class LanBridge:
    """Keeps listeners on the PC's network addresses while the phone bridge is enabled."""

    def __init__(self, app: Any, port: int | None = None,
                 addresses: Callable[[], list[str]] = network_addresses) -> None:
        self._app = app
        self._port = port if port is not None else _PORT["value"]
        self._addresses = addresses
        self._listeners: dict[str, _Listener] = {}
        self.errors: dict[str, str] = {}
        self._watch: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def port(self) -> int:
        return self._port

    def listening(self) -> list[str]:
        return sorted(self._listeners)

    async def apply(self, enabled: bool) -> list[str]:
        """Open or close the network entrance; returns the addresses now listening."""
        if not self._port:
            return []
        if enabled:
            await self._sync()
            if self._watch is None or self._watch.done():
                self._watch = asyncio.create_task(self._rescan(), name="lan-rescan")
        else:
            await self.close()
        return self.listening()

    async def _sync(self) -> None:
        async with self._lock:
            wanted = await asyncio.to_thread(self._addresses)
            for host in [h for h in self._listeners if h not in wanted]:
                await self._listeners.pop(host).stop()
            for host in wanted:
                if host in self._listeners:
                    continue
                listener = _Listener(self._app, host, self._port)
                try:
                    await listener.start()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - one busy address must not stop the rest
                    self.errors[host] = str(exc)
                    logger.warning("bridge cannot listen on %s:%s: %s", host, self._port, exc)
                    continue
                self.errors.pop(host, None)
                self._listeners[host] = listener
                logger.info("phone bridge listening on %s:%s", host, self._port)

    async def _rescan(self) -> None:
        while True:
            await asyncio.sleep(RESCAN_SECONDS)
            try:
                await self._sync()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - keep watching; the next pass may succeed
                logger.warning("bridge address rescan failed", exc_info=True)

    async def close(self) -> None:
        if self._watch is not None:
            self._watch.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch
            self._watch = None
        async with self._lock:
            for listener in self._listeners.values():
                await listener.stop()
            self._listeners.clear()
