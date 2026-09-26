"""The built-in browser's network: direct (bypassing the VPN) or through it, per site."""

from __future__ import annotations

import asyncio
import socket
import struct
import time
from urllib.parse import unquote

import pytest

import core.browser_net as net
from core.browser_net import BrowserNetProxy, NetRules, Topology, pac_url, parse_dns_answer

# ------------------------------------------------------------------ pure pieces


def test_pac_sends_sites_to_the_proxy_with_a_direct_fallback():
    script = unquote(pac_url(51234).split(",", 1)[1])
    assert "PROXY 127.0.0.1:51234; DIRECT" in script
    assert "'localhost'" in script and "192.168.*" in script  # local pages never go through it


def test_dns_answer_parsing():
    tid = 0x1234
    question = b"\x07example\x03com\x00" + struct.pack(">HH", 1, 1)
    answer = (b"\xc0\x0c" + struct.pack(">HHIH", 5, 1, 60, 2) + b"\xc0\x0c"          # CNAME
              + b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 120, 4) + socket.inet_aton("93.184.216.34"))
    reply = struct.pack(">HHHHHH", tid, 0x8180, 1, 2, 0, 0) + question + answer
    ips, ttl = parse_dns_answer(reply, tid)
    assert ips == ["93.184.216.34"] and ttl == 120
    with pytest.raises(ValueError):
        parse_dns_answer(reply, 0x9999)


def test_rules_match_subdomains_and_persist(tmp_path):
    rules = NetRules(tmp_path / "network.json")
    assert rules.set("https://www.YouTube.com/watch?v=1", "vpn") == "youtube.com"
    assert rules.rule_for("m.youtube.com") == ("vpn", "youtube.com")
    assert rules.rule_for("notyoutube.com") == ("", "")
    rules.set("gosuslugi.ru", "direct")
    rules.learn("instagram.com")
    again = NetRules(tmp_path / "network.json")
    assert again.sites == {"youtube.com": "vpn", "gosuslugi.ru": "direct"}
    assert again.is_learned("instagram.com")
    again.set("youtube.com", "auto")
    assert again.rule_for("youtube.com") == ("", "")
    with pytest.raises(ValueError):
        again.set("x.com", "tor")


def test_learned_sites_expire(tmp_path):
    rules = NetRules(tmp_path / "network.json")
    rules.learned["old.example"] = time.time() - (NetRules.LEARNED_DAYS + 1) * 86400
    rules._save()
    assert not NetRules(tmp_path / "network.json").is_learned("old.example")


# ------------------------------------------------------------------ the proxy with real sockets


async def _server(handler) -> tuple[asyncio.base_events.Server, int]:
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _stall(reader, writer):  # accepts and never answers: what a blocked site looks like
    while await reader.read(1024):  # swallow the ClientHello; end when the caller hangs up
        pass
    writer.close()


async def _answer(tag: bytes):
    async def handler(reader, writer):
        data = await reader.read(1024)
        writer.write(tag + b":" + data)
        await writer.drain()
        writer.close()
    return handler


async def _through_proxy(port: int, host: str, timeout: float = 15) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode())
    await writer.drain()
    assert b"200" in await reader.readuntil(b"\r\n\r\n")
    writer.write(b"client-hello")
    await writer.drain()
    reply = await asyncio.wait_for(reader.read(1024), timeout)
    writer.close()
    return reply


@pytest.fixture()
async def proxy(tmp_path, monkeypatch):
    monkeypatch.setattr(net, "DIRECT_FIRST_REPLY_TIMEOUT", 0.5)

    probed = {"topology": Topology(vpn=True, vpn_kind="tun", physical_ip="127.0.0.1")}

    async def fake_probe():  # the background network probe reports what the test says
        return probed["topology"]

    monkeypatch.setattr(net, "detect_topology", fake_probe)
    stall, stall_port = await _server(_stall)
    direct, direct_port = await _server(await _answer(b"direct"))
    vpn, vpn_port = await _server(await _answer(b"vpn"))
    p = BrowserNetProxy(NetRules(tmp_path / "network.json"), "auto")
    port = await p.start(0)
    await asyncio.sleep(0.05)  # let the first background probe land
    p.probed = probed
    blocked = {"blocked.example"}

    async def open_direct(host, _port):
        return await asyncio.open_connection("127.0.0.1", stall_port if host in blocked else direct_port)

    async def open_vpn(host, _port):
        return await asyncio.open_connection("127.0.0.1", vpn_port)

    p._open_direct, p._open_vpn = open_direct, open_vpn
    yield p, port
    await p.close()
    for s in (stall, direct, vpn):
        s.close()


async def test_auto_goes_direct_when_the_site_answers(proxy):
    p, port = proxy
    assert await _through_proxy(port, "ok.example") == b"direct:client-hello"
    assert p.recent["ok.example"]["route"] == "direct"


async def test_auto_replays_through_the_vpn_when_direct_stalls_and_remembers(proxy):
    p, port = proxy
    assert await _through_proxy(port, "blocked.example") == b"vpn:client-hello"
    assert p.recent["blocked.example"] == {"route": "vpn", "why": "auto: not reachable directly"}
    assert p.rules.is_learned("blocked.example")
    assert p.mode_for("blocked.example") == ("vpn", "learned")  # next time: no direct attempt


async def test_rules_force_a_route(proxy):
    p, port = proxy
    p.rules.set("ok.example", "vpn")
    assert await _through_proxy(port, "ok.example") == b"vpn:client-hello"
    p.rules.set("blocked.example", "direct")  # forced direct: it keeps waiting, never the VPN
    with pytest.raises(asyncio.TimeoutError):
        await _through_proxy(port, "blocked.example", timeout=2)
    assert "blocked.example" not in p.rules.learned


async def test_without_a_vpn_everything_is_direct(proxy):
    p, port = proxy
    p.probed["topology"] = p.topology = Topology(vpn=False)
    p.rules.set("ok.example", "vpn")
    assert await _through_proxy(port, "ok.example") == b"direct:client-hello"


# ------------------------------------------------------------------ the agent's tool


async def test_tool_status_and_set(tmp_path, monkeypatch):
    from core.tools.base import ToolContext
    from core.tools.builtin.browser_tools import BrowserNetworkTool

    p = BrowserNetProxy(NetRules(tmp_path / "network.json"), "auto")
    p.topology = Topology(vpn=True, vpn_kind="tun", vpn_adapter="wwan99", physical_ip="192.168.8.40",
                          physical_adapter="Ethernet")
    monkeypatch.setitem(net._PROXY, "instance", p)
    tool = BrowserNetworkTool()
    status = await tool.invoke({"action": "status"}, ToolContext())
    assert status.ok and "VPN on (tun, adapter wwan99)" in status.content and "192.168.8.40" in status.content
    done = await tool.invoke({"action": "set", "site": "https://kinopoisk.ru/film/1", "mode": "direct"}, ToolContext())
    assert done.ok and "kinopoisk.ru: direct" in done.content
    assert p.rules.rule_for("hd.kinopoisk.ru") == ("direct", "kinopoisk.ru")
    assert tool.auto_verdict(tool.parse_args({"action": "set", "site": "x.com", "mode": "vpn"}), ToolContext()) == "allow"
