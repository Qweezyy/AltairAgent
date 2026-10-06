"""Installing the agent on a server (core/servers): the installer against a scripted server.

The fake server answers the installer's commands the way Ubuntu would and records what it was
sent: the key added to authorized_keys, the settings file, the service, the cards exchanged.
Checked: the whole install in order; the password is used once and stored nowhere; the second
connection uses the PC's key and the pinned host key; the server's settings carry the model
settings but none of this PC's paths or ports; a checksum mismatch, an unusable server or a
release too old to be a body stop the install with the step and the reason; uninstalling removes
the service, the files, the PC's key and the trust.
"""

from __future__ import annotations

import json
import shlex
import shutil
import re
from pathlib import Path

import pytest

import core.servers.install as install_module
from core.bodies import Identity, TrustStore
from core.servers.install import InstallError, Installer, InstallRequest, ReleaseRef, server_env, Layout, uninstall
from core.servers.preflight import parse
from core.servers.registry import ServerStore
from core.servers.remote import RunResult

GOOD_PROBE = """arch=x86_64
system=Ubuntu 22.04.5 LTS
os_id=ubuntu
cpus=1
mem_mb=1963
disk_mb=26000
uid=0
systemd=yes
docker=no
unzip=no
curl=yes
apt=yes
sudo_nopass=yes
sudo=yes
github=200
ports=22 53
installed=
"""


class FakeServer:
    """What the server holds between connections."""

    def __init__(self, tmp: Path, sha: str = "a" * 64) -> None:
        self.identity = Identity(tmp / "server-identity", "server", "vps")
        self.sha = sha
        self.logins: list = []
        self.commands: list[str] = []
        self.files: dict[str, str] = {}
        self.authorized: list[str] = []
        self.trusted_cards: list[dict] = []
        self.probe = GOOD_PROBE


class FakeRemote:
    """Stands in for SSHRemote: the same interface, answers from the FakeServer."""

    def __init__(self, server: FakeServer, login) -> None:
        self.server, self.login = server, login
        self.host_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeHostKeyForTests"
        self.host_key_fingerprint = "SHA256:fake"
        self.sudo = ""
        self.sudo_password = ""
        server.logins.append(login)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def put(self, local, remote_path, on_progress=None):
        self.server.files[remote_path] = f"<upload {Path(local).name}>"
        if on_progress:
            on_progress(10, 10)

    async def run(self, command: str, *, root: bool = False, timeout: float = 0, stdin: str | None = None) -> RunResult:
        s = self.server
        s.commands.append(command)
        if "echo \"arch=$(uname -m)\"" in command:
            return RunResult(0, s.probe, "")
        if command.startswith("printf %s \"$HOME\""):
            return RunResult(0, "/root", "")
        if "authorized_keys" in command and "grep -qxF" in command:
            s.authorized.append(re.search(r"(ssh-ed25519 \S+ altair-pc-\w+)", command).group(1))
            return RunResult(0, "", "")
        if command.startswith("sha256sum"):
            return RunResult(0, f"{s.sha}  /opt/altair/downloads/pkg.zip\n", "")
        if "cat > " in command:
            s.files[re.search(r"cat > (\S+)", command).group(1)] = stdin or ""
            return RunResult(0, "", "")
        if "base64 -d | tar" in command:
            s.files["<bundle>"] = stdin or ""
            return RunResult(0, "", "")
        if command.endswith("--body-card"):
            return RunResult(0, "some log line\n" + json.dumps(s.identity.card()) + "\n", "")
        if "--trust-body" in command:
            card = json.loads(re.search(r"--trust-body '(.+)'$", command).group(1))
            s.trusted_cards.append(card)
            return RunResult(0, json.dumps({"trusted": card["id"]}), "")
        return RunResult(0, "", "")


def _installer(settings, server: FakeServer, request: InstallRequest, steps: list | None = None) -> Installer:
    return Installer(settings, request, lambda step, detail, pct: steps.append(step) if steps is not None else None,
                     remote_factory=lambda login: FakeRemote(server, login))


@pytest.fixture()
def release(monkeypatch):
    async def fake_release(arch, repo=install_module.REPO):
        assert arch == "x64"
        return ReleaseRef(version="0.3.0", name="Altair-0.3.0-linux-x64.zip", url="https://example/pkg.zip",
                          sha256="a" * 64)

    monkeypatch.setattr(install_module, "signed_release", fake_release)


async def test_a_server_is_installed_step_by_step(settings, tmp_path, release):
    (settings.app_dir / ".env").write_text(
        "LLM_API_KEY=sk-secret-1234\nDEFAULT_MODEL=glm-5.3-flash\nWORKSPACE_PATH=D:\\Projects\nHOST=0.0.0.0\n"
        "BRIDGE_TOKEN=lan\nSKILLS_EXTRA=C:\\x\n", encoding="utf-8")
    server = FakeServer(tmp_path)
    steps: list[str] = []
    record = await _installer(settings, server, InstallRequest(host="203.0.113.7", password="hunter2", name="vps"),
                              steps).run()

    assert [s for s in dict.fromkeys(steps)] == ["connect", "preflight", "key", "packages", "download", "unpack",
                                                 "configure", "identity", "service", "health", "done"]
    # The password is used for the first connection only; later ones use the PC's key and the
    # host key seen the first time.
    first, *later = server.logins
    assert first.password == "hunter2" and not first.key_path
    assert later and all(l.password == "" and l.key_path and l.host_key == FakeRemote(server, first).host_key
                         for l in later)
    assert len(server.authorized) == 1 and server.authorized[0].endswith(f"altair-pc-{record.id}")
    # unzip was missing: installed with apt-get.
    assert any("apt-get" in c and " install " in c and "unzip" in c for c in server.commands)
    # The server's settings: the model settings and key from this PC, none of its paths/ports.
    env = server.files["/var/lib/altair/.env"]
    assert "LLM_API_KEY=sk-secret-1234" in env and "DEFAULT_MODEL=glm-5.3-flash" in env
    assert "HOST=127.0.0.1" in env and "APPROVAL_MODE=bypass" in env and "BODY_KIND=server" in env
    assert "D:\\Projects" not in env and "0.0.0.0" not in env and "BRIDGE_TOKEN" not in env and "C:\\x" not in env
    unit = server.files["/etc/systemd/system/altair.service"]
    assert "Restart=always" in unit and "APP_PATH=/var/lib/altair" in unit and "--host 127.0.0.1" in unit
    # The two bodies trust each other.
    assert TrustStore(settings.data_dir / "identity").get(server.identity.id) is not None
    assert server.trusted_cards and server.trusted_cards[0]["kind"] == "pc"
    # What is remembered here: no password anywhere.
    stored = ServerStore(settings.data_dir).path.read_text(encoding="utf-8")
    assert "hunter2" not in stored and record.body_id == server.identity.id and record.version == "0.3.0"
    assert ServerStore(settings.data_dir).key_path(record.id).exists()


async def test_a_checksum_mismatch_stops_the_install(settings, tmp_path, release):
    server = FakeServer(tmp_path, sha="b" * 64)
    with pytest.raises(InstallError) as err:
        await _installer(settings, server, InstallRequest(host="h", password="p")).run()
    assert err.value.step == "download" and "checksum" in str(err.value)
    assert not any("--body-card" in c for c in server.commands)        # nothing ran after it


async def test_an_unusable_server_is_refused_before_any_change(settings, tmp_path, release):
    server = FakeServer(tmp_path)
    server.probe = GOOD_PROBE.replace("mem_mb=1963", "mem_mb=512").replace("arch=x86_64", "arch=mips")
    with pytest.raises(InstallError) as err:
        await _installer(settings, server, InstallRequest(host="h", password="p")).run()
    assert err.value.step == "preflight" and "mips" in str(err.value) and "512 MB" in str(err.value)
    assert server.authorized == [] and not any("apt-get" in c and " install " in c for c in server.commands)


async def test_a_release_too_old_to_be_a_body_is_refused(settings, tmp_path, monkeypatch):
    async def old(arch, repo=install_module.REPO):
        return ReleaseRef(version="0.2.1", name="Altair-0.2.1-linux-x64.zip", url="u", sha256="a" * 64)

    monkeypatch.setattr(install_module, "signed_release", old)
    with pytest.raises(InstallError) as err:
        await _installer(settings, FakeServer(tmp_path), InstallRequest(host="h", password="p")).run()
    assert err.value.step == "download" and "0.3.0" in str(err.value)


async def test_a_local_package_is_uploaded_and_checked(settings, tmp_path):
    package = tmp_path / "Altair-0.3.0-linux-x64.zip"
    package.write_bytes(b"not really a zip")
    import hashlib

    server = FakeServer(tmp_path, sha=hashlib.sha256(package.read_bytes()).hexdigest())
    record = await _installer(settings, server, InstallRequest(host="h", password="p", source=str(package))).run()
    assert record.version == "0.3.0"
    assert any(path.startswith("/tmp/altair-upload-") for path in server.files)


async def test_uninstall_takes_everything_back(settings, tmp_path, release, monkeypatch):
    server = FakeServer(tmp_path)
    record = await _installer(settings, server, InstallRequest(host="h", password="p")).run()
    monkeypatch.setattr("core.servers.install.SSHRemote", lambda login: FakeRemote(server, login))
    await uninstall(settings, record)
    tail = " ".join(server.commands[-6:])
    assert "disable --now altair" in tail and "rm -rf /opt/altair /var/lib/altair" in tail
    assert f"grep -v altair-pc-{record.id}" in tail
    assert ServerStore(settings.data_dir).get(record.id) is None
    assert TrustStore(settings.data_dir / "identity").get(server.identity.id).revoked


def test_the_preflight_reads_the_server_and_recommends_a_mode():
    bare = parse(GOOD_PROBE)
    assert bare.can_install and bare.package_arch == "x64" and bare.sudo == "root" and bare.missing == ["unzip"]
    assert bare.recommended_mode == "owner"
    busy = parse(GOOD_PROBE.replace("ports=22 53", "ports=22 53 80 443 5432"))
    assert busy.recommended_mode == "autopilot" and "80" in busy.why
    no_rights = parse(GOOD_PROBE.replace("uid=0", "uid=1000").replace("sudo_nopass=yes", "sudo_nopass=no")
                      .replace("sudo=yes", "sudo=no"))
    assert not no_rights.can_install and "sudo" in " ".join(no_rights.problems)


def test_server_settings_keep_the_model_and_drop_the_pc():
    env = server_env("OPENROUTER_API_KEY=k\nWORKSPACE_PATH=C:/Users/me\nPORT=8137\n# comment\nLLM_REASONING=adaptive\n",
                     "careful", Layout())
    assert "OPENROUTER_API_KEY=k" in env and "LLM_REASONING=adaptive" in env
    assert "C:/Users/me" not in env and "APPROVAL_MODE=manual" in env and env.count("PORT=") == 1


async def test_installing_again_reuses_the_record_and_the_key(settings, tmp_path, release):
    """An update or a repair: no second record, no password, the same key, the pinned host key."""
    server = FakeServer(tmp_path)
    first = await _installer(settings, server, InstallRequest(host="h", password="p", name="vps")).run()
    server.logins.clear()
    again = await _installer(settings, server, InstallRequest(host="h")).run()
    assert again.id == first.id and again.name == "vps"
    assert [s.id for s in ServerStore(settings.data_dir).all()] == [first.id]
    assert all(not l.password and l.key_path and l.host_key for l in server.logins)
    assert len(set(server.authorized)) == 1


async def test_apt_waits_for_the_lock_of_a_fresh_server(settings, tmp_path, release):
    """Found live: a fresh Ubuntu runs unattended-upgrades after boot and holds the dpkg lock."""
    server = FakeServer(tmp_path)
    await _installer(settings, server, InstallRequest(host="h", password="p")).run()
    apt = [c for c in server.commands if "DEBIAN_FRONTEND" in c]
    assert apt and all("DPkg::Lock::Timeout" in c for c in apt)


# ------------------------------------------------------------------ the API the window uses


def _api(monkeypatch, settings, server: FakeServer):
    from fastapi.testclient import TestClient

    import core.servers.remote as remote_module
    import core.settings as settings_module
    import server.app as app_module
    import server.ws as ws_module
    from server.app import create_app

    for mod in (settings_module, app_module, ws_module):
        monkeypatch.setattr(mod, "get_settings", lambda: settings)
    fake = lambda login: FakeRemote(server, login)  # noqa: E731
    monkeypatch.setattr(install_module, "SSHRemote", fake)
    monkeypatch.setattr(remote_module, "SSHRemote", fake)
    return TestClient(create_app())


def _wait_job(client, job_id: str) -> dict:
    import time

    for _ in range(200):
        job = client.get(f"/api/servers/jobs/{job_id}").json()
        if job["state"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError(job)


def test_the_api_installs_lists_and_removes_a_server(monkeypatch, settings, tmp_path, release):
    server = FakeServer(tmp_path)
    with _api(monkeypatch, settings, server) as client:
        assert client.get("/api/servers").json() == {"servers": []}
        assert client.post("/api/servers/install", json={"host": ""}).json()["ok"] is False

        pre = client.post("/api/servers/preflight", json={"host": "203.0.113.7", "password": "pw"}).json()
        assert pre["ok"] and pre["report"]["recommended_mode"] and not pre["report"]["problems"]

        started = client.post("/api/servers/install",
                              json={"host": "203.0.113.7", "password": "pw", "name": "vps", "mode": "careful"}).json()
        job = _wait_job(client, started["job"])
        assert job["state"] == "done", job
        assert "password" not in json.dumps(job) and "_task" not in job
        assert job["steps"][0] == "connect" and job["server"]["mode"] == "careful"

        listed = client.get("/api/servers").json()["servers"]
        assert [s["host"] for s in listed] == ["203.0.113.7"]
        # A server added before is checked again with this PC's key: no password needed.
        server.logins.clear()
        again = client.post("/api/servers/preflight", json={"host": "203.0.113.7"}).json()
        assert again["ok"] and server.logins[0].key_path and not server.logins[0].password
        assert server.logins[0].host_key == FakeRemote(server, server.logins[0]).host_key
        # Nothing secret in the list: no host key material beyond the fingerprint, no key paths.
        assert "key_path" not in json.dumps(listed) and "pw" not in json.dumps(listed)

        assert client.get("/api/servers/jobs/nope").status_code == 404
        removed = client.post(f"/api/servers/{listed[0]['id']}/uninstall", json={}).json()
        assert removed == {"ok": True}
        assert client.get("/api/servers").json() == {"servers": []}
        assert client.post("/api/servers/nope/uninstall", json={}).status_code == 404

        kinds = [r["kind"] for r in client.get("/api/journal?kind=server.").json()["records"]]
        assert kinds == ["server.removed", "server.installed"]


def test_a_failed_install_reports_its_step(monkeypatch, settings, tmp_path, release):
    server = FakeServer(tmp_path, sha="b" * 64)   # the download does not match the signed checksum
    with _api(monkeypatch, settings, server) as client:
        job = _wait_job(client, client.post("/api/servers/install", json={"host": "h", "password": "p"}).json()["job"])
        assert job["state"] == "error" and job["failed_step"] == "download" and job["error"]
        assert client.get("/api/servers").json() == {"servers": []}
        kinds = [r["kind"] for r in client.get("/api/journal?kind=server.").json()["records"]]
        assert kinds == ["server.install_failed"]


async def test_altair_is_on_the_servers_path_and_leaves_with_it(settings, tmp_path, release):
    """Found live: after the install `altair` on the server was not found, and run by its full path
    it looked for its data next to the binary."""
    server = FakeServer(tmp_path)
    record = await _installer(settings, server, InstallRequest(host="h", password="p")).run()
    wrapper = server.files["/usr/local/bin/altair"]
    assert wrapper.startswith("#!/bin/sh") and "export APP_PATH=/var/lib/altair" in wrapper
    assert 'exec /opt/altair/current/bin/altair "$@"' in wrapper

    server.commands.clear()
    import core.servers.install as m
    m_remote = m.SSHRemote
    m.SSHRemote = lambda login: FakeRemote(server, login)
    try:
        await uninstall(settings, record)
    finally:
        m.SSHRemote = m_remote
    assert any("rm -f /usr/local/bin/altair" in c for c in server.commands)


def test_a_server_without_root_gets_altair_in_the_home_folder():
    layout = Layout.for_login(False, "/home/ann")
    assert layout.command == "/home/ann/.local/bin/altair" and layout.data_dir.startswith("/home/ann/")


def test_altair_already_there_is_not_other_services():
    """Found live: re-checking a server with Altair on it recommended Autopilot because of the
    agent's own ports (8137 and its browser's)."""
    report = parse(GOOD_PROBE.replace("ports=22 53", "ports=22 53 8137"))
    assert report.busy_ports == [] and report.recommended_mode == "owner"


@pytest.mark.skipif(not shutil.which("bash") or not shutil.which("awk"), reason="needs bash and awk")
def test_the_probe_skips_the_agents_own_listeners():
    import subprocess

    from core.servers.preflight import _PROBE

    line = next(l for l in _PROBE.splitlines() if l.startswith('echo "ports='))
    ss = ('LISTEN 0 100 127.0.0.1:37595 0.0.0.0:* users:(("LocalAIAgent",pid=5,fd=14))\n'
          'LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=2,fd=3))\n'
          'LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:(("nginx",pid=9,fd=6))\n')
    script = f"ss() {{ printf '%s' {shlex.quote(ss)}; }}\n{line}"
    # Bytes: in text mode Windows would turn the newlines into CRLF, which bash cannot read.
    out = subprocess.run(["bash"], input=script.encode(), capture_output=True, timeout=20).stdout.decode()
    assert out.split("=", 1)[1].split() == ["22", "80"]


@pytest.mark.parametrize("error, expected", [
    (TimeoutError(), "did not answer in 25 s"),
    (ConnectionRefusedError(111, "Connection refused"), "Connection refused"),
    (OSError(), "OSError"),
])
async def test_a_failed_connection_says_why(monkeypatch, error, expected):
    """Found live: a timeout gave "could not connect to host:22:" with nothing after the colon."""
    import asyncssh

    from core.servers.remote import Login, RemoteError, SSHRemote

    async def refuse(*args, **kwargs):
        raise error

    monkeypatch.setattr(asyncssh, "connect", refuse)
    with pytest.raises(RemoteError) as caught:
        async with SSHRemote(Login(host="203.0.113.7", password="p")):
            pass
    message = str(caught.value)
    assert "203.0.113.7:22" in message and expected in message and not message.rstrip().endswith(":")
