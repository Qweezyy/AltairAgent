"""Выборочный перенос логинов (куки) из Firefox в браузер агента.

Firefox хранит куки в `cookies.sqlite` профиля НЕ зашифрованными (в отличие от
Chrome), поэтому их можно прочитать напрямую и перенести в постоянный контекст
браузера агента через `context.add_cookies`.

Безопасность:
- банки/почта/платёжки/крипта/госуслуги — в чёрном списке и НЕ показываются в
  списке для переноса (двойная страховка: список + явный выбор пользователя);
- пользователь сам отмечает галочками, что переносить.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.logging_setup import get_logger

logger = get_logger("firefox_import")

#: Куски домена, по которым сайт считается чувствительным и НЕ переносится.
_SENSITIVE = (
    # почта
    "mail", "gmail", "outlook", "proton", "icloud", "yahoo", "gmx", "yandex",
    # банки/платежи/крипта
    "bank", "pay", "wallet", "sber", "tinkoff", "alfa", "vtb", "gazprom", "raiff",
    "otkritie", "sovcombank", "pochtabank", "mkb", "rshb", "qiwi", "yoomoney",
    "paypal", "stripe", "wise", "revolut", "coinbase", "binance", "bybit", "okx",
    "kraken", "metamask", "trustwallet", "kucoin",
    # гос/налоги/идентификация
    "gosuslugi", "nalog", "gov", "mos.ru", "esia",
)

#: Точные домены-исключения (когда keyword слишком широк).
_SENSITIVE_EXACT = {"mail.ru", "e.mail.ru", "account.mail.ru"}


def _is_sensitive(domain: str) -> bool:
    d = domain.lstrip(".").lower()
    if d in _SENSITIVE_EXACT:
        return True
    return any(k in d for k in _SENSITIVE)


def _registrable(host: str) -> str:
    """Грубое приведение host к «сайту»: последние две метки (example.com)."""
    h = host.lstrip(".")
    parts = h.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else h


@dataclass(slots=True)
class FirefoxProfile:
    name: str
    path: str


def _profiles_root() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "Mozilla" / "Firefox" / "Profiles"


def find_profiles() -> list[FirefoxProfile]:
    """Профили Firefox, где есть cookies.sqlite (в них есть что переносить)."""
    root = _profiles_root()
    out: list[FirefoxProfile] = []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if d.is_dir() and (d / "cookies.sqlite").exists():
            out.append(FirefoxProfile(name=d.name, path=str(d)))
    return out


def _open_cookies_copy(profile_path: str) -> tuple[sqlite3.Connection, str]:
    """Копирует cookies.sqlite (+wal/shm) во временную папку и открывает read-only.

    Копия нужна, потому что при открытом Firefox файл заблокирован.
    """
    src = Path(profile_path) / "cookies.sqlite"
    if not src.exists():
        raise FileNotFoundError("В профиле нет cookies.sqlite")
    tmpdir = tempfile.mkdtemp(prefix="ff_cookies_")
    dst = Path(tmpdir) / "cookies.sqlite"
    shutil.copy2(src, dst)
    for suffix in ("-wal", "-shm"):
        extra = Path(profile_path) / f"cookies.sqlite{suffix}"
        if extra.exists():
            try:
                shutil.copy2(extra, str(dst) + suffix)
            except OSError:  # a locked journal: the main file still has most of the cookies
                logger.debug("cookies.sqlite%s was not copied", suffix, exc_info=True)
    conn = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    return conn, tmpdir


def list_domains(profile_path: str) -> list[dict[str, Any]]:
    """Список сайтов с числом кук, БЕЗ чувствительных (банки/почта/…)."""
    conn, tmpdir = _open_cookies_copy(profile_path)
    try:
        rows = conn.execute("SELECT host, COUNT(*) FROM moz_cookies GROUP BY host").fetchall()
    finally:
        conn.close()
        shutil.rmtree(tmpdir, ignore_errors=True)
    agg: dict[str, int] = {}
    for host, cnt in rows:
        site = _registrable(str(host or ""))
        if not site or _is_sensitive(site) or _is_sensitive(str(host or "")):
            continue
        agg[site] = agg.get(site, 0) + int(cnt)
    return [{"domain": d, "count": n} for d, n in sorted(agg.items())]


#: 31.12.9999 in unix seconds: anything larger is a millisecond expiry.
_MAX_SECONDS = 253402300799


def _samesite(v: Any) -> str:
    return {0: "None", 1: "Lax", 2: "Strict"}.get(int(v or 0), "Lax")


def read_cookies(profile_path: str, domains: list[str]) -> list[dict[str, Any]]:
    """Куки выбранных сайтов в формате Playwright add_cookies."""
    picked = {d.lstrip(".").lower() for d in domains if not _is_sensitive(d)}
    conn, tmpdir = _open_cookies_copy(profile_path)
    try:
        rows = conn.execute(
            "SELECT host, name, value, path, expiry, isSecure, isHttpOnly, sameSite FROM moz_cookies"
        ).fetchall()
    finally:
        conn.close()
        shutil.rmtree(tmpdir, ignore_errors=True)
    cookies: list[dict[str, Any]] = []
    for host, name, value, path, expiry, secure, http_only, same in rows:
        site = _registrable(str(host or ""))
        if site not in picked or _is_sensitive(str(host or "")):
            continue
        c: dict[str, Any] = {
            "name": str(name),
            "value": str(value),
            "domain": str(host),
            "path": str(path or "/"),
            "httpOnly": bool(http_only),
            "secure": bool(secure),
            "sameSite": _samesite(same),
        }
        # Recent Firefox (cookies.sqlite schema 17) stores the expiry in milliseconds, older
        # ones in seconds. Dropping the millisecond values made every imported cookie a
        # session cookie, which the browser erases when it closes: logins did not survive
        # a restart or an update.
        try:
            exp = int(expiry or 0)
        except (TypeError, ValueError):
            exp = 0
        if exp > _MAX_SECONDS:
            exp //= 1000
        if 0 < exp <= _MAX_SECONDS:
            c["expires"] = exp
        cookies.append(c)
    return cookies


async def import_into_agent(profile_path: str, domains: list[str]) -> int:
    """Переносит куки выбранных сайтов в постоянный контекст браузера агента."""
    from core.browser_session import get_agent_browser

    cookies = read_cookies(profile_path, domains)
    if not cookies:
        return 0
    b = get_agent_browser()
    ctx = await b.ensure()
    try:
        await ctx.add_cookies(cookies)
        done = len(cookies)
    except Exception:  # noqa: BLE001 - одна плохая кука роняет всю пачку, добавляем по одной
        done = 0
        for c in cookies:
            try:
                await ctx.add_cookies([c])
                done += 1
            except Exception as exc:  # noqa: BLE001 - one bad cookie must not stop the rest
                logger.debug("cookie %s for %s was refused: %s", c["name"], c["domain"], exc)
    logger.info("Перенесено куки из Firefox: %d/%d (сайтов: %d)", done, len(cookies), len(domains))
    return done
