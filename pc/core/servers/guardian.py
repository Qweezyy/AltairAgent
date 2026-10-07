#!/usr/bin/env python3
"""altair-guardian: keeps the agent on a server alive, and gives it a way back.

A small process of its own (a systemd service next to the agent's), run by the system's python3
with nothing but the standard library: an update that breaks the agent cannot break this. The
installer puts it at <install dir>/guardian.py. Every 15 s it:

  * confirms or rolls back an update: after a switch to a new release the agent must answer for
    a while; if it has not by the deadline, `current` goes back to the release before and the
    agent is restarted;
  * rolls back a system change nobody confirmed (core/tools/builtin/system_change_tools.py): a
    change to sshd, the firewall or the network is applied with its undo armed here, and only a
    fresh SSH login from the PC disarms it — a change that cut the way in undoes itself;
  * restarts an agent that is up but no longer answers (a crash is systemd's: Restart=always);
  * frees disk when it runs low: caches, old logs, old releases — never the user's data.

Everything it does goes to <data>/guardian/events.jsonl; the agent copies it into its Journal.

    python3 guardian.py watch        the loop (the systemd service)
    python3 guardian.py rollback     go back to the release before, now (the installer uses it)
    python3 guardian.py status       what it is watching, as JSON
    python3 guardian.py confirm ID   disarm a pending system change (the PC, over a new login)
    python3 guardian.py undo ID      undo a pending system change now (its own check failed)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

TICK_S = 15.0
#: An update must answer this many checks in a row to be kept.
UPDATE_OK_CHECKS = 3
#: Answering, then not: a hang. Restart after this many failed checks in a row…
HANG_CHECKS = 8
#: …but not more often than this.
RESTART_EVERY_S = 600.0
#: Below this much free disk, clean up (at most once an hour).
LOW_DISK_MB = 1024
CLEAN_EVERY_S = 3600.0
#: Releases kept besides the current and the previous one.
KEEP_RELEASES = 3
SERVICE = "altair"


class Ops:
    """What the guardian does to the machine; the tests give it a scripted one."""

    def __init__(self, install_dir: Path, data_dir: Path, port: int) -> None:
        self.install_dir, self.data_dir, self.port = install_dir, data_dir, port

    def now(self) -> float:
        return time.time()

    def healthy(self) -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/health", timeout=5) as r:
                return r.status == 200 and json.loads(r.read().decode("utf-8")).get("status") == "ok"
        except (OSError, ValueError):
            return False

    def current(self) -> str:
        link = self.install_dir / "current"
        return os.path.realpath(link) if link.exists() or link.is_symlink() else ""

    def switch(self, release: str) -> None:
        tmp = self.install_dir / ".current.tmp"
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        os.symlink(release, tmp)
        os.replace(tmp, self.install_dir / "current")

    def restart(self) -> None:
        subprocess.run(["systemctl", "restart", SERVICE], check=False, timeout=60)

    def shell(self, command: str, timeout: float = 120) -> tuple[int, str]:
        r = subprocess.run(["sh", "-c", command], capture_output=True, text=True, timeout=timeout, check=False)
        return r.returncode, (r.stdout + r.stderr)[-2000:]

    def free_mb(self) -> int:
        return shutil.disk_usage(self.data_dir).free // (1024 * 1024)


class Guardian:
    def __init__(self, ops: Ops) -> None:
        self.ops = ops
        self.dir = ops.data_dir / "guardian"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.dir / "state.json"
        self.state = self._read(self.state_file, {"fails": 0, "last_restart": 0.0, "last_clean": 0.0, "update_ok": 0})

    # ---------------------------------------------------------------- storage

    @staticmethod
    def _read(path: Path, default: dict) -> dict:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else dict(default)
        except (OSError, ValueError):
            return dict(default)

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def event(self, kind: str, **data: object) -> None:
        line = json.dumps({"ts": self.ops.now(), "kind": kind, **data}, ensure_ascii=False)
        with (self.dir / "events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    # ---------------------------------------------------------------- one check

    def tick(self) -> None:
        healthy = self.ops.healthy()
        self._update_gate(healthy)
        self._changes()
        self._hang(healthy)
        self._disk()
        self._write(self.state_file, self.state)

    def _update_gate(self, healthy: bool) -> None:
        pending_file = self.dir / "pending-update.json"
        pending = self._read(pending_file, {})
        if not pending:
            return
        if healthy:
            self.state["update_ok"] = self.state.get("update_ok", 0) + 1
            if self.state["update_ok"] >= UPDATE_OK_CHECKS:
                pending_file.unlink(missing_ok=True)
                self.state["update_ok"] = 0
                self.event("update.confirmed", release=pending.get("new", ""))
                self._prune_releases()
            return
        self.state["update_ok"] = 0
        if self.ops.now() >= float(pending.get("deadline", 0)):
            self.rollback(reason="the new release did not answer in time")

    def rollback(self, reason: str = "asked") -> bool:
        """Back to the release before the last update (from the pending update, or the newest
        other release). True when it switched."""
        pending_file = self.dir / "pending-update.json"
        pending = self._read(pending_file, {})
        target = str(pending.get("previous") or "")
        current = self.ops.current()
        if not target or not Path(target).is_dir():
            older = [str(p) for p in self._releases() if os.path.realpath(p) != current]
            target = older[-1] if older else ""
        if not target:
            self.event("update.rollback_impossible", reason=reason, current=current)
            return False
        self.ops.switch(target)
        self.ops.restart()
        pending_file.unlink(missing_ok=True)
        self.state["update_ok"] = 0
        self.event("update.rolled_back", reason=reason, to=target, broken=current)
        return True

    def _hang(self, healthy: bool) -> None:
        if healthy or (self.dir / "pending-update.json").exists():
            self.state["fails"] = 0
            return
        self.state["fails"] = self.state.get("fails", 0) + 1
        now = self.ops.now()
        if self.state["fails"] >= HANG_CHECKS and now - float(self.state.get("last_restart", 0)) >= RESTART_EVERY_S:
            self.ops.restart()
            self.state["last_restart"], self.state["fails"] = now, 0
            self.event("agent.restarted", reason=f"no answer for {HANG_CHECKS} checks")

    # ---------------------------------------------------------------- system changes

    def _changes(self) -> None:
        folder = self.dir / "changes"
        if not folder.is_dir():
            return
        for path in sorted(folder.glob("*.json")):
            change = self._read(path, {})
            cid = path.stem
            if (folder / f"{cid}.confirmed").exists():
                path.unlink(missing_ok=True)
                (folder / f"{cid}.confirmed").unlink(missing_ok=True)
                shutil.rmtree(folder / cid, ignore_errors=True)      # the copies are not needed now
                self.event("change.confirmed", id=cid, title=change.get("title", ""))
            elif self.ops.now() >= float(change.get("deadline", 0)):
                self.undo_change(cid, change, reason="not confirmed in time")
                path.unlink(missing_ok=True)
                shutil.rmtree(folder / cid, ignore_errors=True)

    def undo_change(self, cid: str, change: dict, reason: str) -> None:
        restored = []
        for target, backup in (change.get("backups") or {}).items():
            try:
                if backup:
                    shutil.copy2(backup, target)
                else:
                    Path(target).unlink(missing_ok=True)     # the change created it
                restored.append(target)
            except OSError as exc:
                self.event("change.restore_failed", id=cid, file=target, error=str(exc))
        outputs = []
        for command in change.get("rollback") or []:
            code, out = self.ops.shell(command)
            outputs.append({"command": command, "code": code, "out": out[-500:]})
        self.event("change.rolled_back", id=cid, title=change.get("title", ""), reason=reason,
                   restored=restored, commands=outputs)

    # ---------------------------------------------------------------- disk

    def _releases(self) -> list[Path]:
        folder = self.ops.install_dir / "releases"
        if not folder.is_dir():
            return []
        return sorted((p for p in folder.iterdir() if p.is_dir() and not p.name.startswith(".")),
                      key=lambda p: p.stat().st_mtime)

    def _prune_releases(self) -> list[str]:
        """Old releases go; the current one, the one before it and KEEP_RELEASES more stay."""
        current = self.ops.current()
        pending = self._read(self.dir / "pending-update.json", {})
        keep = {current, os.path.realpath(str(pending.get("previous") or "")) if pending else ""}
        others = [p for p in self._releases() if os.path.realpath(p) not in keep]
        removed = []
        for p in others[: max(0, len(others) - KEEP_RELEASES)]:
            shutil.rmtree(p, ignore_errors=True)
            removed.append(p.name)
        return removed

    def _disk(self) -> None:
        now = self.ops.now()
        if self.ops.free_mb() >= LOW_DISK_MB or now - float(self.state.get("last_clean", 0)) < CLEAN_EVERY_S:
            return
        before = self.ops.free_mb()
        self.state["last_clean"] = now
        removed = self._prune_releases()
        logs = self.ops.data_dir / "logs"
        old_logs = 0
        if logs.is_dir():
            for f in logs.glob("*.log*"):
                try:
                    if now - f.stat().st_mtime > 7 * 86400:
                        f.unlink()
                        old_logs += 1
                except OSError:
                    continue
        for command in ("journalctl --vacuum-size=200M", "apt-get clean", "rm -f /tmp/altair-move-*.tar.gz"):
            self.ops.shell(command)
        self.event("disk.cleaned", freed_mb=self.ops.free_mb() - before, releases=removed, old_logs=old_logs,
                   free_mb=self.ops.free_mb())

    # ---------------------------------------------------------------- status

    def status(self) -> dict:
        changes = sorted(p.stem for p in (self.dir / "changes").glob("*.json")) if (self.dir / "changes").is_dir() else []
        return {"current": self.ops.current(), "pending_update": self._read(self.dir / "pending-update.json", {}),
                "pending_changes": changes, "releases": [p.name for p in self._releases()], **self.state}


def _ops_from_env() -> Ops:
    install = Path(os.environ.get("ALTAIR_INSTALL_DIR") or Path(__file__).resolve().parent)
    data = Path(os.environ.get("APP_PATH") or "/var/lib/altair")
    return Ops(install, data, int(os.environ.get("ALTAIR_PORT") or 8137))


def main(argv: list[str]) -> int:
    guardian = Guardian(_ops_from_env())
    command = argv[1] if len(argv) > 1 else "watch"
    if command == "rollback":
        return 0 if guardian.rollback(reason=" ".join(argv[2:]) or "asked") else 1
    if command == "status":
        print(json.dumps(guardian.status(), indent=1))
        return 0
    if command == "confirm" and len(argv) > 2:
        cid = "".join(c for c in argv[2] if c.isalnum() or c in "-_")
        (guardian.dir / "changes").mkdir(exist_ok=True)
        if not (guardian.dir / "changes" / f"{cid}.json").exists():
            print(json.dumps({"confirmed": False, "why": "no such pending change (already rolled back?)"}))
            return 1
        (guardian.dir / "changes" / f"{cid}.confirmed").write_text("ok", encoding="utf-8")
        print(json.dumps({"confirmed": True}))
        return 0
    if command == "undo" and len(argv) > 2:
        cid = "".join(c for c in argv[2] if c.isalnum() or c in "-_")
        path = guardian.dir / "changes" / f"{cid}.json"
        if not path.exists():
            print(json.dumps({"undone": False, "why": "no such pending change"}))
            return 1
        guardian.undo_change(cid, guardian._read(path, {}), reason=" ".join(argv[3:]) or "asked")
        path.unlink(missing_ok=True)
        shutil.rmtree(guardian.dir / "changes" / cid, ignore_errors=True)
        print(json.dumps({"undone": True}))
        return 0
    if command != "watch":
        print(__doc__)
        return 2
    # Where this script is: the agent's safe_system_change calls it (confirm / undo).
    (guardian.dir / "script.path").write_text(str(Path(__file__).resolve()), encoding="utf-8")
    guardian.event("guardian.started", current=guardian.ops.current())
    while True:
        try:
            guardian.tick()
        except Exception as exc:  # noqa: BLE001 - the watcher itself must keep watching
            guardian.event("guardian.error", error=f"{type(exc).__name__}: {exc}")
        time.sleep(TICK_S)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
