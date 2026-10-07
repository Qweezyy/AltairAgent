"""safe_system_change (0.3.0 stage 4): a change that could cut the way in is armed with its undo
before it runs, undone at once when it fails, and kept only after a new SSH login from the PC.

The shell is scripted (it really edits the files); the undo runs the real guardian script."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import core.bodies_routing as routing
import core.servers.guardian as guardian_module
import core.tools.builtin.system_change_tools as sct
from core.security.approval import always_allow
from core.servers.registry import ServerRecord
from core.tools.base import ToolContext


@pytest.fixture()
def server(tmp_path, settings, monkeypatch):
    """A Linux server with the guardian: its script, its folder; the shell edits real files."""
    monkeypatch.setattr(sct.platform, "system", lambda: "Linux")
    folder = settings.app_dir / "guardian"         # APP_PATH, like the guardian (found live)
    folder.mkdir(parents=True)
    (folder / "script.path").write_text(guardian_module.__file__, encoding="utf-8")
    config = tmp_path / "sshd_config"
    config.write_text("Port 22\n", encoding="utf-8")
    ran: list[str] = []
    armed_before_apply: list[bool] = []

    async def fake_sh(command: str, timeout: float = 300):
        ran.append(command)
        if command.startswith("python3 "):              # the guardian, for real
            env = {**os.environ, "APP_PATH": str(settings.app_dir)}
            args = command.split(" ", 1)[1]
            import shlex

            out = subprocess.run([sys.executable, *shlex.split(args)], capture_output=True, text=True, env=env, timeout=60)
            return out.returncode, out.stdout + out.stderr
        if command.startswith("set-port "):
            armed_before_apply.append(any((folder / "changes").glob("*.json")))
            config.write_text(f"Port {command.split()[1]}\n", encoding="utf-8")
            return 0, ""
        if command == "sshd -t":
            return (0, "") if "Port 22" in config.read_text(encoding="utf-8") or "2222" in config.read_text(encoding="utf-8") else (255, "bad config")
        if command == "fail":
            return 1, "boom"
        return 0, ""

    monkeypatch.setattr(sct, "_sh", fake_sh)

    class S:
        pass

    s = S()
    s.config, s.folder, s.ran, s.armed = config, folder, ran, armed_before_apply
    return s


def _ctx(settings):
    settings.approval_mode = "bypass"
    return ToolContext(settings=settings, approver=always_allow)


async def test_a_change_is_armed_first_and_waits_for_a_new_login(settings, server):
    out = await sct.SafeSystemChangeTool().invoke(
        {"title": "ssh on 2222", "apply": ["set-port 2222"], "files": [str(server.config)],
         "rollback": ["systemctl reload ssh"], "check": ["sshd -t"]}, _ctx(settings))
    assert out.ok, out.content
    assert server.armed == [True]                                  # the undo existed before the change ran
    assert server.config.read_text(encoding="utf-8") == "Port 2222\n"
    assert out.metadata["needs_confirmation"] and "NOT kept yet" in out.content
    change = json.loads((server.folder / "changes" / f"{out.metadata['change_id']}.json").read_text(encoding="utf-8"))
    assert change["rollback"] == ["systemctl reload ssh"] and change["deadline"] > change["created"] + 100
    backup = Path(change["backups"][str(server.config)])
    assert backup.read_text(encoding="utf-8") == "Port 22\n"


@pytest.mark.parametrize("apply, check, why", [
    (["set-port 2222", "fail"], [], "'fail' failed"),
    (["set-port 9999"], ["sshd -t"], "the check 'sshd -t' failed"),
])
async def test_a_failing_step_or_check_undoes_it_at_once(settings, server, apply, check, why):
    out = await sct.SafeSystemChangeTool().invoke(
        {"title": "t", "apply": apply, "files": [str(server.config)], "check": check}, _ctx(settings))
    assert not out.ok and why in out.content and "undone" in out.content
    assert server.config.read_text(encoding="utf-8") == "Port 22\n"      # the file is back
    assert not list((server.folder / "changes").glob("*.json"))           # nothing left armed
    events = [json.loads(x)["kind"] for x in (server.folder / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events == ["change.rolled_back"]


async def test_without_a_guardian_or_off_linux_it_refuses(settings, monkeypatch):
    monkeypatch.setattr(sct.platform, "system", lambda: "Linux")
    out = await sct.SafeSystemChangeTool().invoke({"title": "t", "apply": ["x"]}, _ctx(settings))
    assert not out.ok and "No guardian" in out.content
    monkeypatch.setattr(sct.platform, "system", lambda: "Windows")
    out = await sct.SafeSystemChangeTool().invoke({"title": "t", "apply": ["x"]}, _ctx(settings))
    assert not out.ok and "Linux servers" in out.content


def test_it_always_asks_unless_the_owner_chose_no_confirmations():
    tool = sct.SafeSystemChangeTool()
    args = sct.SafeChangeArgs(title="t", apply=["x"])
    assert tool.auto_verdict(args, ToolContext()) == "ask" and tool.category == "execute"


# ------------------------------------------------------------------ the PC confirms with a new login


class _Tunnel:
    def __init__(self) -> None:
        self.record = ServerRecord(id="srv1", name="test-vps", host="203.0.113.7", port=22, user="root",
                                   mode="owner", data_dir="/var/lib/altair")
        self.state, self.local_port, self.error, self.agent = "online", 1, "", "ok"


@pytest.fixture()
def pc_side(monkeypatch, settings):
    from core.tools.base import ToolResult

    tunnel = _Tunnel()

    class Tunnels:
        def all(self):
            return [tunnel]

    routing.set_tunnels(Tunnels(), settings.data_dir)
    monkeypatch.setattr(routing, "CONFIRM_PAUSE_S", 0.0)

    async def remote_applied(*a, **k):
        return ToolResult(content="Applied: x.", metadata={"change_id": "c1", "needs_confirmation": True,
                                                           "guardian": "/opt/altair/guardian.py"})

    monkeypatch.setattr(routing, "call_remote", remote_applied)
    logins: list = []
    yield tunnel, logins
    routing.set_tunnels(None)


def _fake_ssh(monkeypatch, logins, *, refuse_times=0, answer=""):
    import core.servers.remote as remote_module
    from core.servers.remote import RemoteError, RunResult

    class Remote:
        def __init__(self, login):
            self.login, self.sudo = login, ""

        async def __aenter__(self):
            logins.append(self.login)
            if len(logins) <= refuse_times:
                raise RemoteError("Connection refused")
            return self

        async def __aexit__(self, *exc):
            return None

        async def run(self, command, **kw):
            logins.append(command)
            if answer:
                return RunResult(1, answer, "")
            return RunResult(0, '{"confirmed": true}', "")

    monkeypatch.setattr(remote_module, "SSHRemote", Remote)


async def _call(settings):
    from core.tools import build_default_registry

    settings.approval_mode = "bypass"
    return await build_default_registry().get("safe_system_change").invoke(
        {"title": "t", "apply": ["x"], "body": "test-vps"}, ToolContext(settings=settings, approver=always_allow))


async def test_the_pc_confirms_with_a_new_login(settings, pc_side, monkeypatch):
    tunnel, logins = pc_side
    _fake_ssh(monkeypatch, logins, refuse_times=1)                 # sshd still reloading at first
    out = await _call(settings)
    assert out.ok and out.content.startswith("Kept:") and out.metadata["confirmed"] is True
    commands = [x for x in logins if isinstance(x, str)]
    assert commands == ["APP_PATH=/var/lib/altair python3 /opt/altair/guardian.py confirm c1"]
    assert all(x.key_path and not x.password for x in logins if not isinstance(x, str))


async def test_no_new_login_means_it_is_not_kept(settings, pc_side, monkeypatch):
    tunnel, logins = pc_side
    _fake_ssh(monkeypatch, logins, refuse_times=99)
    out = await _call(settings)
    assert not out.ok and out.content.startswith("NOT kept") and "Connection refused" in out.content
    assert len(logins) == routing.CONFIRM_TRIES


async def test_a_change_already_undone_is_not_confirmed_again(settings, pc_side, monkeypatch):
    tunnel, logins = pc_side
    _fake_ssh(monkeypatch, logins, answer='{"confirmed": false, "why": "no such pending change"}')
    out = await _call(settings)
    assert not out.ok and len([x for x in logins if isinstance(x, str)]) == 1
