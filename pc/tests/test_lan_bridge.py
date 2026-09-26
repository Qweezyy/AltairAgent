"""The phone bridge's network entrance: real listeners, switched live, guarded by the token.

127.0.0.2 stands in for the PC's Wi-Fi address: Windows and Linux route the whole 127/8 block to
the loopback, and it is *not* one of the trusted loopback hosts, so requests from it are treated
exactly like requests from a phone.
"""

from __future__ import annotations

import socket

import httpx
import pytest

from core.pairing import bridge_base_url, is_loopback_only, pair_info
from server.lan_bridge import LanBridge

PHONE_IP = "127.0.0.2"


def _phone(timeout: float = 10) -> httpx.AsyncClient:
    """A client whose requests come *from* 127.0.0.2 — like a phone, not the app window."""
    return httpx.AsyncClient(timeout=timeout, transport=httpx.AsyncHTTPTransport(local_address=PHONE_IP))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _can_bind(ip: str) -> bool:
    with socket.socket() as s:
        try:
            s.bind((ip, 0))
        except OSError:
            return False
    return True


@pytest.fixture()
def app(settings, monkeypatch):
    import server.app as app_module
    import server.remote_auth as remote_auth

    with_token = settings.model_copy(update={"bridge_token": "phone-secret"})
    monkeypatch.setattr(app_module, "get_settings", lambda: with_token)
    monkeypatch.setattr(remote_auth, "get_settings", lambda: with_token)
    return app_module.create_app()


async def test_entrance_opens_and_closes_live_and_needs_the_token(app):
    if not _can_bind(PHONE_IP):
        pytest.skip("127.0.0.2 is not routable here")
    port = _free_port()
    bridge = LanBridge(app, port=port, addresses=lambda: [PHONE_IP])
    url = f"http://{PHONE_IP}:{port}/"
    try:
        assert await bridge.apply(True) == [PHONE_IP]
        async with _phone() as phone:
            assert (await phone.get(url)).status_code == 401  # no token: refused
            ok = await phone.get(url, headers={"Authorization": "Bearer phone-secret"})
            assert ok.status_code == 200 and "<html" in ok.text.lower()

        assert await bridge.apply(False) == []
        async with _phone(5) as phone:
            with pytest.raises(httpx.ConnectError):
                await phone.get(url)

        assert await bridge.apply(True) == [PHONE_IP]  # and on again, still no restart
        async with _phone() as phone:
            assert (await phone.get(url, headers={"Authorization": "Bearer phone-secret"})).status_code == 200
    finally:
        await bridge.close()


async def test_a_busy_address_is_reported_not_fatal(app):
    if not _can_bind(PHONE_IP):
        pytest.skip("127.0.0.2 is not routable here")
    port = _free_port()
    blocker = socket.socket()
    blocker.bind((PHONE_IP, port))
    blocker.listen()
    bridge = LanBridge(app, port=port, addresses=lambda: [PHONE_IP])
    try:
        assert await bridge.apply(True) == []
        assert PHONE_IP in bridge.errors
    finally:
        blocker.close()
        await bridge.close()


async def test_without_a_known_port_nothing_listens(app):
    bridge = LanBridge(app, port=0, addresses=lambda: [PHONE_IP])
    assert await bridge.apply(True) == []


def test_qr_address_prefers_the_home_network(settings):
    listening = ["100.101.5.7", "192.168.1.23"]  # Tailscale + Wi-Fi
    assert bridge_base_url(settings, 8137, listening) == "http://192.168.1.23:8137"
    assert bridge_base_url(settings, 8137, ["100.101.5.7"]) == "http://100.101.5.7:8137"
    assert is_loopback_only(settings, []) is True
    assert is_loopback_only(settings, listening) is False


def test_pair_info_reports_what_really_listens(settings, monkeypatch):
    import core.pairing as pairing

    monkeypatch.setattr(pairing, "ensure_bridge_token", lambda s=None: "tok")
    info = pair_info("", settings, 8137, ["10.0.0.5"])
    assert info["url"] == "http://10.0.0.5:8137" and info["listening"] == ["10.0.0.5"]
    assert info["loopback_only"] is False
    assert "10.0.0.5" in info["link"] or "10.0.0.5" in pairing.unquote(info["link"])
