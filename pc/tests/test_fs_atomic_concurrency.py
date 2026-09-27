"""Parallel writers of one state file: no lost writes, no stale overwrites, no leftovers."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import core.fs_atomic as fs_atomic
from core.agent.session import Session
from core.agent.storage import SessionStore
from core.fs_atomic import atomic_write_text, next_seq


def _slow_writes(monkeypatch, delay_event: threading.Event | None = None):
    """Make every temp-file write yield mid-way, the window where a shared temp file lost data."""
    original = Path.write_text

    def slow(self, data, *a, **kw):
        if self.name.endswith(".tmp"):
            half = len(data) // 2
            original(self, data[:half], *a, **kw)
            threading.Event().wait(0.002)
            return original(self, data, *a, **kw)
        return original(self, data, *a, **kw)

    monkeypatch.setattr(Path, "write_text", slow)


def test_parallel_writers_never_corrupt_the_file_or_leave_temp_files(tmp_path, monkeypatch):
    _slow_writes(monkeypatch)
    target = tmp_path / "state.json"
    errors: list[BaseException] = []

    def writer(n: int) -> None:
        try:
            for i in range(15):
                atomic_write_text(target, json.dumps({"writer": n, "i": i, "pad": "x" * 2000}))
        except BaseException as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert json.loads(target.read_text(encoding="utf-8"))["pad"] == "x" * 2000  # whole, valid JSON
    assert list(tmp_path.glob("*.tmp")) == []


def test_an_older_snapshot_does_not_overwrite_a_newer_one(tmp_path):
    target = tmp_path / "chat.json"
    old_seq, new_seq = next_seq(), next_seq()
    assert atomic_write_text(target, "new", seq=new_seq)
    assert not atomic_write_text(target, "old", seq=old_seq)  # its thread just finished later
    assert target.read_text(encoding="utf-8") == "new"


def test_chat_title_save_racing_the_run_keeps_both(settings):
    """The title task and the run save the same chat: whichever thread finishes last, the file
    ends with the newest snapshot (the title *and* the messages)."""
    store = SessionStore(settings=settings)
    session = Session(title="Новый диалог")
    session.add_user("вопрос")
    early = store._snapshot(session)              # the run's save: messages, no title yet
    session.title = "Название от модели"
    late = store._snapshot(session)               # the title's save
    store._write(session.id, *late)
    store._write(session.id, *early)              # the older write lands last
    stored = store.load(session.id)
    assert stored.title == "Название от модели" and stored.messages


async def test_async_save_snapshots_before_the_session_changes(settings):
    store = SessionStore(settings=settings)
    session = Session()
    session.add_user("первое")
    pending = store.async_save(session)
    task = __import__("asyncio").ensure_future(pending)
    await __import__("asyncio").sleep(0)          # the snapshot is taken on this thread...
    session.messages.clear()                      # ...so a change right after it is not written
    await task
    assert store.load(session.id).messages


def test_env_writes_from_many_threads_keep_every_key(settings, monkeypatch):
    from core import config_file

    _slow_writes(monkeypatch)
    errors: list[BaseException] = []

    def save(n: int) -> None:
        try:
            config_file.write_values({f"TEST_KEY_{n}": str(n)}, settings)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=save, args=(n,)) for n in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    text = config_file.config_path(settings).read_text(encoding="utf-8")
    assert all(f"TEST_KEY_{n}={n}" in text for n in range(16)), text


def test_path_lock_is_reentrant(tmp_path):
    target = tmp_path / "x.json"
    with fs_atomic.path_lock(target), fs_atomic.path_lock(target):
        atomic_write_text(target, "{}")
    assert target.read_text(encoding="utf-8") == "{}"


def test_a_reader_holding_the_file_does_not_force_an_in_place_overwrite(tmp_path, monkeypatch):
    """Windows refuses a replace while the target is open; the old fallback then overwrote the
    file in place and a concurrent reader saw it half-written. Now the replace is retried."""
    import sys

    target = tmp_path / "chat.json"
    atomic_write_text(target, "old")
    overwrites: list[Path] = []
    original = Path.write_bytes
    monkeypatch.setattr(Path, "write_bytes", lambda self, data: (overwrites.append(self), original(self, data))[1])
    reader = open(target, encoding="utf-8")  # noqa: SIM115 - held open on purpose
    writer = threading.Thread(target=atomic_write_text, args=(target, "new"))
    writer.start()
    threading.Event().wait(0.05)
    reader.close()
    writer.join()
    assert target.read_text(encoding="utf-8") == "new"
    if sys.platform == "win32":
        assert overwrites == []  # it waited for the reader instead of writing in place
