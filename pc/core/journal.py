"""The Journal: one read-only record of everything the agent did on this body.

Every record is one JSON line: what happened (a task started, a tool ran, an approval was
answered, a file was restored, the app updated…), when, in which chat and run. The lines are
only ever appended. Each one carries the hash of the line before it, so an edit or a deleted
line anywhere shows up in `verify()` — the agent can add to the journal, it cannot quietly
rewrite it. Files roll over by size and are never deleted by the app.

The same format serves the window, the terminal and, from 0.3.0, the server bodies: a body's
journal is what the owner reads to know what an agent with wide rights did while nobody watched.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from core.logging_setup import get_logger

logger = get_logger("journal")

#: A journal file rolls over to the next one past this size.
MAX_FILE_BYTES = 16 * 1024 * 1024
#: The longest a single text field may be in a record (tool arguments, outputs, messages):
#: the journal says what happened; the full texts live in the chats.
FIELD_CHARS = 600
GENESIS = "0" * 16

_FILE_PREFIX = "journal-"


def _digest(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()[:16]


def summarize(value: Any, limit: int = FIELD_CHARS) -> Any:
    """A value cut to journal size: long strings shortened, nested structures kept small."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[: limit - 1] + "…"
    if isinstance(value, dict):
        return {str(k): summarize(v, limit) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        items = [summarize(v, limit) for v in list(value)[:40]]
        if len(value) > 40:
            items.append(f"… +{len(value) - 40}")
        return items
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return summarize(str(value), limit)


class Journal:
    """The journal of one body, kept in `folder` as journal-000001.jsonl, journal-000002.jsonl…"""

    def __init__(self, folder: Path, body: str = "pc", max_bytes: int = MAX_FILE_BYTES) -> None:
        self.folder = Path(folder)
        self.body = body
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._seq = 0
        self._prev = GENESIS
        self._file: Path | None = None
        self._loaded = False

    # ------------------------------------------------------------ files

    def files(self) -> list[Path]:
        if not self.folder.is_dir():
            return []
        return sorted(p for p in self.folder.glob(f"{_FILE_PREFIX}*.jsonl") if p.is_file())

    def _load_tail(self) -> None:
        """Where the chain ends: the last record's number and hash."""
        self._loaded = True
        files = self.files()
        if not files:
            self._file = self.folder / f"{_FILE_PREFIX}000001.jsonl"
            return
        self._file = files[-1]
        last = _last_line(self._file)
        if last is None and len(files) > 1:
            last = _last_line(files[-2])
        if last is None:
            return
        try:
            record = json.loads(last)
            self._seq = int(record.get("seq") or 0)
            self._prev = _digest(last)
        except (ValueError, TypeError):
            # A torn last line (the machine died mid-write): the chain goes on from it, and
            # verify() points at the spot.
            self._prev = _digest(last)
            logger.warning("journal: the last line of %s is not whole", self._file.name)

    def _next_file(self) -> Path:
        assert self._file is not None
        try:
            if self._file.stat().st_size < self.max_bytes:
                return self._file
        except OSError:
            return self._file
        number = int(self._file.stem.removeprefix(_FILE_PREFIX) or 0) + 1
        self._file = self.folder / f"{_FILE_PREFIX}{number:06d}.jsonl"
        return self._file

    # ------------------------------------------------------------ writing

    def append(self, kind: str, *, chat: str = "", run: str = "", **data: Any) -> dict[str, Any]:
        """Adds one record and returns it. Never raises for a full disk or a locked file: the
        journal must not stop the agent's work; the failure goes to the log."""
        with self._lock:
            if not self._loaded:
                self._load_tail()
            record = {
                "seq": self._seq + 1,
                "ts": round(time.time(), 3),
                "body": self.body,
                "kind": kind,
                "chat": chat,
                "run": run,
                "data": summarize(data),
                "prev": self._prev,
            }
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                with open(self._next_file(), "a", encoding="utf-8", newline="\n") as fh:
                    fh.write(line + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
            except OSError as exc:
                logger.warning("journal: could not write %s: %s", kind, exc)
                return record
            self._seq += 1
            self._prev = _digest(line)
            return record

    # ------------------------------------------------------------ reading

    def _lines_newest_first(self) -> Iterable[str]:
        for path in reversed(self.files()):
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as exc:
                logger.warning("journal: could not read %s: %s", path.name, exc)
                continue
            yield from reversed(lines)

    def read(self, *, before: int | None = None, since: int | None = None, chat: str = "",
             kinds: Iterable[str] | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """Records newest first. `before`: older than this seq (paging back); `since`: newer
        than it (following new records); `kinds`: exact kinds or prefixes ending with '.'."""
        wanted = [k for k in (kinds or []) if k]
        out: list[dict[str, Any]] = []
        for line in self._lines_newest_first():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            seq = int(record.get("seq") or 0)
            if since is not None and seq <= since:
                break
            if before is not None and seq >= before:
                continue
            if chat and record.get("chat") != chat:
                continue
            kind = str(record.get("kind") or "")
            if wanted and not any(kind == k or (k.endswith(".") and kind.startswith(k)) for k in wanted):
                continue
            out.append(record)
            if len(out) >= limit:
                break
        return out

    def verify(self) -> dict[str, Any]:
        """Walks the whole chain: every line must name the hash of the one before it and the
        numbers must go up by one. Returns {"ok", "checked", "broken_at", "reason"}."""
        prev, expected_seq, checked = GENESIS, 1, 0
        for path in self.files():
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as exc:
                return {"ok": False, "checked": checked, "broken_at": None, "reason": f"unreadable {path.name}: {exc}"}
            for line in lines:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    return {"ok": False, "checked": checked, "broken_at": expected_seq,
                            "reason": f"a damaged line in {path.name}"}
                if record.get("prev") != prev:
                    return {"ok": False, "checked": checked, "broken_at": record.get("seq"),
                            "reason": "the line before it was changed or removed"}
                if record.get("seq") != expected_seq:
                    return {"ok": False, "checked": checked, "broken_at": record.get("seq"),
                            "reason": f"expected record {expected_seq}"}
                prev, expected_seq, checked = _digest(line), expected_seq + 1, checked + 1
        return {"ok": True, "checked": checked, "broken_at": None, "reason": ""}


def _last_line(path: Path) -> str | None:
    """The last non-empty line of a file, read from its end (journals grow to megabytes)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            chunk = 4096
            data = b""
            pos = size
            while pos > 0:
                step = min(chunk, pos)
                pos -= step
                fh.seek(pos)
                data = fh.read(step) + data
                lines = data.splitlines()
                if len(lines) > 1 or pos == 0:
                    for raw in reversed(lines):
                        if raw.strip():
                            return raw.decode("utf-8", errors="replace")
                    if pos == 0:
                        return None
    except OSError:
        return None
    return None


_JOURNALS: dict[str, Journal] = {}
_JOURNALS_LOCK = threading.Lock()


def get_journal(folder: Path, body: str = "pc") -> Journal:
    """One Journal object per folder: every chat and window of the app writes the same chain."""
    key = str(Path(folder).resolve())
    with _JOURNALS_LOCK:
        journal = _JOURNALS.get(key)
        if journal is None:
            journal = _JOURNALS[key] = Journal(Path(folder), body)
        return journal
