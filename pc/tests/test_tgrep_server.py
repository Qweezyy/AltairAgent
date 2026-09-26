"""Персистентный tgrep-сервер (опция для огромных репозиториев)."""

from __future__ import annotations

import asyncio

import pytest

from core import tgrep_server as ts
from core.search_backend import _find_exe
from core.tgrep_server import TgrepServerManager


async def test_none_without_tgrep(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "_find_exe", lambda name: None)
    m = TgrepServerManager()
    m.configure(index_root=tmp_path / "idx", min_files=1)
    assert await m.ready_index_path(str(tmp_path)) is None


async def test_none_without_configure(tmp_path, monkeypatch):
    # Даже если tgrep есть, без configure (index_root=None) — не поднимаем.
    monkeypatch.setattr(ts, "_find_exe", lambda name: "tgrep")
    m = TgrepServerManager()
    assert await m.ready_index_path(str(tmp_path)) is None


def test_index_path_is_deterministic_and_under_root(tmp_path):
    m = TgrepServerManager()
    m.configure(index_root=tmp_path / "idx", min_files=1)
    a = m._index_path_for(r"C:\Repo\Big")
    b = m._index_path_for(r"C:\Repo\Big")
    assert a == b  # детерминирован
    assert str((tmp_path / "idx").resolve()) in str(a)  # под app-data, не в проекте


def test_stop_all_is_idempotent():
    m = TgrepServerManager()
    m.stop_all()
    m.stop_all()  # без ошибок, даже когда ничего не поднято


async def test_small_repo_is_skipped(tmp_path):
    """Маленькая папка не поднимает демон (прямой скан и так мгновенен)."""
    if _find_exe("tgrep") is None:
        pytest.skip("tgrep недоступен")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    m = TgrepServerManager()
    m.configure(index_root=tmp_path / "idx", min_files=10**9)  # порог заведомо не достижим
    assert await m.ready_index_path(str(tmp_path)) is None
    srv = next(iter(m._servers.values()))
    await srv.task  # дождаться фоновой проверки размера
    assert srv.state == "skip"
    assert srv.proc is None
    assert await m.ready_index_path(str(tmp_path)) is None
    m.stop_all()


async def test_serves_indexes_and_stops(tmp_path):
    """Интеграция: сервер поднимается, ищет по индексу, гасится."""
    if _find_exe("tgrep") is None:
        pytest.skip("tgrep недоступен")
    (tmp_path / "f.py").write_text("HELLO_TOKEN = 1\n", encoding="utf-8")
    m = TgrepServerManager()
    m.configure(index_root=tmp_path / "idx", min_files=1)
    await m.ready_index_path(str(tmp_path))
    srv = next(iter(m._servers.values()))
    idx = None
    for _ in range(60):
        idx = await m.ready_index_path(str(tmp_path))
        if idx:
            break
        await asyncio.sleep(0.5)
    try:
        assert idx and srv.state == "ready"
        from core.search_backend import candidate_files

        cands = await candidate_files("HELLO_TOKEN", str(tmp_path), case_sensitive=False, serve_index_path=idx)
        assert cands and any("f.py" in p for p in cands)
    finally:
        m.stop_all()
    await asyncio.sleep(0.5)
    assert srv.proc is not None and srv.proc.poll() is not None  # процесс погашен
