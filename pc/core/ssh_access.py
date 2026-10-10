"""How a PC reaches this server over SSH, so revoking that PC really keeps it out (0.3.0, stage 7).

A PC talks to its server through an SSH tunnel: to the agent there it is "this machine", not a
body signed in with its key, so taking back the PC's body key alone would change nothing. The
installer records here which authorized_keys line and which key fingerprint belong to each PC
body; revoking that body (from the PC itself or the owner's phone) removes the line and ends the
SSH sessions open with that key, the tunnel included.

The file is identity/ssh_access.json in the agent's data folder:
    {"<pc body id>": {"authorized_keys": "/root/.ssh/authorized_keys",
                      "marker": "altair-pc-<server id>", "fingerprint": "SHA256:…"}}
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
from pathlib import Path
from typing import Any

from core.logging_setup import get_logger

logger = get_logger("ssh_access")

FILE = "ssh_access.json"
_FIELDS = ("authorized_keys", "marker", "fingerprint")
# "sshd[1234]: Accepted publickey for root from 203.0.113.7 port 50122 ssh2: ED25519 SHA256:abc…"
# (OpenSSH 10 logs it as "sshd-session[1234]").
_ACCEPTED = re.compile(r"sshd(?:-session)?\[(\d+)\]: Accepted publickey for \S+ from \S+ port \d+ \S+: \S+ (SHA256:\S+)")


class SshAccess:
    def __init__(self, folder: Path) -> None:
        self.path = Path(folder) / FILE

    def all(self) -> dict[str, dict[str, str]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            logger.warning("unreadable %s", self.path, exc_info=True)
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, body_id: str) -> dict[str, str] | None:
        return self.all().get(body_id)

    def record(self, body_id: str, info: dict[str, Any]) -> dict[str, str]:
        entry = {k: str(info.get(k) or "") for k in _FIELDS}
        if not all(entry.values()) or not entry["marker"].startswith("altair-pc-"):
            raise ValueError(f"ssh access needs {', '.join(_FIELDS)} (marker altair-pc-…)")
        data = self.all()
        data[body_id] = entry
        self._write(data)
        return entry

    def forget(self, body_id: str) -> None:
        data = self.all()
        if data.pop(body_id, None) is not None:
            self._write(data)

    def _write(self, data: dict) -> None:
        from core.fs_atomic import atomic_write_text

        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(data, indent=1))


def remove_key_line(authorized_keys: Path, marker: str) -> bool:
    """Drops the lines of `marker` from authorized_keys, keeping the rest and the file's mode.
    True when one was there."""
    try:
        text = authorized_keys.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    lines = text.splitlines(keepends=True)
    kept = [ln for ln in lines if marker not in ln]
    if len(kept) == len(lines):
        return False
    mode = authorized_keys.stat().st_mode & 0o777
    tmp = authorized_keys.with_name(authorized_keys.name + ".altair-tmp")
    tmp.write_text("".join(kept), encoding="utf-8")
    os.chmod(tmp, mode)
    stat = authorized_keys.stat()
    if hasattr(os, "chown"):
        try:
            os.chown(tmp, stat.st_uid, stat.st_gid)
        except PermissionError:
            logger.debug("could not keep the owner of %s", authorized_keys)
    os.replace(tmp, authorized_keys)
    return True


def sessions_with_key(fingerprint: str, log_text: str) -> list[int]:
    """The sshd processes of the connections that logged in with this key (from sshd's log)."""
    return sorted({int(m.group(1)) for m in _ACCEPTED.finditer(log_text) if m.group(2) == fingerprint})


def _sshd_log() -> str:
    """sshd's recent log: the journal (its short form keeps the "sshd[pid]:" prefix), else the
    auth log files."""
    try:
        out = subprocess.run(["journalctl", "-q", "--no-pager", "--since", "-14d", "-t", "sshd", "-t", "sshd-session"],
                             capture_output=True, text=True, timeout=30).stdout
        if out:
            return out
    except (OSError, subprocess.SubprocessError):
        logger.debug("no journal", exc_info=True)
    for name in ("/var/log/auth.log", "/var/log/secure"):
        try:
            return Path(name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


def _is_sshd(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip().startswith("sshd")
    except OSError:
        return False


def cut(info: dict[str, str], log_text: str | None = None) -> dict[str, Any]:
    """Takes a PC's SSH access away: its authorized_keys line, then its open sessions (the
    tunnel). Ending a session needs the rights over sshd's process: a system install has them;
    an install in a user's home does not, and its open tunnel lasts until it drops."""
    removed = False
    try:
        removed = remove_key_line(Path(info["authorized_keys"]), info["marker"])
    except OSError:
        logger.exception("could not change %s", info.get("authorized_keys"))
    pids = [p for p in sessions_with_key(info["fingerprint"], _sshd_log() if log_text is None else log_text)
            if _is_sshd(p)]
    ended = 0
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            ended += 1
        except (ProcessLookupError, PermissionError):
            logger.info("could not end ssh session %s", pid)
    return {"key_removed": removed, "sessions_cut": ended}
