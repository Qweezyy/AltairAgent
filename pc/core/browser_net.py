"""Which network the built-in browser uses: straight to the internet, or through the VPN.

A VPN that is always on breaks two kinds of sites at once: local ones that refuse foreign IPs,
and foreign ones that are blocked without it. The browser therefore goes through a small local
proxy (this module) that decides per site:

* ``direct`` — bypass the VPN: resolve the name with the physical network's DNS and connect from
  the physical adapter's address. On Windows a socket bound to that address leaves through that
  adapter (strong host model), so this works even for TUN VPNs (sing-box, Hiddify, Clash TUN,
  WireGuard) that capture all traffic, and for proxy-mode VPNs (the system proxy is not used).
* ``vpn`` — an ordinary connection, i.e. whatever the system does (through the tunnel), or the
  system proxy when a proxy-mode VPN set one.
* ``auto`` (default) — try direct; if the site does not answer or drops the TLS handshake (what
  blocked sites do), replay the same first bytes through the VPN, invisibly to the browser, and
  remember the site for the session.

Per-site choices come from the user (browser panel) or the agent (``browser_network`` tool)
and are kept in ``<browser dir>/network.json``. Without a detected VPN everything is simply
direct. Remote requests never reach this proxy: it listens on 127.0.0.1 only.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import re
import socket
import struct
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.fs_atomic import safe_replace
from core.logging_setup import get_logger
from core.utils.proc import powershell_argv, run_process

logger = get_logger("browser_net")

MODES = ("auto", "direct", "vpn")
#: How long a direct attempt may take before "auto" falls back to the VPN.
DIRECT_CONNECT_TIMEOUT = 5.0
DIRECT_FIRST_REPLY_TIMEOUT = 7.0
#: Adapters that are tunnels or virtual, never "the physical network".
_VIRTUAL = re.compile(
    r"wintun|wireguard|tap-|tap |\btun\b|openvpn|tailscale|zerotier|hyper-v|vethernet|virtual|"
    r"loopback|vpn|outline|sing-box|hiddify|clash|v2ray|xray|nekoray|amnezia|radmin|hamachi",
    re.IGNORECASE,
)
PUBLIC_DNS = ("77.88.8.8", "1.1.1.1", "8.8.8.8")

_STATE: dict[str, Any] = {"port": 0}


def configure(port: int) -> None:
    """The port the desktop shell reserved for this proxy (it bakes it into the browser)."""
    _STATE["port"] = int(port)


def proxy_port() -> int:
    return int(_STATE.get("port") or 0)


def pac_url(port: int) -> str:
    """A PAC script for the browser: our proxy for web sites, DIRECT as a fallback (the page
    still loads if the proxy is down) and always DIRECT for local and LAN addresses."""
    script = (
        "function FindProxyForURL(url, host) {"
        " if (isPlainHostName(host) || host == 'localhost' || shExpMatch(host, '127.*') ||"
        " shExpMatch(host, '10.*') || shExpMatch(host, '192.168.*') || shExpMatch(host, '169.254.*') ||"
        " shExpMatch(host, '172.1[6-9].*') || shExpMatch(host, '172.2[0-9].*') || shExpMatch(host, '172.3[01].*') ||"
        " host == '[::1]') return 'DIRECT';"
        f" return 'PROXY 127.0.0.1:{port}; DIRECT'; }}"
    )
    return "data:application/x-ns-proxy-autoconfig," + _pct(script)


def _pct(text: str) -> str:
    safe = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.~")
    return "".join(ch if ch in safe else "".join(f"%{b:02X}" for b in ch.encode("utf-8")) for ch in text)


# ------------------------------------------------------------------ network topology


@dataclass
class Topology:
    vpn: bool = False
    vpn_kind: str = "none"          # "tun" | "proxy" | "none"
    vpn_adapter: str = ""
    physical_ip: str = ""
    physical_adapter: str = ""
    gateway: str = ""
    dns: list[str] = field(default_factory=list)
    system_proxy: str = ""          # "host:port" or "socks=host:port" (proxy-mode VPNs)
    checked_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k != "checked_at"}


def read_system_proxy() -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
            server = winreg.QueryValueEx(key, "ProxyServer")[0] if enabled else ""
    except OSError:
        return ""
    return str(server or "").strip()


async def detect_topology() -> Topology:
    """Find the physical adapter (address, gateway, DNS) and whether a VPN is in the way."""
    topo = Topology(system_proxy=read_system_proxy(), checked_at=time.monotonic())
    if os.name != "nt":
        return topo  # bypassing a VPN is implemented for Windows; elsewhere: plain connections
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$cfg = Get-NetIPConfiguration | ForEach-Object { @{alias=$_.InterfaceAlias;"
        " desc=$_.InterfaceDescription; index=$_.InterfaceIndex;"
        " ip=@($_.IPv4Address | ForEach-Object IPAddress);"
        " gw=@($_.IPv4DefaultGateway | ForEach-Object NextHop);"
        " dns=@($_.DNSServer | Where-Object AddressFamily -eq 2 | ForEach-Object ServerAddresses) } };"
        "$routes = Get-NetRoute -AddressFamily IPv4 | Where-Object { $_.DestinationPrefix -in"
        " @('0.0.0.0/0','0.0.0.0/1','128.0.0.0/1') } | ForEach-Object { @{alias=$_.InterfaceAlias;"
        " prefix=$_.DestinationPrefix} };"
        "@{adapters=@($cfg); routes=@($routes)} | ConvertTo-Json -Depth 4 -Compress"
    )
    result = await run_process(powershell_argv(script), timeout=30.0)
    try:
        data = json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError:
        logger.warning("network topology unreadable: %s", (result.stderr or "")[:200])
        return topo
    adapters = data.get("adapters") or []
    if isinstance(adapters, dict):
        adapters = [adapters]
    routes = data.get("routes") or []
    if isinstance(routes, dict):
        routes = [routes]

    def virtual(a: dict) -> bool:
        return bool(_VIRTUAL.search(f"{a.get('alias', '')} {a.get('desc', '')}"))

    for adapter in adapters:
        gateways = [g for g in (adapter.get("gw") or []) if g and g != "0.0.0.0"]
        ips = [ip for ip in (adapter.get("ip") or []) if ip and not ip.startswith("169.254.")]
        if gateways and ips and not virtual(adapter):
            topo.physical_ip, topo.gateway = ips[0], gateways[0]
            topo.physical_adapter = str(adapter.get("alias") or "")
            topo.dns = [d for d in (adapter.get("dns") or []) if d]
            break
    tunnel_aliases = {str(a.get("alias")) for a in adapters if virtual(a)}
    for route in routes:
        if route.get("alias") in tunnel_aliases and route.get("alias") != "Tailscale":
            topo.vpn, topo.vpn_kind, topo.vpn_adapter = True, "tun", str(route.get("alias"))
            break
    if not topo.vpn and topo.system_proxy:
        topo.vpn, topo.vpn_kind = True, "proxy"
    return topo


# ------------------------------------------------------------------ DNS over the physical network


def _dns_query(name: str) -> tuple[int, bytes]:
    tid = random.randint(0, 0xFFFF)
    labels = b"".join(bytes([len(p)]) + p.encode("idna") for p in name.rstrip(".").split("."))
    return tid, struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0) + labels + b"\0" + struct.pack(">HH", 1, 1)


def _skip_name(data: bytes, i: int) -> int:
    while data[i]:
        if data[i] & 0xC0 == 0xC0:
            return i + 2
        i += data[i] + 1
    return i + 1


def parse_dns_answer(data: bytes, tid: int) -> tuple[list[str], int]:
    """A records and the smallest TTL from a DNS reply (CNAME chains are followed by the server)."""
    if len(data) < 12 or struct.unpack(">H", data[:2])[0] != tid:
        raise ValueError("not our DNS reply")
    qd, an = struct.unpack(">HH", data[4:8])
    i = 12
    for _ in range(qd):
        i = _skip_name(data, i) + 4
    ips: list[str] = []
    ttl = 300
    for _ in range(an):
        i = _skip_name(data, i)
        rtype, _cls, rttl, length = struct.unpack(">HHIH", data[i : i + 10])
        i += 10
        if rtype == 1 and length == 4:
            ips.append(socket.inet_ntoa(data[i : i + 4]))
            ttl = min(ttl, max(30, rttl))
        i += length
    return ips, ttl


def resolve_direct(name: str, bind_ip: str, servers: list[str], timeout: float = 2.5) -> tuple[list[str], int]:
    """Resolve `name` with DNS servers reached from the physical adapter, not the VPN's resolver
    (TUN VPNs often answer with fake 198.18.x.x addresses that only work inside the tunnel)."""
    last: Exception | None = None
    for server in servers:
        tid, query = _dns_query(name)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            if bind_ip:
                sock.bind((bind_ip, 0))
            sock.sendto(query, (server, 53))
            data, _ = sock.recvfrom(4096)
            ips, ttl = parse_dns_answer(data, tid)
            if ips:
                return ips, ttl
        except (OSError, ValueError, struct.error, IndexError) as exc:
            last = exc
        finally:
            sock.close()
    raise OSError(f"cannot resolve {name} outside the VPN: {last}")


# ------------------------------------------------------------------ per-site rules


class NetRules:
    """Per-site choices ("example.com" covers its subdomains), stored next to the browser."""

    #: How long a site "auto" found blocked keeps going through the VPN without a new check.
    LEARNED_DAYS = 7

    def __init__(self, path: Path) -> None:
        self.path = path
        self.sites: dict[str, str] = {}
        #: Sites "auto" found unreachable directly, with when that was seen — kept on disk so
        #: a blocked site does not cost the direct attempt again on every launch.
        self.learned: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.sites = {str(k).lower(): v for k, v in dict(data.get("sites") or {}).items() if v in MODES}
        horizon = time.time() - self.LEARNED_DAYS * 86400
        self.learned = {str(k): float(v) for k, v in dict(data.get("learned") or {}).items()
                        if isinstance(v, (int, float)) and v > horizon}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        data = {"sites": self.sites, "learned": self.learned}
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        safe_replace(tmp, self.path)

    @staticmethod
    def normalize(host: str) -> str:
        host = host.strip().lower()
        host = re.sub(r"^[a-z]+://", "", host).split("/")[0].split(":")[0]
        return host.removeprefix("www.")

    def rule_for(self, host: str) -> tuple[str, str]:
        """(mode, matched site) — the most specific rule wins; ("", "") if none."""
        host = self.normalize(host)
        parts = host.split(".")
        for i in range(len(parts) - 1):
            site = ".".join(parts[i:])
            if site in self.sites:
                return self.sites[site], site
        return "", ""

    def is_learned(self, host: str) -> bool:
        seen = self.learned.get(self.normalize(host))
        return seen is not None and seen > time.time() - self.LEARNED_DAYS * 86400

    def learn(self, host: str) -> None:
        self.learned[self.normalize(host)] = time.time()
        try:
            self._save()
        except OSError:
            logger.debug("could not save learned sites", exc_info=True)

    def unlearn(self, site: str) -> None:
        site = self.normalize(site)
        for host in [h for h in self.learned if h == site or h.endswith("." + site)]:
            del self.learned[host]

    def set(self, host: str, mode: str) -> str:
        site = self.normalize(host)
        if not site:
            raise ValueError("empty site")
        self.unlearn(site)  # an explicit choice restarts the automatic check
        if mode == "auto":
            self.sites.pop(site, None)
        elif mode in ("direct", "vpn"):
            self.sites[site] = mode
        else:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        self._save()
        return site


# ------------------------------------------------------------------ the proxy


class BrowserNetProxy:
    """HTTP proxy on 127.0.0.1 that sends each browser connection direct or through the VPN."""

    def __init__(self, rules: NetRules, default_mode: str = "auto") -> None:
        self.rules = rules
        self.default_mode = default_mode if default_mode in MODES else "auto"
        self.topology = Topology()
        self._server: asyncio.base_events.Server | None = None
        self._dns_cache: dict[str, tuple[list[str], float]] = {}
        #: Last route per host, newest last — what the UI and the agent show.
        self.recent: OrderedDict[str, dict[str, str]] = OrderedDict()
        self._watch: asyncio.Task[None] | None = None
        #: Open browser connections — cut on close(), or the server would wait for all of them.
        self._conns: set[asyncio.Task[Any]] = set()
        self.port = 0

    # ---- lifecycle
    async def start(self, port: int = 0) -> int:
        # The network is probed in the background (a PowerShell call takes seconds): until the
        # first answer every site simply goes the ordinary way, and the app starts at once.
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", port)
        self.port = self._server.sockets[0].getsockname()[1]
        self._watch = asyncio.create_task(self._rescan(), name="browser-net-rescan")
        logger.info("browser network proxy on 127.0.0.1:%s", self.port)
        return self.port

    async def _rescan(self) -> None:
        first = True
        while True:
            if not first:
                await asyncio.sleep(30)
            first = False
            try:
                topo = await detect_topology()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - keep the last known picture
                logger.debug("topology rescan failed", exc_info=True)
                continue
            if (topo.vpn, topo.physical_ip) != (self.topology.vpn, self.topology.physical_ip):
                self._dns_cache.clear()
                logger.info("browser network: vpn=%s %s, physical=%s", topo.vpn, topo.vpn_kind,
                            topo.physical_ip or "-")
            self.topology = topo

    async def close(self) -> None:
        if self._watch is not None:
            self._watch.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch
        if self._server is not None:
            self._server.close()
            for task in list(self._conns):
                task.cancel()
            await asyncio.gather(*self._conns, return_exceptions=True)
            with contextlib.suppress(Exception):
                await self._server.wait_closed()

    # ---- decisions
    def mode_for(self, host: str) -> tuple[str, str]:
        """(mode, source): source is "rule", "learned" or "default"."""
        mode, _site = self.rules.rule_for(host)
        if mode:
            return mode, "rule"
        if self.default_mode == "auto" and self.rules.is_learned(host):
            return "vpn", "learned"
        return self.default_mode, "default"

    def _note(self, host: str, route: str, why: str) -> None:
        host = NetRules.normalize(host)
        self.recent.pop(host, None)
        self.recent[host] = {"route": route, "why": why}
        while len(self.recent) > 60:
            self.recent.popitem(last=False)

    def status(self) -> dict[str, Any]:
        return {
            "port": self.port,
            "default": self.default_mode,
            "network": self.topology.as_dict(),
            "sites": dict(self.rules.sites),
            "learned_vpn": sorted(self.rules.learned),
            "recent": [{"host": h, **r} for h, r in reversed(self.recent.items())],
        }

    def forget(self, site: str) -> None:
        """After the user or the agent changed a site's route: drop what was cached for it."""
        site = NetRules.normalize(site)
        self.rules.unlearn(site)
        for host in [h for h in self._dns_cache if h == site or h.endswith("." + site)]:
            del self._dns_cache[host]

    # ---- connections
    async def _open_direct(self, host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        topo = self.topology
        if not topo.vpn or not topo.physical_ip:
            return await asyncio.wait_for(asyncio.open_connection(host, port), DIRECT_CONNECT_TIMEOUT)
        ips = await self._resolve_direct(host)
        last: Exception | None = None
        for ip in ips[:3]:
            try:
                return await asyncio.wait_for(
                    asyncio.open_connection(ip, port, local_addr=(topo.physical_ip, 0)), DIRECT_CONNECT_TIMEOUT
                )
            except (OSError, asyncio.TimeoutError) as exc:
                last = exc
        raise OSError(f"direct connection to {host} failed: {last}")

    async def _resolve_direct(self, host: str) -> list[str]:
        try:
            socket.inet_aton(host)
            return [host]
        except OSError:
            pass
        cached = self._dns_cache.get(host)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        topo = self.topology
        servers = [s for s in [topo.gateway, *topo.dns, *PUBLIC_DNS] if s and not s.startswith("198.18.")]
        ips, ttl = await asyncio.to_thread(resolve_direct, host, topo.physical_ip, list(dict.fromkeys(servers)))
        self._dns_cache[host] = (ips, time.monotonic() + ttl)
        return ips

    async def _open_vpn(self, host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        proxy = self.topology.system_proxy if self.topology.vpn_kind == "proxy" else ""
        if not proxy:
            return await asyncio.wait_for(asyncio.open_connection(host, port), 20.0)
        return await _via_upstream_proxy(proxy, host, port)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._conns.add(task)
            task.add_done_callback(self._conns.discard)
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30.0)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError):
            writer.close()
            return
        try:
            line = head.split(b"\r\n", 1)[0].decode("latin-1")
            method, target, _version = line.split(" ", 2)
            if method.upper() == "CONNECT":
                await self._tunnel(target, reader, writer)
            else:
                await self._plain(method, target, head, reader, writer)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one broken connection must not stop the proxy
            logger.debug("proxy connection failed: %s", exc)
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    async def _tunnel(self, target: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        host, _, port_text = target.rpartition(":")
        host, port = host.strip("[]"), int(port_text or 443)
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        try:
            first = await asyncio.wait_for(reader.read(65536), 30.0)
        except asyncio.TimeoutError:
            return
        if not first:
            return
        upstream = await self._connect_with_first_flight(host, port, first)
        if upstream is None:
            return
        up_reader, up_writer, reply = upstream
        writer.write(reply)
        await writer.drain()
        await _pipe(reader, writer, up_reader, up_writer)

    async def _connect_with_first_flight(self, host: str, port: int, first: bytes):
        """Send the client's first bytes (the TLS ClientHello) and wait for the first reply.
        Blocked sites reset or stall right here — then "auto" replays through the VPN."""
        mode, source = self.mode_for(host)
        if not self.topology.vpn:
            mode = "direct"  # nothing to bypass
        attempts = ["direct", "vpn"] if mode == "auto" else [mode]
        for route in attempts:
            try:
                up_reader, up_writer = await (self._open_direct(host, port) if route == "direct"
                                              else self._open_vpn(host, port))
                up_writer.write(first)
                await up_writer.drain()
                wait = DIRECT_FIRST_REPLY_TIMEOUT if (mode == "auto" and route == "direct") else 30.0
                reply = await asyncio.wait_for(up_reader.read(65536), wait)
                if not reply:
                    raise ConnectionError("closed by the server")
            except (OSError, asyncio.TimeoutError, ConnectionError) as exc:
                with contextlib.suppress(Exception):
                    up_writer.close()  # type: ignore[possibly-undefined]
                logger.debug("%s via %s failed: %s", host, route, exc)
                continue
            if mode == "auto" and route == "vpn":
                self.rules.learn(host)
                self._note(host, "vpn", "auto: not reachable directly")
            else:
                self._note(host, route, source if mode != "auto" else "auto")
            return up_reader, up_writer, reply
        self._note(host, "failed", f"{mode}: no route answered")
        return None

    async def _plain(self, method: str, target: str, head: bytes, reader, writer) -> None:
        """Plain http:// through the proxy — rare today; one request per connection."""
        from urllib.parse import urlsplit

        url = urlsplit(target)
        host, port = url.hostname or "", url.port or 80
        path = (url.path or "/") + (f"?{url.query}" if url.query else "")
        lines = head.decode("latin-1").split("\r\n")
        rest = [ln for ln in lines[1:] if ln and not ln.lower().startswith(("proxy-connection:", "connection:"))]
        request = f"{method} {path} HTTP/1.1\r\n" + "\r\n".join(rest) + "\r\nConnection: close\r\n\r\n"
        upstream = await self._connect_with_first_flight(host, port, request.encode("latin-1"))
        if upstream is None:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return
        up_reader, up_writer, reply = upstream
        writer.write(reply)
        await writer.drain()
        await _pipe(reader, writer, up_reader, up_writer)


async def _pipe(a_reader, a_writer, b_reader, b_writer) -> None:
    async def copy(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
        try:
            while chunk := await src.read(65536):
                dst.write(chunk)
                await dst.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            with contextlib.suppress(Exception):
                dst.close()

    await asyncio.gather(copy(a_reader, b_writer), copy(b_reader, a_writer))


async def _via_upstream_proxy(proxy: str, host: str, port: int):
    """Through a proxy-mode VPN's system proxy: "host:port", "http=…;https=…" or "socks=…"."""
    entries = dict(part.split("=", 1) for part in proxy.split(";") if "=" in part)
    target = entries.get("https") or entries.get("http") or (proxy if "=" not in proxy else "")
    socks = entries.get("socks", "")
    if target:
        p_host, _, p_port = target.rpartition(":")
        r, w = await asyncio.wait_for(asyncio.open_connection(p_host, int(p_port)), 15.0)
        w.write(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
        await w.drain()
        reply = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 15.0)
        if b" 200" not in reply.split(b"\r\n", 1)[0]:
            w.close()
            raise ConnectionError("the system proxy refused the tunnel")
        return r, w
    if socks:
        s_host, _, s_port = socks.rpartition(":")
        r, w = await asyncio.wait_for(asyncio.open_connection(s_host, int(s_port)), 15.0)
        w.write(b"\x05\x01\x00")
        await w.drain()
        if (await r.readexactly(2))[1] != 0:
            raise ConnectionError("SOCKS proxy wants authentication")
        name = host.encode("idna")
        w.write(b"\x05\x01\x00\x03" + bytes([len(name)]) + name + struct.pack(">H", port))
        await w.drain()
        head = await r.readexactly(4)
        if head[1] != 0:
            raise ConnectionError(f"SOCKS connect failed ({head[1]})")
        skip = {1: 4, 4: 16}.get(head[3])
        await r.readexactly((skip if skip else (await r.readexactly(1))[0]) + 2)
        return r, w
    raise ConnectionError(f"unsupported system proxy: {proxy}")


_PROXY: dict[str, BrowserNetProxy | None] = {"instance": None}


def get_proxy() -> BrowserNetProxy | None:
    return _PROXY["instance"]


async def start_proxy(rules_path: Path, default_mode: str) -> BrowserNetProxy:
    proxy = BrowserNetProxy(NetRules(rules_path), default_mode)
    await proxy.start(proxy_port())
    _PROXY["instance"] = proxy
    return proxy


async def stop_proxy() -> None:
    proxy, _PROXY["instance"] = _PROXY["instance"], None
    if proxy is not None:
        await proxy.close()
