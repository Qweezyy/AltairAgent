"""Связывание телефона с ПК-мостом: адрес, токен, ссылка и QR-код.

Телефон (Android) сканирует QR любой камерой → открывается deeplink
`altair://pair?u=<url>&t=<token>&w=<workspace>` (значения URL-encoded) и приложение
само настраивает мост. Здесь — ПК-сторона: определить адрес в локальной сети,
выдать/сохранить общий секрет, собрать ссылку и нарисовать QR.

QR рисуется библиотекой segno (чистый Python, без зависимостей). Если её нет —
возвращаем None, интерфейс покажет ссылку текстом (мост всё равно настраивается
вручную/через буфер). См. [[pc-bridge-implementation]].
"""

from __future__ import annotations

import secrets
import socket
from urllib.parse import quote

from core.config_file import write_values
from core.logging_setup import get_logger
from core.settings import Settings, get_settings, reload_settings

logger = get_logger("pairing")

_LOOPBACK = {"", "127.0.0.1", "::1", "localhost"}


def _ip_score(ip: str) -> int:
    """Ранг адреса как «домашнего LAN» — чем больше, тем вероятнее Wi-Fi/Ethernet.

    Телефон должен быть в одной сети с ПК, поэтому предпочитаем частные диапазоны
    RFC1918 (192.168 / 10 / 172.16–31) и отбрасываем loopback, link-local и
    служебные (198.18/19 — бенчмарк, часто у VPN-адаптеров).
    """
    if not ip or ip.startswith("127.") or ip.startswith("169.254."):
        return -1
    if ip.startswith("192.168."):
        return 100
    if ip.startswith("10."):
        return 90
    if ip.startswith("172."):
        try:
            second = int(ip.split(".")[1])
        except (IndexError, ValueError):
            second = 0
        if 16 <= second <= 31:
            return 85
    if ip.startswith(("198.18.", "198.19.")):
        return 5  # бенчмарк-диапазон (обычно VPN/виртуальный адаптер)
    return 40  # прочие маршрутизируемые — лучше, чем ничего


def local_ip() -> str:
    """IP-адрес ПК в локальной сети (для доступа с телефона по Wi-Fi).

    Собираем кандидатов: адрес исходящего интерфейса (UDP-трюк — трафик не идёт)
    и все адреса по имени хоста, затем берём самый «домашний» по _ip_score.
    При неудаче — 127.0.0.1.
    """
    candidates: list[str] = []
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # адрес не важен, соединение не устанавливается
        candidates.append(s.getsockname()[0])
    except OSError:
        pass
    finally:
        if s is not None:
            s.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidates.append(info[4][0])
    except OSError:
        pass

    best, best_score = "127.0.0.1", -1
    for ip in candidates:
        score = _ip_score(ip)
        if score > best_score:
            best, best_score = ip, score
    return best


def ensure_bridge_token(settings: Settings | None = None) -> str:
    """Вернуть общий секрет моста, сгенерировав и сохранив его при отсутствии."""
    settings = settings or get_settings()
    token = (settings.bridge_token or "").strip()
    if token:
        return token
    token = secrets.token_urlsafe(24)
    write_values({"BRIDGE_TOKEN": token}, settings)
    reload_settings()
    logger.info("Сгенерирован bridge_token для связывания телефона.")
    return token


def rotate_bridge_token(settings: Settings | None = None) -> str:
    """Сгенерировать НОВЫЙ секрет (старые связки перестанут подключаться)."""
    settings = settings or get_settings()
    token = secrets.token_urlsafe(24)
    write_values({"BRIDGE_TOKEN": token}, settings)
    reload_settings()
    logger.info("bridge_token перевыпущен.")
    return token


def bridge_base_url(settings: Settings | None = None, port: int | None = None,
                    listening: list[str] | None = None) -> str:
    """HTTP-адрес ПК-моста для телефона: http://<ip>:<port>.

    `listening` — адреса, на которых мост реально принимает подключения (server/lan_bridge):
    из них берём самый «домашний» (Wi-Fi/Ethernet раньше Tailscale). Телефон сам достроит
    адрес до ws://…/ws?token=…, поэтому отдаём именно базу.
    """
    settings = settings or get_settings()
    candidates = [ip for ip in (listening or []) if _ip_score(ip) >= 0]
    ip = max(candidates, key=_ip_score) if candidates else local_ip()
    return f"http://{ip}:{port or settings.port}"


def is_loopback_only(settings: Settings | None = None, listening: list[str] | None = None) -> bool:
    """True, если мост не принимает подключений из сети — телефон не подключится."""
    if listening is not None:
        return not listening
    settings = settings or get_settings()
    host = (settings.host or "").strip().lower()
    return host in _LOOPBACK


def build_pair_link(url: str, token: str, workspace: str = "") -> str:
    """Собрать deeplink `altair://pair?u=…&t=…&w=…` (значения URL-encoded)."""
    parts = [f"u={quote(url, safe='')}", f"t={quote(token, safe='')}"]
    if workspace:
        parts.append(f"w={quote(workspace, safe='')}")
    return "altair://pair?" + "&".join(parts)


#: On-screen size of the pairing QR code, in CSS pixels (a phone camera reads it off a monitor).
QR_TARGET_PX = 300


def qr_svg(data: str, target_px: int = QR_TARGET_PX) -> str | None:
    """The pairing QR code as an SVG to put straight into the page; None without segno.

    Tuned for a phone camera reading a monitor: error level L (fewer, larger modules for the
    same link), a whole number of pixels per module with crisp edges, the full 4-module quiet
    zone, and a viewBox so the page can resize it without cropping. The old code had no
    viewBox while the CSS forced a smaller size, so only a corner of the code was shown and
    no scanner could read it.
    """
    try:
        import segno
    except ImportError:
        logger.debug("segno is not installed: no QR, only the link.")
        return None
    try:
        import io

        qr = segno.make(data, error="l")
        modules = qr.symbol_size(scale=1, border=4)[0]
        scale = max(4, target_px // modules)
        buf = io.BytesIO()
        qr.save(buf, kind="svg", scale=scale, border=4, svgclass="pair-qr", xmldecl=False, dark="#000", light="#fff")
        size = modules * scale
        svg = buf.getvalue().decode("utf-8")
        return svg.replace("<svg ", f'<svg viewBox="0 0 {size} {size}" shape-rendering="crispEdges" ', 1)
    except Exception:  # noqa: BLE001 - the link is shown anyway
        logger.debug("the QR code was not drawn", exc_info=True)
        return None


def _is_auto_workspace(workspace: str, settings: Settings) -> bool:
    from pathlib import Path

    try:
        auto = (Path(settings.app_dir) / "storage" / "chat_files").resolve()
        return Path(workspace).resolve().is_relative_to(auto)
    except (OSError, ValueError):
        return False


def pair_info(workspace: str = "", settings: Settings | None = None, port: int | None = None,
              listening: list[str] | None = None) -> dict:
    """Полный набор для интерфейса связывания: адрес, токен, ссылка, QR, статус сети.

    `port` — реальный порт сервера (из запроса), чтобы телефон шёл на верный порт;
    `listening` — адреса, на которых мост сейчас реально слушает сеть.
    """
    settings = settings or get_settings()
    real_port = port or settings.port
    url = bridge_base_url(settings, real_port, listening)
    token = ensure_bridge_token(settings)
    ws = (workspace or "").strip()
    # A chat's own scratch folder (the app picks it when the user chose none) means nothing to
    # the phone and makes the QR code much denser: send only a folder the user chose.
    if ws and _is_auto_workspace(ws, settings):
        ws = ""
    link = build_pair_link(url, token, ws)
    return {
        "url": url,
        "token": token,
        "workspace": ws,
        "link": link,
        "qr_svg": qr_svg(link),
        "lan_ip": local_ip(),
        "port": real_port,
        "listening": list(listening or []),
        "loopback_only": is_loopback_only(settings, listening),
        "bridge_lan": settings.bridge_lan,
    }
