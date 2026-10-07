"""Memory and skills shared by the bodies (0.3.0 stage 3): a three-way sync between this PC and a
server — the server here is a real app with the real /api/sync endpoints and its own folders."""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from core.bodies_sync import manifest, plan, safe_rel, sync_area
from core.memory import MemoryStore
from core.settings import Settings


@pytest.fixture()
def bodies(tmp_path):
    from server import bodies as bodies_routes

    def settings_in(name: str) -> Settings:
        app_dir, work = tmp_path / name / "app", tmp_path / name / "work"
        app_dir.mkdir(parents=True)
        work.mkdir(parents=True)
        return Settings(_env_file=None, openrouter_api_key="k", app_path=app_dir, workspace_path=work)

    pc, server = settings_in("pc"), settings_in("server")
    app = FastAPI()
    app.state.settings = server
    bodies_routes.install(app)
    base_file = tmp_path / "pc" / "base.json"

    async def call(method, path, payload):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://server") as http:
            r = await http.request(method, path, json=payload)
            r.raise_for_status()
            return r.json()

    async def sync():
        return [await sync_area(pc, area, base_file, call) for area in ("memory", "skills")]

    return pc, server, sync


def _note(settings, title, body):
    return MemoryStore(settings.data_dir).create(title, f"about {title}", "project", body)


async def test_what_one_body_learned_the_other_knows(bodies):
    pc, server, sync = bodies
    note = _note(pc, "Deploy steps", "build, then rsync")
    skill = server.skills_dir / "server-ops" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: server-ops\n---\nrestart with systemctl\n", encoding="utf-8")

    results = await sync()
    assert results[0]["to_there"] == 1 and results[1]["to_here"] == 1
    there = MemoryStore(server.data_dir)
    assert there.get(note.name).body.strip() == "build, then rsync"
    assert "Deploy steps" in there.index_text()                     # the index is rebuilt there
    assert (pc.skills_dir / "server-ops" / "SKILL.md").read_text(encoding="utf-8").endswith("systemctl\n")

    again = await sync()
    assert all(r["to_here"] == r["to_there"] == 0 for r in again)      # nothing left to do


async def test_a_deletion_is_not_undone_by_the_other_side(bodies):
    pc, server, sync = bodies
    note = _note(pc, "Old idea", "x")
    await sync()
    MemoryStore(pc.data_dir).delete(note.name)
    results = await sync()
    assert results[0]["deleted_there"] == 1
    assert MemoryStore(server.data_dir).get(note.name) is None
    assert MemoryStore(pc.data_dir).get(note.name) is None             # and it stays gone here
    assert "Old idea" not in MemoryStore(server.data_dir).index_text()


async def test_an_edit_wins_over_a_deletion_and_the_newer_edit_over_the_older(bodies):
    pc, server, sync = bodies
    kept = _note(pc, "Kept", "v1")
    both = _note(pc, "Both", "v1")
    await sync()

    # Deleted here, edited there: the edit comes back here.
    MemoryStore(pc.data_dir).delete(kept.name)
    edited = MemoryStore(server.data_dir).get(kept.name)
    edited.body = "v2 from the server"
    MemoryStore(server.data_dir).save(edited)

    # Edited on both: the newer one is kept on both.
    here = MemoryStore(pc.data_dir).get(both.name)
    here.body = "older, from the pc"
    MemoryStore(pc.data_dir).save(here)
    old = time.time() - 60
    os.utime(MemoryStore(pc.data_dir).path_of(both.name), (old, old))
    there = MemoryStore(server.data_dir).get(both.name)
    there.body = "newer, from the server"
    MemoryStore(server.data_dir).save(there)

    results = await sync()
    assert results[0]["conflicts"] == [f"{both.name}.md"]
    assert MemoryStore(pc.data_dir).get(kept.name).body.strip() == "v2 from the server"
    assert MemoryStore(pc.data_dir).get(both.name).body.strip() == "newer, from the server"
    assert MemoryStore(server.data_dir).get(both.name).body.strip() == "newer, from the server"


def test_the_plan_reads_every_difference_against_the_base():
    a, b = {"sha": "a", "mtime": 1}, {"sha": "b", "mtime": 2}
    p = plan(local={"new_here": a, "same": a, "changed_here": b, "edited_both": a},
             remote={"new_there": a, "same": a, "changed_here": a, "gone_here": a, "edited_both": b},
             base={"same": "a", "changed_here": "a", "gone_here": "a", "gone_there": "a", "edited_both": "c"})
    assert p.to_remote == ["changed_here", "new_here"]
    assert p.to_local == ["edited_both", "new_there"]                   # the server's is newer
    assert p.delete_remote == ["gone_here"] and p.delete_local == []
    assert p.conflicts == ["edited_both"]


@pytest.mark.parametrize("bad", ["../x.md", "/etc/passwd", "a/../../b", "C:/Windows/x", ""])
def test_a_path_cannot_leave_its_area(bad):
    with pytest.raises(ValueError):
        safe_rel(bad)


async def test_the_server_refuses_paths_outside_and_strangers(bodies, tmp_path):
    pc, server, sync = bodies
    from server import bodies as bodies_routes

    app = FastAPI()
    app.state.settings = server
    bodies_routes.install(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://s") as http:
        r = await http.post("/api/sync/apply", json={"area": "memory", "put": {"../../evil.md": "eA=="}})
        assert r.status_code == 400 and not (Path(server.data_dir).parent / "evil.md").exists()
        assert (await http.get("/api/sync/manifest?area=secrets")).status_code == 400
    lan = httpx.ASGITransport(app=app, client=("192.168.1.50", 5555))
    async with httpx.AsyncClient(transport=lan, base_url="http://s") as phone:
        assert (await phone.get("/api/sync/manifest?area=memory")).status_code == 403
    assert manifest(server.skills_dir, "skills") is not None


async def test_a_skill_deleted_on_one_body_leaves_no_empty_folder_on_the_other(bodies):
    pc, server, sync = bodies
    skill = pc.skills_dir / "zz-check" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: zz-check\n---\nx\n", encoding="utf-8")
    await sync()
    assert (server.skills_dir / "zz-check" / "SKILL.md").exists()
    skill.unlink()
    skill.parent.rmdir()
    await sync()
    assert not (server.skills_dir / "zz-check").exists()
