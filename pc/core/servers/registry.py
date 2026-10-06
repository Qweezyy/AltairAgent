"""servers.json: the servers this PC installed the agent on, and how to reach them.

No passwords are kept here, ever: the login password is used once to put this PC's own SSH key
on the server, and from then on the key is used. The key itself lives next to this file
(keys/<id>), readable by its owner only.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.fs_atomic import atomic_write_text
from core.logging_setup import get_logger

logger = get_logger("servers")


@dataclass
class ServerRecord:
    id: str
    name: str
    host: str
    port: int = 22
    user: str = "root"
    #: The server's SSH host key as seen on first contact ("ssh-ed25519 AAAA…"): later
    #: connections must present the same key, or they are refused (a man in the middle).
    host_key: str = ""
    host_key_fingerprint: str = ""
    #: The agent's body id on the server (its key, core/bodies.py).
    body_id: str = ""
    arch: str = ""
    system: str = ""
    version: str = ""
    mode: str = "owner"
    #: Where the agent lives there.
    install_dir: str = "/opt/altair"
    data_dir: str = "/var/lib/altair"
    service: str = "altair"
    user_service: bool = False
    port_remote: int = 8137
    added_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class ServerStore:
    def __init__(self, data_dir: Path) -> None:
        self.folder = Path(data_dir) / "servers"
        self.path = self.folder / "servers.json"
        self._lock = threading.Lock()

    @property
    def keys_dir(self) -> Path:
        return self.folder / "keys"

    def key_path(self, sid: str) -> Path:
        return self.keys_dir / sid

    def all(self) -> list[ServerRecord]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        out = []
        for item in raw if isinstance(raw, list) else []:
            try:
                out.append(ServerRecord(**{k: item[k] for k in ServerRecord.__dataclass_fields__ if k in item}))
            except (TypeError, KeyError):
                continue
        return out

    def get(self, sid: str) -> ServerRecord | None:
        return next((s for s in self.all() if s.id == sid), None)

    def save(self, record: ServerRecord) -> None:
        record.updated_at = time.time()
        with self._lock:
            items = [s for s in self.all() if s.id != record.id]
            items.append(record)
            self.folder.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.path, json.dumps([asdict(s) for s in items], ensure_ascii=False, indent=2))

    def remove(self, sid: str) -> bool:
        with self._lock:
            items = self.all()
            kept = [s for s in items if s.id != sid]
            if len(kept) == len(items):
                return False
            atomic_write_text(self.path, json.dumps([asdict(s) for s in kept], ensure_ascii=False, indent=2))
        for path in (self.key_path(sid), self.key_path(sid).with_suffix(".pub")):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("could not delete the server key %s: %s", path.name, exc)
        return True

    @staticmethod
    def public(record: ServerRecord) -> dict[str, Any]:
        return asdict(record)
