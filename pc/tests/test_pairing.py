"""Связывание телефона: адрес в LAN, ссылка, токен и QR."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from core.pairing import (
    _ip_score,
    bridge_base_url,
    build_pair_link,
    ensure_bridge_token,
    is_loopback_only,
    local_ip,
    pair_info,
    qr_svg,
)

# ------------------------------------------------------------------ выбор адреса


def test_private_lan_beats_vpn_and_virtual():
    # RFC1918 предпочтительнее бенчмарк-диапазона VPN и loopback.
    assert _ip_score("192.168.1.5") > _ip_score("198.18.0.1")
    assert _ip_score("10.0.0.7") > _ip_score("198.18.0.1")
    assert _ip_score("172.20.0.1") > _ip_score("100.102.19.29")
    assert _ip_score("127.0.0.1") < 0
    assert _ip_score("169.254.1.1") < 0
    # 172.x вне 16–31 — не приватный класс B, обычный маршрутизируемый ранг.
    assert _ip_score("172.99.0.1") == 40


def test_local_ip_is_nonempty_string():
    ip = local_ip()
    assert isinstance(ip, str) and ip
    assert not ip.startswith("169.254.")  # link-local не отдаём


# ------------------------------------------------------------------ ссылка


def test_pair_link_is_url_encoded_and_parseable():
    link = build_pair_link("http://192.168.1.5:8000", "a b/c=d", "D:/My Проект")
    assert link.startswith("altair://pair?")
    parsed = urlparse(link)
    q = parse_qs(parsed.query)
    assert q["u"][0] == "http://192.168.1.5:8000"
    assert q["t"][0] == "a b/c=d"  # спецсимволы пережили round-trip
    assert q["w"][0] == "D:/My Проект"


def test_pair_link_omits_empty_workspace():
    link = build_pair_link("http://x", "tok", "")
    assert "w=" not in link


def test_bridge_base_url_uses_given_port():
    assert bridge_base_url(port=8792).endswith(":8792")


# ------------------------------------------------------------------ статус сети


def test_loopback_only_detection(settings):
    settings.host = "127.0.0.1"
    assert is_loopback_only(settings) is True
    settings.host = "0.0.0.0"
    assert is_loopback_only(settings) is False


# ------------------------------------------------------------------ токен


def test_ensure_token_generates_and_persists(settings):
    assert settings.bridge_token == ""
    token = ensure_bridge_token(settings)
    assert token
    from core.config_file import config_path

    saved = config_path(settings).read_text(encoding="utf-8")
    assert f"BRIDGE_TOKEN={token}" in saved


def test_ensure_token_keeps_existing(settings):
    settings.bridge_token = "уже-есть"
    assert ensure_bridge_token(settings) == "уже-есть"


# ------------------------------------------------------------------ сводка


def test_pair_info_shape(settings):
    settings.bridge_token = "tok123"  # чтобы не писать в .env во время теста
    info = pair_info("D:/proj", settings, port=8792)
    assert info["url"].endswith(":8792")
    assert info["token"] == "tok123"
    assert info["workspace"] == "D:/proj"
    assert info["link"].startswith("altair://pair?")
    assert "tok123" in info["link"]
    assert info["loopback_only"] is True


# ------------------------------------------------------------------ QR


def test_qr_svg_renders_when_segno_available():
    pytest.importorskip("segno")
    svg = qr_svg("altair://pair?u=x&t=y")
    assert svg and "<svg" in svg and "</svg>" in svg
