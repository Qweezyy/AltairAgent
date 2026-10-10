"""Shutting a PC out of a server from the owner's phone (0.3.0 stage 7, scenario 6).

A PC reaches its server through SSH: there it is "this machine", so taking back only its body
key would change nothing. Revoking a PC body also removes its authorized_keys line and ends the
SSH sessions open with its key — the tunnel. Only this machine or a signed-in phone may revoke."""

from __future__ import annotations

import json
import os
import stat

import httpx
import pytest

from core.bodies import Identity
from core.ssh_access import SshAccess, cut, remove_key_line, sessions_with_key

PC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPcKey altair-pc-0123abcd"
OWNER_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOwnerKey owner@laptop"

LOG = """Oct 10 12:00:01 vps sshd[4101]: Accepted publickey for root from 203.0.113.7 port 50122 ssh2: ED25519 SHA256:pcFingerprint
Oct 10 12:00:05 vps sshd[4102]: Accepted publickey for root from 198.51.100.4 port 40000 ssh2: ED25519 SHA256:ownerFingerprint
Oct 10 12:30:00 vps sshd-session[4200]: Accepted publickey for altest from 127.0.0.1 port 50200 ssh2: ED25519 SHA256:pcFingerprint
Oct 10 12:31:00 vps sshd[4300]: Failed publickey for root from 203.0.113.7 port 50300 ssh2: ED25519 SHA256:pcFingerprint
"""


def test_only_the_pcs_line_leaves_authorized_keys(tmp_path):
    keys = tmp_path / "authorized_keys"
    keys.write_text(f"{OWNER_KEY}\n{PC_KEY}\n# a note\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(keys, 0o600)
    assert remove_key_line(keys, "altair-pc-0123abcd") is True
    assert keys.read_text(encoding="utf-8") == f"{OWNER_KEY}\n# a note\n"
    if os.name != "nt":
        assert stat.S_IMODE(keys.stat().st_mode) == 0o600
    assert remove_key_line(keys, "altair-pc-0123abcd") is False           # already gone
    assert remove_key_line(tmp_path / "missing", "altair-pc-x") is False


def test_the_sessions_of_the_pcs_key_are_found_in_sshds_log():
    assert sessions_with_key("SHA256:pcFingerprint", LOG) == [4101, 4200]   # OpenSSH 9 and 10 forms
    assert sessions_with_key("SHA256:ownerFingerprint", LOG) == [4102]
    assert sessions_with_key("SHA256:nobody", LOG) == []


def test_cut_removes_the_line_and_ends_only_live_sshd_sessions(tmp_path, monkeypatch):
    import core.ssh_access as mod

    keys = tmp_path / "authorized_keys"
    keys.write_text(f"{OWNER_KEY}\n{PC_KEY}\n", encoding="utf-8")
    killed = []
    monkeypatch.setattr(mod, "_is_sshd", lambda pid: pid == 4200)           # 4101 has ended since
    monkeypatch.setattr(mod.os, "kill", lambda pid, sig: killed.append(pid))
    info = {"authorized_keys": str(keys), "marker": "altair-pc-0123abcd", "fingerprint": "SHA256:pcFingerprint"}
    assert cut(info, LOG) == {"key_removed": True, "sessions_cut": 1}
    assert killed == [4200] and "altair-pc" not in keys.read_text(encoding="utf-8")


def test_the_record_keeps_only_well_formed_entries(tmp_path):
    access = SshAccess(tmp_path)
    with pytest.raises(ValueError):
        access.record("pc1", {"authorized_keys": "/root/.ssh/authorized_keys", "marker": "owner@laptop",
                              "fingerprint": "SHA256:x"})                    # not one of ours: never touched
    access.record("pc1", {"authorized_keys": "/root/.ssh/authorized_keys", "marker": "altair-pc-1",
                          "fingerprint": "SHA256:x", "extra": "dropped"})
    assert access.get("pc1") == {"authorized_keys": "/root/.ssh/authorized_keys", "marker": "altair-pc-1",
                                 "fingerprint": "SHA256:x"}
    access.forget("pc1")
    assert access.get("pc1") is None


# ------------------------------------------------------------------ the server's API


@pytest.fixture()
def server_app(monkeypatch, settings):
    import core.settings as settings_module
    import server.app as app_module
    import server.bodies as bodies_module
    import server.remote_auth as remote_auth
    import server.ws as ws_module
    from server.app import create_app

    settings.body_kind = "server"
    for mod in (settings_module, app_module, ws_module, remote_auth):
        monkeypatch.setattr(mod, "get_settings", lambda: settings)

    async def no_tunnels(app):
        app.state.tunnels = None

    monkeypatch.setattr(bodies_module, "start_tunnels", no_tunnels)
    return create_app()


async def _signed_in(app, body: Identity) -> httpx.AsyncClient:
    """A client from another address (not this machine), signed in with `body`'s key."""
    net = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("203.0.113.9", 5000)), base_url="http://srv")
    ch = (await net.post("/api/bodies/challenge", json={"id": body.id})).json()
    token = (await net.post("/api/bodies/login", json={"id": body.id, "nonce": ch["nonce"],
                                                     "signature": body.answer(ch["nonce"], ch["verifier"])})).json()["token"]
    net.headers["Authorization"] = f"Bearer {token}"
    return net


async def test_the_owners_phone_shuts_the_pc_out(server_app, settings, tmp_path, monkeypatch):
    import core.ssh_access as mod

    app = server_app
    cuts = []
    monkeypatch.setattr(mod, "cut", lambda info, log_text=None: cuts.append(info) or {"key_removed": True,
                                                                                    "sessions_cut": 1})
    async with app.router.lifespan_context(app):
        local = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://srv")
        pc = Identity(tmp_path / "pc", "pc", "Desktop")
        phone = Identity(tmp_path / "phone", "phone", "Pixel")
        code = (await local.post("/api/bodies/pair-code")).json()["code"]
        assert (await local.post("/api/bodies/pair", json={"code": code, "card": phone.card()})).json()["ok"]
        assert (await local.post("/api/bodies", json=pc.card())).json()["ok"]
        SshAccess(settings.data_dir / "identity").record(pc.id, {
            "authorized_keys": "/root/.ssh/authorized_keys", "marker": "altair-pc-0123abcd",
            "fingerprint": "SHA256:pcFingerprint"})

        from_pc = await _signed_in(app, pc)
        from_phone = await _signed_in(app, phone)
        # The list the phone shows, with itself marked.
        listed = (await from_phone.get("/api/bodies/trusted")).json()
        assert listed["you"] == phone.id and listed["self"]["kind"] == "server"
        assert {b["id"]: b["kind"] for b in listed["trusted"]} == {pc.id: "pc", phone.id: "phone"}
        # A PC body may not revoke anyone; nobody may revoke the server's own body.
        assert (await from_pc.post(f"/api/bodies/{phone.id}/revoke")).status_code == 403
        assert (await from_pc.get("/api/bodies/trusted")).status_code == 403
        own = listed["self"]["id"]
        assert (await from_phone.post(f"/api/bodies/{own}/revoke")).status_code == 400

        done = (await from_phone.post(f"/api/bodies/{pc.id}/revoke")).json()
        assert done == {"ok": True, "sessions_ended": 1, "key_removed": True, "sessions_cut": 1}
        assert cuts and cuts[0]["marker"] == "altair-pc-0123abcd"
        assert SshAccess(settings.data_dir / "identity").get(pc.id) is None
        assert (await from_pc.get("/api/notices")).status_code == 401       # its session is over
        records = (await local.get("/api/journal?kind=body.revoked")).json()["records"]
        assert records[0]["data"]["by"] == f"phone:{phone.id}" and records[0]["data"]["key_removed"] is True

        for client in (local, from_pc, from_phone):
            await client.aclose()


async def test_a_revoked_or_unknown_caller_cannot_revoke(server_app, tmp_path):
    app = server_app
    async with app.router.lifespan_context(app):
        local = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://srv")
        stranger = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("203.0.113.9", 5000)),
                                     base_url="http://srv")
        assert (await stranger.post("/api/bodies/x/revoke")).status_code == 401
        assert (await stranger.get("/api/bodies/trusted")).status_code == 401
        assert (await local.get("/api/bodies/trusted")).json()["you"] == ""
        for client in (local, stranger):
            await client.aclose()


async def test_the_installer_tells_the_server_which_key_is_the_pcs(settings, tmp_path, monkeypatch):
    import asyncssh

    from core.servers import install as install_module
    from core.servers.install import InstallRequest, ReleaseRef
    from tests.test_server_install import FakeServer, _installer

    async def fake_release(arch, repo=install_module.REPO):
        return ReleaseRef(version="0.3.0", name="Altair-0.3.0-linux-x64.zip", url="https://e/p.zip", sha256="a" * 64)

    monkeypatch.setattr(install_module, "signed_release", fake_release)
    server = FakeServer(tmp_path)
    record = await _installer(settings, server, InstallRequest(host="h", password="p")).run()
    sent = [c for c in server.commands if " --pc-ssh " in c]
    assert len(sent) == 1
    info = json.loads(sent[0].split(" --pc-ssh ", 1)[1].strip("'"))
    public = (settings.data_dir / "servers" / "keys" / f"{record.id}.pub")
    public = public if public.exists() else next((settings.data_dir / "servers").rglob(f"{record.id}*.pub"))
    assert info == {"body": Identity(settings.data_dir / "identity", settings.body_kind).id,
                    "authorized_keys": "/root/.ssh/authorized_keys", "marker": f"altair-pc-{record.id}",
                    "fingerprint": asyncssh.import_public_key(public.read_text()).get_fingerprint("sha256")}
    # An update tells it again: servers installed before this change learn it too.
    server.commands.clear()
    await _installer(settings, server, InstallRequest(host="h", update=True)).run()
    assert any(" --pc-ssh " in c for c in server.commands)
