"""The update shows how it goes and survives a stalled connection; web links open outside.

Found live: updating 0.2.0 → 0.2.1 the download froze at 16 of 148 MB (a fresh connection ran at
9 MB/s) while the window only said "downloading started" — one silent request did it all.
"""

from __future__ import annotations

import functools
import hashlib
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import core.updater as updater_module
from core.updater import UpdateInfo, Updater

PACKAGE = bytes(range(256)) * 4096  # 1 MiB with varied bytes, so a wrong splice shows


class _Stall(httpx.AsyncByteStream):
    """Sends `upto` bytes of the package, then the connection stalls (a read timeout)."""

    def __init__(self, data: bytes, upto: int) -> None:
        self.data, self.upto = data, upto

    async def __aiter__(self):
        for i in range(0, self.upto, 65536):
            yield self.data[i:min(i + 65536, self.upto)]
        raise httpx.ReadTimeout("stalled")

    async def aclose(self) -> None:
        return None


def _serve(monkeypatch, behaviours):
    """Each request takes the next behaviour: ("stall", upto) | ("range",) | ("full",)."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("range"))
        kind = behaviours.pop(0)
        if kind[0] == "stall":
            start = int((request.headers.get("range") or "bytes=0-")[6:].rstrip("-") or 0)
            part = PACKAGE[start:]
            return httpx.Response(206 if start else 200, headers={"content-length": str(len(part))},
                                  stream=_Stall(part, kind[1]))
        if kind[0] == "range" and request.headers.get("range"):
            start = int(request.headers["range"][6:].rstrip("-"))
            return httpx.Response(206, content=PACKAGE[start:])
        return httpx.Response(200, content=PACKAGE)

    monkeypatch.setattr(updater_module.httpx, "AsyncClient",
                        functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)))

    async def no_wait(_s):
        return None

    monkeypatch.setattr(updater_module.asyncio, "sleep", no_wait)
    return seen


def _info():
    return UpdateInfo(available=True, version="9.9.9", url="https://dl.example/pkg.zip",
                      sha256=hashlib.sha256(PACKAGE).hexdigest(), signed=True)


async def test_a_stalled_download_continues_where_it_stopped(settings, monkeypatch):
    seen = _serve(monkeypatch, [("stall", 400_000), ("range",)])
    reports = []
    path = await Updater(settings).download(_info(), lambda *a: reports.append(a))
    assert path.read_bytes() == PACKAGE, "the resumed part must join the first one exactly"
    # It continues from what was written (httpx hands the bytes over in 256 KB pieces).
    assert seen[0] is None and seen[1].startswith("bytes=") and 0 < int(seen[1][6:-1]) <= 400_000
    downloading = [r for r in reports if r[0] == "downloading"]
    assert downloading[-1][1] == downloading[-1][2] == len(PACKAGE)
    assert max(r[3] for r in downloading) == 1  # one resume, and the window is told about it
    assert reports[-1][0] == "verifying"


async def test_a_server_that_ignores_the_range_is_downloaded_again_from_the_start(settings, monkeypatch):
    _serve(monkeypatch, [("stall", 300_000), ("full",)])
    path = await Updater(settings).download(_info())
    assert path.read_bytes() == PACKAGE


async def test_a_download_that_keeps_stalling_gives_up_with_a_reason(settings, monkeypatch):
    _serve(monkeypatch, [("stall", 10)] * (updater_module.MAX_RESUMES + 1))
    with pytest.raises(ValueError, match="kept stalling"):
        await Updater(settings).download(_info())


# ---------------------------------------------------------------- the install job and its progress


def _client(monkeypatch, settings):
    import core.settings as settings_module
    import server.app as app_module
    import server.ws as ws_module
    from server.app import create_app

    for mod in (settings_module, app_module, ws_module):
        monkeypatch.setattr(mod, "get_settings", lambda: settings)
    return TestClient(create_app())


def test_the_install_runs_in_the_background_and_reports_each_stage(monkeypatch, settings, tmp_path):
    async def check(self):
        return UpdateInfo(available=True, installable=True, version="9.9.9", url="https://x/y.zip")

    async def download(self, info, on_progress=None):
        for done in (0, 50, 100):
            on_progress("downloading", done, 100, 0)
        on_progress("verifying", 0, 0, 0)
        return tmp_path / "pkg.zip"

    applied = []
    monkeypatch.setattr(Updater, "check", check)
    monkeypatch.setattr(Updater, "download", download)
    monkeypatch.setattr(Updater, "unpack", lambda self, archive: tmp_path)
    monkeypatch.setattr(Updater, "apply", lambda self, folder: applied.append(folder))
    with _client(monkeypatch, settings) as tc:
        assert tc.get("/api/update/progress").json()["stage"] == "idle"
        assert tc.post("/api/update/install").json() == {"ok": True, "started": True}
        for _ in range(100):
            job = tc.get("/api/update/progress").json()
            if job["stage"] in ("ready", "error"):
                break
            time.sleep(0.05)
        assert job["stage"] == "ready", job
        assert job["version"] == "9.9.9" and job["done"] == job["total"] == 100
        assert applied == [tmp_path]


def test_a_failed_install_says_why(monkeypatch, settings):
    async def check(self):
        return UpdateInfo(available=True, installable=False, error="This release is not signed")

    monkeypatch.setattr(Updater, "check", check)
    with _client(monkeypatch, settings) as tc:
        tc.post("/api/update/install")
        for _ in range(100):
            job = tc.get("/api/update/progress").json()
            if job["stage"] == "error":
                break
            time.sleep(0.05)
        assert job["stage"] == "error" and "not signed" in job["error"]


# ---------------------------------------------------------------- web links from the desktop window


def test_web_links_open_in_the_system_browser_only_from_this_pc(monkeypatch, settings):
    import webbrowser

    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    with _client(monkeypatch, settings) as tc:
        url = "https://github.com/Qweezyy/AltairAgent/blob/main/README.ru.md"
        assert tc.post("/api/open-url", json={"url": url}).json() == {"ok": True}
        assert tc.post("/api/open-url", json={"url": "file:///C:/Windows/System32/calc.exe"}).json()["ok"] is False
        assert tc.post("/api/open-url", json={"url": "javascript:alert(1)"}).json()["ok"] is False
    assert opened == [url]
    import server.app as app_module

    monkeypatch.setattr(app_module, "_LOOPBACK_HOSTS", {"127.0.0.1"})  # the test client as a LAN device
    with _client(monkeypatch, settings) as lan:
        assert lan.post("/api/open-url", json={"url": url}).status_code == 403
    assert opened == [url], "a phone on the network must not open pages on this PC"
