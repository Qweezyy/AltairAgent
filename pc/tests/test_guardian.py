"""The server guardian (core/servers/guardian.py): a bad update and an unconfirmed system change
undo themselves, a hung agent is restarted, low disk is cleaned — and it runs as a plain script.

The machine is scripted (FakeOps over temp folders); the decisions are the real code."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import core.servers.guardian as g


class FakeOps(g.Ops):
    def __init__(self, root: Path) -> None:
        super().__init__(root / "opt", root / "data", 8137)
        (root / "opt" / "releases").mkdir(parents=True)
        (root / "data").mkdir()
        self.clock = 1_000_000.0
        self.up = True
        self.pointer = ""
        self.restarts = 0
        self.commands: list[str] = []
        self.free = 50_000

    def now(self) -> float:
        return self.clock

    def healthy(self) -> bool:
        return self.up

    def current(self) -> str:
        return self.pointer

    def switch(self, release: str) -> None:
        self.pointer = os.path.realpath(release)

    def restart(self) -> None:
        self.restarts += 1

    def shell(self, command: str, timeout: float = 120) -> tuple[int, str]:
        self.commands.append(command)
        return 0, ""

    def free_mb(self) -> int:
        return self.free

    def release(self, name: str, age: float = 0) -> str:
        path = self.install_dir / "releases" / name
        path.mkdir()
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
        return os.path.realpath(path)


def _events(guard: g.Guardian) -> list[dict]:
    path = guard.dir / "events.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _pending(guard: g.Guardian, previous: str, new: str, deadline: float) -> None:
    (guard.dir / "pending-update.json").write_text(
        json.dumps({"previous": previous, "new": new, "deadline": deadline}), encoding="utf-8")


def test_an_update_that_answers_is_kept_and_old_releases_go(tmp_path):
    ops = FakeOps(tmp_path)
    old = [ops.release(f"0.2.{i}", age=100 - i) for i in range(6)]
    new = ops.release("0.3.0", age=0)
    ops.pointer = new
    guard = g.Guardian(ops)
    _pending(guard, previous=old[-1], new=new, deadline=ops.clock + 300)
    for _ in range(g.UPDATE_OK_CHECKS):
        guard.tick()
    assert not (guard.dir / "pending-update.json").exists()
    assert [e["kind"] for e in _events(guard)] == ["update.confirmed"]
    left = sorted(p.name for p in (ops.install_dir / "releases").iterdir())
    assert "0.3.0" in left and len(left) == 1 + g.KEEP_RELEASES       # current + the newest others
    assert ops.restarts == 0


def test_an_update_that_does_not_answer_goes_back_at_the_deadline(tmp_path):
    ops = FakeOps(tmp_path)
    previous, new = ops.release("0.2.1", age=50), ops.release("0.3.0")
    ops.pointer, ops.up = new, False
    guard = g.Guardian(ops)
    _pending(guard, previous=previous, new=new, deadline=ops.clock + 300)
    guard.tick()
    assert ops.pointer == new and ops.restarts == 0                    # still waiting
    ops.up = True
    guard.tick()                                                       # answers once…
    ops.up = False
    ops.clock += 301
    guard.tick()                                                       # …then not, past the deadline
    assert ops.pointer == previous and ops.restarts == 1
    rolled = _events(guard)[-1]
    assert rolled["kind"] == "update.rolled_back" and rolled["broken"] == new and rolled["to"] == previous
    assert not (guard.dir / "pending-update.json").exists()


def test_a_rollback_asked_for_without_a_pending_update_takes_the_newest_other_release(tmp_path):
    ops = FakeOps(tmp_path)
    ops.release("0.1.0", age=90)
    newer = ops.release("0.2.0", age=60)
    ops.pointer = ops.release("0.3.0")
    guard = g.Guardian(ops)
    assert guard.rollback("asked") and ops.pointer == newer
    lone = FakeOps(tmp_path / "lone")
    lone.pointer = lone.release("0.3.0")
    assert not g.Guardian(lone).rollback("asked")                      # nowhere to go: says so
    assert _events(g.Guardian(lone))[-1]["kind"] == "update.rollback_impossible"


def test_a_hung_agent_is_restarted_but_not_in_a_loop(tmp_path):
    ops = FakeOps(tmp_path)
    ops.pointer = ops.release("0.3.0")
    guard = g.Guardian(ops)
    ops.up = False
    for _ in range(g.HANG_CHECKS - 1):
        guard.tick()
    assert ops.restarts == 0
    guard.tick()
    assert ops.restarts == 1 and _events(guard)[-1]["kind"] == "agent.restarted"
    for _ in range(g.HANG_CHECKS * 2):
        ops.clock += 15
        guard.tick()
    assert ops.restarts == 1                                           # not again within 10 minutes
    ops.clock += g.RESTART_EVERY_S
    for _ in range(g.HANG_CHECKS):
        guard.tick()
    assert ops.restarts == 2


def test_a_system_change_nobody_confirmed_is_undone(tmp_path):
    ops = FakeOps(tmp_path)
    guard = g.Guardian(ops)
    target = tmp_path / "sshd_config"
    target.write_text("Port 2222\n", encoding="utf-8")                 # the change, applied
    backup = tmp_path / "sshd_config.bak"
    backup.write_text("Port 22\n", encoding="utf-8")
    created = tmp_path / "new.conf"
    created.write_text("x", encoding="utf-8")
    (guard.dir / "changes").mkdir()
    (guard.dir / "changes" / "c1.json").write_text(json.dumps({
        "title": "move ssh to 2222", "deadline": ops.clock + 120,
        "backups": {str(target): str(backup), str(created): ""}, "rollback": ["systemctl reload ssh"]}), encoding="utf-8")
    guard.tick()
    assert target.read_text(encoding="utf-8") == "Port 2222\n"       # still within its two minutes
    ops.clock += 121
    guard.tick()
    assert target.read_text(encoding="utf-8") == "Port 22\n" and not created.exists()
    assert ops.commands == ["systemctl reload ssh"]
    assert _events(guard)[-1]["kind"] == "change.rolled_back" and not (guard.dir / "changes" / "c1.json").exists()


def test_a_confirmed_system_change_stays(tmp_path, monkeypatch):
    ops = FakeOps(tmp_path)
    guard = g.Guardian(ops)
    (guard.dir / "changes").mkdir()
    (guard.dir / "changes" / "c2.json").write_text(json.dumps({"title": "t", "deadline": ops.clock + 120,
                                                               "backups": {}, "rollback": ["echo undo"]}), encoding="utf-8")
    monkeypatch.setattr(g, "_ops_from_env", lambda: ops)
    assert g.main(["guardian.py", "confirm", "c2"]) == 0
    assert g.main(["guardian.py", "confirm", "nope"]) == 1            # nothing to confirm
    ops.clock += 500
    guard.tick()
    assert ops.commands == [] and _events(guard)[-1]["kind"] == "change.confirmed"


def test_low_disk_is_cleaned_once_an_hour_and_never_the_data(tmp_path):
    ops = FakeOps(tmp_path)
    for i in range(7):
        ops.release(f"0.1.{i}", age=200 - i)
    ops.pointer = ops.release("0.3.0")
    logs = ops.data_dir / "logs"
    logs.mkdir()
    old_log, new_log = logs / "agent.log.3", logs / "agent.log"
    old_log.write_text("x", encoding="utf-8")
    new_log.write_text("y", encoding="utf-8")
    os.utime(old_log, (ops.clock - 8 * 86400, ops.clock - 8 * 86400))
    os.utime(new_log, (ops.clock, ops.clock))
    notes = ops.data_dir / "memory"
    notes.mkdir()
    (notes / "note.md").write_text("keep", encoding="utf-8")
    guard = g.Guardian(ops)
    ops.free = 500
    guard.tick()
    assert not old_log.exists() and new_log.exists() and (notes / "note.md").exists()
    assert len(list((ops.install_dir / "releases").iterdir())) == 1 + g.KEEP_RELEASES
    assert any("journalctl --vacuum" in c for c in ops.commands)
    assert _events(guard)[-1]["kind"] == "disk.cleaned"
    ops.commands.clear()
    ops.clock += 60
    guard.tick()
    assert ops.commands == []                                          # not again within the hour


def test_the_guardian_needs_nothing_but_python(tmp_path):
    """It must run with the server's python3 even when the agent's own install is broken."""
    source = Path(g.__file__).read_text(encoding="utf-8")
    imported = {n.names[0].name.split(".")[0] for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Import)}
    imported |= {n.module.split(".")[0] for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ImportFrom) and n.module}
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}
    env = {**os.environ, "APP_PATH": str(tmp_path / "data"), "ALTAIR_INSTALL_DIR": str(tmp_path / "opt")}
    out = subprocess.run([sys.executable, "-I", g.__file__, "status"], capture_output=True, text=True, env=env,
                         timeout=30, cwd=tmp_path)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["pending_changes"] == []


def test_the_build_ships_the_guardian_as_a_file():
    build = (Path(__file__).resolve().parents[1] / "build_app.py").read_text(encoding="utf-8")
    assert "'guardian.py'" in build and "core/servers" in build


def test_the_guardians_events_reach_the_journal_once(tmp_path, settings):
    from core.journal import get_journal
    from server.bodies import ingest_guardian_events

    folder = settings.app_dir / "guardian"
    folder.mkdir(parents=True)
    events = folder / "events.jsonl"
    events.write_text(json.dumps({"ts": 1, "kind": "update.rolled_back", "to": "/opt/altair/releases/a"}) + "\n"
                      + '{"ts": 2, "kind": "agent.res', encoding="utf-8")        # the last line is half-written
    assert ingest_guardian_events(settings) == 1
    assert ingest_guardian_events(settings) == 0                                  # not twice
    with events.open("a", encoding="utf-8") as fh:
        fh.write('tarted"}\n')
    assert ingest_guardian_events(settings) == 1
    kinds = [r["kind"] for r in get_journal(settings.data_dir / "journal").read(limit=10)]
    assert kinds == ["guardian.agent.restarted", "guardian.update.rolled_back"]


def test_the_agent_and_the_guardian_meet_in_the_same_folder(settings, monkeypatch):
    """Found live: the guardian works in APP_PATH/guardian, the agent looked in its data folder
    under it — safe_system_change said "no guardian" on a server that had one."""
    import core.tools.builtin.system_change_tools as sct

    monkeypatch.setenv("APP_PATH", str(settings.app_dir))
    guard = g.Guardian(g._ops_from_env())
    assert guard.dir == settings.app_dir / "guardian"
    (guard.dir / "script.path").write_text(g.__file__, encoding="utf-8")
    folder, script = sct._guardian(settings.app_dir)
    assert folder == guard.dir and script == g.__file__
