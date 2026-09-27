"""Parallel edits must never lose a snapshot: whole-run rollback depends on the manifest.

Tools run in parallel worker threads. Before the fix a save was not atomic with respect to
other saves: thread A could serialize the manifest, thread B could then append and save its
own snapshot, and A would finally write its stale copy over B's — the manifest on disk lost
B's snapshot and "roll back the run" silently skipped that file. The tests force exactly that
interleaving (A pauses right after serializing), so they fail deterministically without the fix.
"""

from __future__ import annotations

import json as real_json
import threading
import time

import core.checkpoints as checkpoints_module
from core.checkpoints import CheckpointStore


class _SlowFirstDump:
    """json stand-in: the first dumps() pauses after serializing, widening the race window."""

    def __init__(self) -> None:
        self._calls = 0
        self._guard = threading.Lock()

    def dumps(self, *args, **kwargs):
        text = real_json.dumps(*args, **kwargs)
        with self._guard:
            self._calls += 1
            first = self._calls == 1
        if first:
            time.sleep(0.3)
        return text

    def loads(self, *args, **kwargs):
        return real_json.loads(*args, **kwargs)


def _interleaved_snapshots(monkeypatch, store: CheckpointStore, ws, first: str, second: str) -> None:
    monkeypatch.setattr(checkpoints_module, "json", _SlowFirstDump())
    t1 = threading.Thread(target=store.snapshot, args=(ws / first, first, "write"))
    t2 = threading.Thread(target=store.snapshot, args=(ws / second, second, "write"))
    t1.start()
    time.sleep(0.05)  # the second edit lands while the first save is mid-way
    t2.start()
    t1.join()
    t2.join()
    monkeypatch.setattr(checkpoints_module, "json", real_json)


def test_concurrent_snapshots_both_reach_the_manifest(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    store = CheckpointStore(tmp_path / "data", "s1", ws)
    store.set_run("r1")
    _interleaved_snapshots(monkeypatch, store, ws, "a.txt", "b.txt")
    # A later rollback reads the manifest from disk, not this instance's memory.
    on_disk = {cp.path for cp in CheckpointStore(tmp_path / "data", "s1", ws).records()}
    assert on_disk == {"a.txt", "b.txt"}


def test_rollback_after_concurrent_edits_restores_every_file(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.txt").write_text("before", encoding="utf-8")
    store = CheckpointStore(tmp_path / "data", "s1", ws)
    store.set_run("r1")
    _interleaved_snapshots(monkeypatch, store, ws, "a.txt", "new.txt")
    (ws / "a.txt").write_text("after", encoding="utf-8")
    (ws / "new.txt").write_text("created", encoding="utf-8")

    result = CheckpointStore(tmp_path / "data", "s1", ws).restore_run("r1")

    assert set(result["restored"]) == {"a.txt", "new.txt"}
    assert (ws / "a.txt").read_text(encoding="utf-8") == "before"
    assert not (ws / "new.txt").exists()


def test_many_parallel_snapshots_are_kept_and_leave_no_temp_files(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    names = [f"f{i}.txt" for i in range(8)]
    store = CheckpointStore(tmp_path / "data", "s1", ws)
    barrier = threading.Barrier(len(names))

    def snap(name: str) -> None:
        barrier.wait()
        store.snapshot(ws / name, name, "write")

    threads = [threading.Thread(target=snap, args=(name,)) for name in names]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    on_disk = {cp.path for cp in CheckpointStore(tmp_path / "data", "s1", ws).records()}
    assert on_disk == set(names)
    assert not list(store.dir.glob("*.tmp"))
