"""`altair server …`: the terminal's servers, end to end through the real app (in-process) and the
scripted server of test_server_install: list, check, add with live steps, remove."""

from __future__ import annotations

import io
import sys

import httpx
import pytest
from rich.console import Console

import core.servers.install as install_module
from cli import servers as cli_servers
from cli.backend import Backend
from cli.texts import Texts
from core.servers.install import ReleaseRef

sys.path.insert(0, __file__.rsplit("tests", 1)[0] + "tests")
from test_server_install import FakeRemote, FakeServer  # noqa: E402


@pytest.fixture()
async def world(monkeypatch, settings, tmp_path):
    import core.servers.remote as remote_module
    import core.settings as settings_module
    import server.app as app_module
    import server.ws as ws_module
    from server.app import create_app

    for mod in (settings_module, app_module, ws_module):
        monkeypatch.setattr(mod, "get_settings", lambda: settings)
    server = FakeServer(tmp_path)
    fake = lambda login: FakeRemote(server, login)  # noqa: E731
    monkeypatch.setattr(install_module, "SSHRemote", fake)
    monkeypatch.setattr(remote_module, "SSHRemote", fake)
    # The tunnels have tests of their own (test_bodies_tunnel.py); here they would log in to the
    # scripted server too and mix their logins with the installer's.
    import server.bodies as bodies_module

    async def no_tunnels(app):
        app.state.tunnels = None

    monkeypatch.setattr(bodies_module, "start_tunnels", no_tunnels)

    async def fake_release(arch, repo=install_module.REPO):
        return ReleaseRef(version="0.3.0", name="Altair-0.3.0-linux-x64.zip", url="https://example/pkg.zip",
                          sha256="a" * 64)

    monkeypatch.setattr(install_module, "signed_release", fake_release)
    out = io.StringIO()
    console = Console(file=out, width=120, highlight=False, force_terminal=False)
    app = create_app()
    async with app.router.lifespan_context(app):
        client = cli_servers.Servers(Backend(port=1), Texts(), console, transport=httpx.ASGITransport(app=app))
        yield client, server, out


def test_only_the_server_commands_are_taken():
    assert cli_servers.wants(["server"]) and cli_servers.wants(["server", "add", "h"])
    assert cli_servers.wants(["server", "--help"])
    # A task that starts with the word stays a task for the agent.
    assert not cli_servers.wants(["server", "is", "slow,", "look"]) and not cli_servers.wants(["fix", "server"])
    assert not cli_servers.wants([])


async def test_add_list_and_remove_from_the_terminal(world, monkeypatch):
    client, server, out = world
    assert await client.show_list() == 0 and "altair server add" in out.getvalue()

    monkeypatch.setattr(sys, "stdin", io.StringIO("hunter2\n"))
    args = cli_servers.parse(["add", "203.0.113.7", "--password-stdin", "-y", "--name", "vps"])
    assert await client.add(args) == 0
    text = out.getvalue()
    assert "Ubuntu 22.04.5 LTS" in text and "SHA256:fake" in text          # the check's report
    for step in cli_servers.STEPS:                                          # every step, in order
        assert Texts()(f"srv.step.{step}") in text
    assert text.index(Texts()("srv.step.connect")) < text.index(Texts()("srv.step.health"))
    assert "hunter2" not in text and server.logins[0].password == "hunter2"
    assert server.logins[-1].key_path and not server.logins[-1].password

    out.truncate(0)
    out.seek(0)
    assert await client.show_list() == 0 and "root@203.0.113.7:22" in out.getvalue() and "vps" in out.getvalue()

    # Added before: checked again with the key, no password asked.
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    report, login = await client.check(cli_servers.parse(["check", "203.0.113.7"]))
    assert report is not None and login["password"] == ""

    assert await client.remove(cli_servers.parse(["remove", "nope", "-y"])) == 1
    assert await client.remove(cli_servers.parse(["remove", "vps", "-y"])) == 0
    assert await client.all() == []


async def test_a_new_server_without_a_terminal_needs_the_password_on_stdin(world, monkeypatch):
    client, _server, out = world
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))     # not a tty
    report, login = await client.check(cli_servers.parse(["check", "198.51.100.1"]))
    assert report is None and login is None and "--password-stdin" in out.getvalue()


async def test_a_failed_install_names_the_step(world, monkeypatch):
    client, server, out = world
    server.sha = "b" * 64                                   # the download does not match the signature
    monkeypatch.setattr(sys, "stdin", io.StringIO("pw\n"))
    assert await client.add(cli_servers.parse(["add", "h", "--password-stdin", "-y"])) == 1
    assert Texts()("srv.failed", step=Texts()("srv.step.download")) in out.getvalue()


def test_a_body_is_reached_through_this_pcs_backend():
    pc = Backend(port=8765)
    assert pc.http == pc.root == "http://127.0.0.1:8765" and pc.ws == "ws://127.0.0.1:8765/ws"
    pc.body = "srv1"
    assert pc.http == "http://127.0.0.1:8765/b/srv1" and pc.ws == "ws://127.0.0.1:8765/b/srv1/ws"
    assert pc.root == "http://127.0.0.1:8765"          # the bodies and the servers stay this PC's


def test_a_body_is_found_by_name_id_address_or_pc():
    from cli.bodies_view import live, resolve

    bodies = [{"id": "me", "self": True, "name": "desk", "state": "online"},
              {"id": "fb9a83ba2580", "name": "test-vps", "host": "203.0.113.7", "state": "online", "agent": "ok"},
              {"id": "c0ffee", "name": "test-gpu", "host": "198.51.100.2", "state": "offline"}]
    assert resolve(bodies, "pc")["id"] == "me"
    assert resolve(bodies, "TEST-VPS")["id"] == "fb9a83ba2580"
    assert resolve(bodies, "203.0.113.7")["id"] == "fb9a83ba2580"
    assert resolve(bodies, "fb9a")["id"] == "fb9a83ba2580"         # a unique beginning
    assert resolve(bodies, "test") is None                           # two match: no guessing
    assert resolve(bodies, "nothing") is None
    assert [live(b) for b in bodies[1:]] == ["on", "off"]
