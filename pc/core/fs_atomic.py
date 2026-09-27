"""Atomic file writes that survive parallel writers and odd Windows volumes.

Two problems this module solves for every state file of the app (sessions, run markers,
settings, commands, memory, presets, permissions, providers, browser network rules):

* On some Windows setups the app data folder (``%LOCALAPPDATA%``) is a junction to another
  volume, and ``os.replace`` fails with ``WinError 17`` even inside one folder.
  ``safe_replace`` falls back to overwriting the file in place.
* Tools run in parallel threads, and a chat title is saved while the run saves the same
  chat. With one shared ``<file>.tmp`` two writers overwrite each other's temp file and a
  record is silently lost (the rollback bug fixed in ``checkpoints.py``). ``atomic_write_text``
  gives every write its own temp file, serialises writers of one file with a lock, and can
  drop a write whose snapshot is older than one already on disk.
"""

from __future__ import annotations

import errno
import itertools
import os
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger("fs_atomic")

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_WRITTEN_SEQ: dict[str, int] = {}
_SEQ = itertools.count(1)


def _key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def next_seq() -> int:
    """A process-wide increasing number: take it when the snapshot of the data is made."""
    return next(_SEQ)


@contextmanager
def path_lock(path: Path) -> Iterator[None]:
    """Hold the file's lock, e.g. around a read-modify-write of it (reentrant)."""
    key = _key(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    with lock:
        yield


_CROSS_DEVICE = {errno.EXDEV}
_WINERROR_NOT_SAME_DEVICE = 17
_REPLACE_ATTEMPTS = 25


def safe_replace(temp: Path, path: Path) -> None:
    """Replace ``path`` with ``temp``: atomically, or by a direct overwrite when the OS refuses
    (cross-device on a junction folder).

    On Windows a replace is also refused while another thread has the target open (a reader
    listing the chats). That is transient: retry the atomic replace instead of overwriting in
    place, which would let the reader see a half-written file.
    """
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(temp, path)
            return
        except OSError as exc:
            cross_device = (exc.errno in _CROSS_DEVICE
                            or getattr(exc, "winerror", None) == _WINERROR_NOT_SAME_DEVICE)
            if cross_device or not isinstance(exc, PermissionError) or attempt == _REPLACE_ATTEMPTS - 1:
                break
            time.sleep(0.01 * (attempt + 1))
    try:
        path.write_bytes(Path(temp).read_bytes())
    finally:
        try:
            Path(temp).unlink()
        except OSError:
            logger.debug("temp file %s was not removed", temp, exc_info=True)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8", seq: int | None = None) -> bool:
    """Write ``text`` to ``path`` through a temp file of its own.

    ``seq`` (from ``next_seq()`` taken when the text was produced) makes the write skip when
    a newer snapshot of the same file has been written already: threads may finish in any
    order. Returns False for such a skipped write. OSError propagates to the caller.
    """
    path = Path(path)
    with path_lock(path):
        key = _key(path)
        if seq is not None and seq < _WRITTEN_SEQ.get(key, 0):
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            temp.write_text(text, encoding=encoding)
            safe_replace(temp, path)
        except OSError:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                logger.debug("temp file %s was not removed", temp, exc_info=True)
            raise
        if seq is not None:
            _WRITTEN_SEQ[key] = seq
        return True
