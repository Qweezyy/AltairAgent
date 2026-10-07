"""Memory and skills shared by the bodies: what one learns, the others know.

A three-way sync the PC runs with every server it reaches (server/bodies.py): this side's files,
the server's files, and the state both agreed on last time (kept here per server). Against that
base each difference has one reading:
  * changed (or added) on one side only → it goes to the other;
  * deleted on one side, untouched on the other → deleted on the other too (no "resurrection");
  * deleted on one side, edited on the other → the edit is kept (nothing the agent learned is lost);
  * edited on both → the newer one wins, and the Journal says so.

Areas: the global memory notes (`<data>/memory/*.md`; the index is rebuilt from them, never
synced) and the skills (`<app>/skills/**`). A project's own memory travels with its folder.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.fs_atomic import atomic_write_text
from core.logging_setup import get_logger

logger = get_logger("bodies_sync")

AREAS = ("memory", "skills")
_SKIP_NAMES = {"MEMORY.md", ".DS_Store", "Thumbs.db"}
_SKIP_DIRS = {"__pycache__", ".git", ".trash"}


def area_root(settings: Any, area: str) -> Path:
    if area == "memory":
        return Path(settings.data_dir) / "memory"
    if area == "skills":
        return Path(settings.skills_dir)
    raise ValueError(f"unknown area '{area}'")


def safe_rel(path: str) -> str:
    """A relative path inside the area, with '/' — or ValueError for anything that leaves it."""
    raw = str(path).replace("\\", "/")
    rel = raw.strip("/")
    parts = rel.split("/")
    if not rel or raw.startswith("/") or any(p in ("", ".", "..") for p in parts) or ":" in parts[0]:
        raise ValueError(f"not a path inside the area: {path!r}")
    return rel


def manifest(root: Path, area: str) -> dict[str, dict[str, Any]]:
    """{relative path: {sha, mtime}} of the files that are synced."""
    out: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return out
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if name in _SKIP_NAMES or name.endswith((".tmp", ".lock")):
                continue
            path = Path(base) / name
            rel = str(path.relative_to(root)).replace("\\", "/")
            if area == "memory" and ("/" in rel or not rel.endswith(".md")):
                continue
            try:
                data = path.read_bytes()
                out[rel] = {"sha": hashlib.sha256(data).hexdigest(), "mtime": path.stat().st_mtime}
            except OSError as exc:
                logger.info("sync: %s skipped (%s)", path, exc)
    return out


def read_files(root: Path, paths: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in paths:
        rel = safe_rel(p)
        try:
            out[rel] = base64.b64encode((root / rel).read_bytes()).decode("ascii")
        except OSError as exc:
            logger.info("sync: %s not read (%s)", rel, exc)
    return out


def apply_files(root: Path, area: str, put: dict[str, str], delete: list[str]) -> dict[str, int]:
    """Writes and deletes inside the area; rebuilds the memory index after."""
    written = removed = 0
    for p, b64 in put.items():
        rel = safe_rel(p)
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        # The bytes as they are (text mode would turn CRLF into CR CR LF on Windows), through a
        # temp file so a reader never sees half a note.
        tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_bytes(base64.b64decode(b64))
        os.replace(tmp, target)
        written += 1
    for p in delete:
        rel = safe_rel(p)
        try:
            (root / rel).unlink()
            removed += 1
        except FileNotFoundError:
            continue
        # A skill deleted on the other body leaves no empty folder behind here.
        parent = (root / rel).parent
        while parent != root and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    if area == "memory" and (written or removed):
        from core.memory import MemoryDir

        MemoryDir(root)._write_index()
    return {"written": written, "removed": removed}


@dataclass
class Plan:
    """What one sync does, per side."""

    to_local: list[str] = field(default_factory=list)       # copy server → here
    to_remote: list[str] = field(default_factory=list)      # copy here → server
    delete_local: list[str] = field(default_factory=list)
    delete_remote: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.to_local or self.to_remote or self.delete_local or self.delete_remote)


def plan(local: dict[str, dict], remote: dict[str, dict], base: dict[str, str]) -> Plan:
    """The three-way decision for every path (see the module's docstring)."""
    out = Plan()
    for path in sorted(set(local) | set(remote) | set(base)):
        l_sha = (local.get(path) or {}).get("sha")
        r_sha = (remote.get(path) or {}).get("sha")
        b_sha = base.get(path)
        if l_sha == r_sha:
            continue
        local_changed, remote_changed = l_sha != b_sha, r_sha != b_sha
        if local_changed and not remote_changed:
            (out.to_remote if l_sha else out.delete_remote).append(path)
        elif remote_changed and not local_changed:
            (out.to_local if r_sha else out.delete_local).append(path)
        elif l_sha is None:                     # deleted here, edited there: keep the edit
            out.to_local.append(path)
        elif r_sha is None:                     # deleted there, edited here: keep the edit
            out.to_remote.append(path)
        else:                                   # edited on both: the newer one
            out.conflicts.append(path)
            newer_here = (local[path].get("mtime") or 0) >= (remote[path].get("mtime") or 0)
            (out.to_remote if newer_here else out.to_local).append(path)
    return out


Fetch = Callable[[str, str, dict | None], Awaitable[dict]]


async def sync_area(settings: Any, area: str, base_file: Path, call: Fetch) -> dict[str, Any]:
    """Syncs one area with one server. `call(method, path, json)` talks to the server's API."""
    import asyncio

    root = area_root(settings, area)
    bases = _load_base(base_file)
    base = bases.get(area, {})
    local = await asyncio.to_thread(manifest, root, area)
    remote = (await call("GET", f"/api/sync/manifest?area={area}", None)).get("files", {})
    p = plan(local, remote, base)
    if not p.empty:
        if p.to_local:
            got = (await call("POST", "/api/sync/read", {"area": area, "paths": p.to_local})).get("files", {})
        else:
            got = {}
        if got or p.delete_local:
            await asyncio.to_thread(apply_files, root, area, got, p.delete_local)
        if p.to_remote or p.delete_remote:
            files = await asyncio.to_thread(read_files, root, p.to_remote)
            await call("POST", "/api/sync/apply", {"area": area, "put": files, "delete": p.delete_remote})
        local = await asyncio.to_thread(manifest, root, area)
    # What both have now is the base of the next time.
    bases[area] = {k: v["sha"] for k, v in local.items()}
    base_file.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(base_file, json.dumps(bases, indent=1))
    return {"area": area, "to_here": len(p.to_local), "to_there": len(p.to_remote),
            "deleted_here": len(p.delete_local), "deleted_there": len(p.delete_remote), "conflicts": p.conflicts}


def _load_base(path: Path) -> dict[str, dict[str, str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("sync base %s unreadable (%s): starting from scratch", path, exc)
        return {}
