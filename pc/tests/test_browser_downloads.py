# ruff: noqa: ASYNC240 — tests read their fixture files synchronously on purpose
"""Quarantine for browser downloads: check first, then decide — without a browser."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import core.browser_downloads as bd
from core.browser_downloads import DownloadStore, safe_name, sniff_executable


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "defender_scan", lambda path: ("clean", "Microsoft Defender: no threats"))
    return DownloadStore(tmp_path / "quarantine")


async def _download(store: DownloadStore, name: str, data: bytes):
    path = store.new_path(name)
    path.write_bytes(data)
    await store.started("https://example.com/" + name, path)
    return await store.finished(path, True, "https://example.com/" + name)


async def test_clean_file_moves_with_mark_of_the_web(store, tmp_path):
    item = await _download(store, "report.csv", b"a,b\n1,2\n")
    assert item.status == "clean" and item.size == 8 and len(item.sha256) == 64
    moved = await store.move(item.id, tmp_path / "project", confirmed=False)
    target = Path(moved.moved_to)
    assert target.read_bytes() == b"a,b\n1,2\n"
    assert not any(store.quarantine.glob("*/report.csv"))  # left quarantine
    if os.name == "nt":
        motw = Path(f"{target}:Zone.Identifier").read_text(encoding="utf-8")
        assert "ZoneId=3" in motw and "example.com" in motw


async def test_risky_type_needs_confirmation(store, tmp_path):
    item = await _download(store, "setup.exe", b"MZ\x90\x00program")
    assert item.status == "risky" and item.needs_confirmation
    with pytest.raises(PermissionError):
        await store.move(item.id, tmp_path / "out", confirmed=False)
    moved = await store.move(item.id, tmp_path / "out", confirmed=True)
    assert Path(moved.moved_to).exists()


async def test_disguised_executable_is_suspicious(store):
    item = await _download(store, "invoice.pdf", b"MZ\x90\x00this is a program")
    assert item.status == "suspicious" and "pretends" in item.detail


async def test_threat_is_deleted_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "defender_scan", lambda path: ("threat", "Microsoft Defender: Threat EICAR"))
    store = DownloadStore(tmp_path / "q")
    item = await _download(store, "bad.txt", b"not really")
    assert item.status == "threat"
    assert not Path(item.path).exists()
    with pytest.raises(ValueError):
        await store.move(item.id, tmp_path / "out", confirmed=True)


async def test_unscanned_file_needs_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "defender_scan", lambda path: ("unavailable", "Defender is not available"))
    store = DownloadStore(tmp_path / "q")
    item = await _download(store, "notes.txt", b"hello")
    assert item.status == "unscanned" and item.needs_confirmation


async def test_delete_and_registry_survive_restart(store, tmp_path):
    item = await _download(store, "a.txt", b"x")
    await store.delete(item.id)
    assert not Path(item.path).exists()
    again = DownloadStore(store.quarantine)
    assert again.get(item.id).status == "deleted"
    assert not list(store.quarantine.glob("index*.tmp"))


async def test_old_downloads_are_forgotten(store):
    item = await _download(store, "old.txt", b"x")
    item.created -= 8 * 86400
    assert await store.forget_old() == 1
    assert not Path(item.path).exists()


def test_safe_name_and_sniffing(tmp_path):
    assert safe_name("../../etc/passwd") == "_.._etc_passwd" or "/" not in safe_name("../../etc/passwd")
    assert safe_name("con.txt").startswith("_")
    assert safe_name('a<b>:"c".pdf') == "a_b___c_.pdf"
    exe = tmp_path / "x.bin"
    exe.write_bytes(b"MZ\x00\x00")
    assert sniff_executable(exe)
    txt = tmp_path / "x.txt"
    txt.write_text("hello", encoding="utf-8")
    assert not sniff_executable(txt)


def test_defender_output_is_parsed(monkeypatch, tmp_path):
    class Proc:
        returncode = 2
        stdout = "Scan starting...\nScan finished.\nScanning x found 1 threats.\nThreat  : Virus:DOS/EICAR_Test_File"
        stderr = ""

    monkeypatch.setattr(bd, "_defender_exe", lambda: Path("MpCmdRun.exe"))
    monkeypatch.setattr(bd.subprocess, "run", lambda *a, **k: Proc())
    verdict, detail = bd.defender_scan(tmp_path / "f")
    assert verdict == "threat" and "EICAR" in detail
    Proc.stdout, Proc.returncode = "Scanning x found no threats.", 0
    assert bd.defender_scan(tmp_path / "f")[0] == "clean"
    Proc.stdout = "something unexpected"
    assert bd.defender_scan(tmp_path / "f")[0] == "unavailable"


async def test_risky_move_asks_the_user_even_without_confirmations(tmp_path, monkeypatch, settings):
    """In 'bypass' mode nothing asks — except moving a risky file out of quarantine."""
    import core.tools.builtin.browser_tools as bt
    from core.tools.base import ToolContext

    monkeypatch.setattr(bd, "defender_scan", lambda path: ("clean", "ok"))
    store = DownloadStore(tmp_path / "q")
    item = await _download(store, "setup.exe", b"MZ program")

    class Fake:
        downloads = store

    monkeypatch.setattr(bt, "get_agent_browser", lambda: Fake())
    asked: list[str] = []

    async def approver(request):
        asked.append(request.reason)
        return False

    settings.approval_mode = "bypass"
    ctx = ToolContext(settings=settings, approver=approver)
    result = await bt.BrowserDownloadsTool().invoke({"action": "move", "id": item.id}, ctx)
    assert not result.ok and asked and "WARNING" in asked[0]
    assert store.get(item.id).status == "risky"  # still in quarantine

    listing = await bt.BrowserDownloadsTool().invoke({"action": "list"}, ctx)
    assert listing.ok and "setup.exe" in listing.content and len(asked) == 1  # listing never asks
