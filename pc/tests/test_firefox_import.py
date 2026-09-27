"""Logins moved from Firefox survive a restart of the app's browser.

Recent Firefox keeps cookie expiry in milliseconds; those were dropped, so every imported
cookie became a session cookie and vanished when the browser closed.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

import core.browser_session as bs
import core.firefox_import as ff
from core.browser_session import AgentBrowser

YEAR = 365 * 86400


def _profile(tmp_path: Path, rows: list[tuple]) -> str:
    """A Firefox profile with a cookies.sqlite of the current schema (17)."""
    profile = tmp_path / "abc.default-release"
    profile.mkdir(parents=True)
    conn = sqlite3.connect(profile / "cookies.sqlite")
    conn.execute("CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT, path TEXT, expiry INTEGER, "
                 "isSecure INTEGER, isHttpOnly INTEGER, sameSite INTEGER)")
    conn.executemany("INSERT INTO moz_cookies VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.execute("PRAGMA user_version = 17")
    conn.commit()
    conn.close()
    return str(profile)


def test_millisecond_and_second_expiries_both_become_seconds(tmp_path):
    later = int(time.time()) + YEAR
    path = _profile(tmp_path, [
        (".habr.com", "ms", "1", "/", later * 1000, 1, 1, 1),   # Firefox 13x: milliseconds
        (".habr.com", "sec", "2", "/", later, 1, 0, 0),          # older Firefox: seconds
        (".habr.com", "none", "3", "/", 0, 0, 0, 2),             # a real session cookie
        (".bank.com", "x", "4", "/", later, 1, 1, 1),            # sensitive: never moved
    ])
    cookies = {c["name"]: c for c in ff.read_cookies(path, ["habr.com", "bank.com"])}
    assert set(cookies) == {"ms", "sec", "none"}
    assert cookies["ms"]["expires"] == later and cookies["sec"]["expires"] == later
    assert "expires" not in cookies["none"]


async def test_imported_login_survives_a_browser_restart(tmp_path, monkeypatch):
    later = int(time.time()) + YEAR
    path = _profile(tmp_path / "ff", [(".example.com", "session_id", "abc123", "/", later * 1000, 1, 1, 1)])
    monkeypatch.setitem(bs._EMBEDDED, "dir", tmp_path / "browser")
    monkeypatch.setitem(bs._EMBEDDED, "port", 0)

    first = AgentBrowser()
    try:
        await first.ensure()
    except Exception as exc:  # noqa: BLE001 - no Chrome/Edge on this machine
        pytest.skip(f"No browser available: {str(exc)[:120]}")
    monkeypatch.setattr(bs, "get_agent_browser", lambda: first)
    try:
        assert await ff.import_into_agent(path, ["example.com"]) == 1
    finally:
        await first.close()

    again = AgentBrowser()  # the app restarted: the same profile folder
    ctx = await again.ensure()
    try:
        saved = [c for c in await ctx.cookies("https://example.com") if c["name"] == "session_id"]
    finally:
        await again.close()
    assert saved and saved[0]["value"] == "abc123"
    assert abs(saved[0]["expires"] - later) < 5
