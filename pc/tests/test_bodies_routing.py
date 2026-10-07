"""One agent on several bodies (0.3.0 stage 3): a machine tool runs on a server when given `body`.

The "server" is a real HTTP server on a local port with the real /api/tools/run and the real
tools, working in its own folder; the PC side calls it the way it calls a server behind its
tunnel. Checked: the file lands on the server and not here; the approval follows the stricter
mode; the folder given once is remembered by the chat; unknown and disconnected bodies fail
clearly; the endpoint is only for this machine; the planner and the `bodies` tool rank the bodies.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

import core.bodies_routing as routing
from core.bodies_planner import Needs, rank
from core.servers.registry import ServerRecord
from core.settings import Settings
from core.tools import build_default_registry
from core.tools.base import ToolContext


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def server_side(tmp_path):
    """A second body: its own settings and folder, the real endpoint and tools."""
    from server import bodies as bodies_routes

    app_dir, work = tmp_path / "server-app", tmp_path / "server-work"
    app_dir.mkdir()
    work.mkdir()
    settings = Settings(_env_file=None, openrouter_api_key="k", app_path=app_dir, workspace_path=work, approval_mode="manual")
    app = FastAPI()
    app.state.settings = settings
    app.state.registry = build_default_registry()
    bodies_routes.install(app)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    end = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < end
        time.sleep(0.02)

    class Side:
        pass

    side = Side()
    side.app, side.port, side.settings, side.work = app, port, settings, work
    yield side
    server.should_exit = True
    thread.join(10)


class _Tunnel:
    def __init__(self, port: int, mode: str = "owner", state: str = "online") -> None:
        self.record = ServerRecord(id="srv1", name="test-vps", host="203.0.113.7", port=22, user="root",
                                   mode=mode, body_id="b-srv1", system="Ubuntu 22.04", arch="x64")
        self.local_port, self.state, self.error, self.agent = port, state, "", "ok"
        self.status = {"system": "Ubuntu 22.04", "arch": "x86_64", "cpus": 1, "mem_mb": 1963, "gpus": [],
                       "docker": False, "workspace": "/var/lib/altair/workspace",
                       "load": {"cpu_pct": 2, "mem_used_mb": 400, "tasks": 0}}

    def public(self):
        return {"state": self.state, "agent": self.agent, "error": self.error, "port": self.local_port,
                "status": self.status, "last_seen": 1.0, "online_since": 1.0}


class _Tunnels:
    def __init__(self, *tunnels):
        self._all = list(tunnels)

    def all(self):
        return self._all


@pytest.fixture()
def no_servers():
    routing.set_tunnels(None)
    yield
    routing.set_tunnels(None)


@pytest.fixture()
def pc(settings):
    settings.approval_mode = "bypass"
    return settings


def _ctx(settings, approver=None, session=None) -> ToolContext:
    from core.security.approval import always_allow

    return ToolContext(settings=settings, approver=approver or always_allow, session=session)


def test_without_servers_the_tools_are_exactly_what_they_were(no_servers):
    from core.tools.builtin import builtin_tools

    plain = {t.name: t.schema() for t in builtin_tools()}
    wrapped = build_default_registry()
    assert all(wrapped.get(name).schema() == schema for name, schema in plain.items())
    routing.set_tunnels(_Tunnels(_Tunnel(1)))
    params = wrapped.get("execute_command").schema()["function"]["parameters"]["properties"]
    assert "body" in params and "body" not in wrapped.get("web_search").schema()["function"]["parameters"]["properties"]
    assert routing.ROUTED <= set(wrapped.names())          # every routed name is a real tool


async def test_a_file_written_on_the_server_is_there_and_not_here(pc, server_side, no_servers):
    routing.set_tunnels(_Tunnels(_Tunnel(server_side.port)))
    reg = build_default_registry()
    out = await reg.get("write_file").invoke(
        {"path": "hello.txt", "content": "from the pc", "body": "test-vps"}, _ctx(pc))
    assert out.ok, out.content
    assert (server_side.work / "hello.txt").read_text(encoding="utf-8") == "from the pc"
    assert not (Path(pc.workspace) / "hello.txt").exists()
    assert out.metadata["body"] == "test-vps"

    ran = await reg.get("execute_command").invoke({"command": "python -c \"print(6*7)\"", "body": "test-vps"}, _ctx(pc))
    assert ran.ok and "42" in ran.content

    # Without `body` it is this machine, as always.
    here = await reg.get("write_file").invoke({"path": "here.txt", "content": "pc"}, _ctx(pc))
    assert here.ok and (Path(pc.workspace) / "here.txt").exists() and not (server_side.work / "here.txt").exists()

    kinds = [json.loads(line)["kind"] for line in
             (server_side.settings.data_dir / "journal").glob("*.jsonl").__next__().read_text(encoding="utf-8").splitlines()]
    assert kinds.count("tool.remote") == 2                  # the server keeps its own record


async def test_a_folder_given_once_is_remembered_by_the_chat(pc, server_side, no_servers, tmp_path):
    from core.agent.session import Session

    project = tmp_path / "srv-project"
    routing.set_tunnels(_Tunnels(_Tunnel(server_side.port)))
    reg = build_default_registry()
    session = Session()
    args = {"path": "a.txt", "content": "1", "body": f"test-vps:{project.as_posix()}"}
    session.messages.append({"role": "assistant", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": json.dumps(args)}}]})
    first = await reg.get("write_file").invoke(args, _ctx(pc, session=session))
    assert first.ok and (project / "a.txt").exists()        # created there

    later = await reg.get("write_file").invoke({"path": "b.txt", "content": "2", "body": "test-vps"},
                                               _ctx(pc, session=session))
    assert later.ok and (project / "b.txt").exists() and not (server_side.work / "b.txt").exists()


async def test_the_stricter_mode_asks_before_the_call_leaves(pc, server_side, no_servers):
    """A chat with no confirmations, a server the owner marked "Careful": the card is shown here."""
    asked = []

    async def deny(request):
        asked.append(request)
        return False

    routing.set_tunnels(_Tunnels(_Tunnel(server_side.port, mode="careful")))
    reg = build_default_registry()
    out = await reg.get("write_file").invoke({"path": "x.txt", "content": "1", "body": "test-vps"}, _ctx(pc, deny))
    assert not out.ok and len(asked) == 1 and "test-vps" in asked[0].reason
    assert not (server_side.work / "x.txt").exists()        # refused before it left

    # An "Owner" server from a chat that asks: this chat's own mode still asks.
    routing.set_tunnels(_Tunnels(_Tunnel(server_side.port, mode="owner")))
    pc.approval_mode = "manual"
    asked.clear()
    out = await reg.get("write_file").invoke({"path": "y.txt", "content": "1", "body": "test-vps"}, _ctx(pc, deny))
    assert not out.ok and len(asked) == 1


async def test_unknown_and_disconnected_bodies_fail_clearly(pc, no_servers):
    routing.set_tunnels(_Tunnels(_Tunnel(1, state="connecting")))
    reg = build_default_registry()
    unknown = await reg.get("read_file").invoke({"path": "a", "body": "gpu-box"}, _ctx(pc))
    assert not unknown.ok and "No body named 'gpu-box'" in unknown.content and "test-vps" in unknown.content
    offline = await reg.get("read_file").invoke({"path": "a", "body": "test-vps"}, _ctx(pc))
    assert not offline.ok and "not connected" in offline.content


async def test_the_endpoint_is_for_this_machine_and_its_machine_tools_only(server_side):
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{server_side.port}") as http:
        assert (await http.post("/api/tools/run", json={"tool": "phone_ask_user", "args": {}})).status_code == 404
        r = await http.post("/api/tools/run", json={"tool": "write_file",
                                                    "args": {"path": "n.txt", "content": "1", "body": "elsewhere"}})
        assert r.json()["ok"] and (server_side.work / "n.txt").exists()   # no second hop
    lan = httpx.ASGITransport(app=server_side.app, client=("192.168.1.50", 5555))
    async with httpx.AsyncClient(transport=lan, base_url="http://server") as phone:
        r = await phone.post("/api/tools/run", json={"tool": "execute_command", "args": {"command": "id"}})
        assert r.status_code == 403


# ------------------------------------------------------------------ where to run it


def _bodies():
    pc = {"id": "me", "self": True, "name": "pc", "state": "online", "agent": "ok", "labels": [],
          "status": {"arch": "amd64", "cpus": 12, "mem_mb": 32000, "gpus": ["RTX 4060"], "docker": True,
                     "load": {"cpu_pct": 10, "mem_used_mb": 16000, "tasks": 1}}}
    vps = {"id": "srv1", "self": False, "name": "test-vps", "state": "online", "agent": "ok", "labels": ["builds"],
           "status": {"arch": "x86_64", "cpus": 1, "mem_mb": 1963, "gpus": [], "docker": False,
                      "load": {"cpu_pct": 1, "mem_used_mb": 400, "tasks": 0}}}
    down = {"id": "srv2", "self": False, "name": "arm-box", "state": "offline", "agent": "", "labels": [],
            "status": {"arch": "aarch64", "mem_mb": 8000}}
    return [pc, vps, down]


def test_hard_needs_rule_bodies_out():
    gpu = rank(_bodies(), Needs(gpu=True))
    assert gpu[0].name == "pc" and [c.fits for c in gpu] == [True, False, False]
    assert "no GPU" in gpu[1].reasons and "not connected" in gpu[2].reasons
    big = rank(_bodies(), Needs(min_memory_gb=4))
    assert [c.name for c in big if c.fits] == ["pc"]
    assert rank(_bodies(), Needs(arch="arm64"))[0].fits is False      # the only arm64 is offline


def test_long_work_goes_to_a_server_and_short_work_stays_here():
    assert rank(_bodies(), Needs())[0].name == "pc"
    long = rank(_bodies(), Needs(long_running=True))
    assert long[0].name == "test-vps" and "keeps running when the PC is off" in long[0].reasons
    assert rank(_bodies(), Needs(data_on="test-vps"))[0].name == "test-vps"
    assert rank(_bodies(), Needs(label="builds"))[0].name == "test-vps"
    assert rank(_bodies(), Needs(long_running=True, pinned="pc"))[0].name == "pc"


async def test_the_bodies_tool_lists_and_picks(pc, no_servers):
    tunnel = _Tunnel(1)
    routing.set_tunnels(_Tunnels(tunnel))
    tool = build_default_registry().get("bodies")
    listed = await tool.invoke({}, _ctx(pc))
    assert listed.ok and "pc (this machine)" in listed.content and "test-vps · online" in listed.content
    picked = await tool.invoke({"action": "pick", "long_running": True}, _ctx(pc))
    assert picked.ok and picked.content.startswith("1. test-vps") and "Best: test-vps" in picked.content
    routing.set_tunnels(None)
    assert "no servers" in (await tool.invoke({}, _ctx(pc))).content


def test_the_prompt_knows_the_bodies_only_when_there_are_some(no_servers):
    assert routing.prompt_section() == ""
    routing.set_tunnels(_Tunnels(_Tunnel(1)))
    section = routing.prompt_section()
    assert section.startswith("<bodies>") and "- test-vps: Ubuntu 22.04, x86_64, 1 CPU, 2 GB RAM; mode owner" in section
    assert "default folder /var/lib/altair/workspace" in section


@pytest.mark.parametrize("value, expected", [
    ("test-vps", ("test-vps", "")),
    ("test-vps:/srv/app", ("test-vps", "/srv/app")),
    ("test-vps:~/app", ("test-vps", "~/app")),
    ("win-box:C:/work", ("win-box", "C:/work")),     # found live: split at the drive's colon
    ("pc", ("pc", "")),
    ("odd:name", ("odd:name", "")),
])
def test_a_body_and_its_folder_are_told_apart(value, expected):
    assert routing.split_body(value) == expected
