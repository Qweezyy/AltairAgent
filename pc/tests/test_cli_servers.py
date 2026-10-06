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
