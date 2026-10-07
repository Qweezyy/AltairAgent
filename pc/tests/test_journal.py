"""The Journal: an append-only, hash-chained record of what the agent did.

Checked: records come back newest first with filters and paging; an edited, a removed or a torn
line is caught by verify(); files roll over by size with the chain going on; a restarted app
continues the chain; a real run through the backend writes the user's message, the start, the
approval, the tool call and the end in that order; the API is for this PC only; JOURNAL=false
writes nothing.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server.chats as chats_module
from core.journal import Journal, summarize
from core.llm.base import AssistantTurn
from tests.fakes import ScriptedLLM, tool_call


def _fill(j: Journal, n: int, chat: str = "c1") -> None:
    for i in range(n):
        j.append("tool" if i % 2 else "run.started", chat=chat, run="r1", i=i)


def test_records_come_back_newest_first_with_filters_and_pages(tmp_path):
    j = Journal(tmp_path)
    _fill(j, 10)
    j.append("approval", chat="c2", name="write_file", scope="once", approved=True)
    all_ = j.read(limit=100)
    assert [r["seq"] for r in all_] == list(range(11, 0, -1))
    assert {r["body"] for r in all_} == {"pc"}
    assert [r["seq"] for r in j.read(chat="c2")] == [11]
    assert all(r["kind"] == "tool" for r in j.read(kinds=["tool"]))
    assert {r["kind"] for r in j.read(kinds=["run."])} == {"run.started"}      # a prefix
    page1 = j.read(limit=4)
    page2 = j.read(limit=4, before=page1[-1]["seq"])
    assert [r["seq"] for r in page1 + page2] == list(range(11, 3, -1))
    assert [r["seq"] for r in j.read(since=9)] == [11, 10]                       # only what is new


@pytest.mark.parametrize("damage", ["edit", "remove", "reorder"])
def test_a_changed_history_is_caught(tmp_path, damage):
    j = Journal(tmp_path)
    _fill(j, 6)
    assert j.verify() == {"ok": True, "checked": 6, "broken_at": None, "reason": ""}
    path = j.files()[0]
    lines = path.read_text(encoding="utf-8").splitlines()
    if damage == "edit":
        record = json.loads(lines[2])
        record["data"]["i"] = 999                     # someone "fixes" what the agent did
        lines[2] = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    elif damage == "remove":
        del lines[3]
    else:
        lines[1], lines[2] = lines[2], lines[1]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    verdict = Journal(tmp_path).verify()
    assert not verdict["ok"] and verdict["broken_at"] is not None


def test_files_roll_over_and_the_chain_goes_on(tmp_path):
    j = Journal(tmp_path, max_bytes=600)
    _fill(j, 30)
    assert len(j.files()) > 3
    assert j.verify()["ok"] and j.verify()["checked"] == 30
    assert [r["seq"] for r in j.read(limit=30)] == list(range(30, 0, -1))      # across the files


def test_a_restarted_app_continues_the_chain(tmp_path):
    _fill(Journal(tmp_path), 5)
    again = Journal(tmp_path)
    again.append("run.finished", chat="c1")
    assert again.read(limit=1)[0]["seq"] == 6
    assert again.verify()["ok"]


def test_a_torn_last_line_is_reported_and_writing_goes_on(tmp_path):
    j = Journal(tmp_path)
    _fill(j, 3)
    with open(j.files()[0], "a", encoding="utf-8") as fh:
        fh.write('{"seq":4,"kind":"to')               # the machine died mid-write
    again = Journal(tmp_path)
    again.append("run.started", chat="c1")
    verdict = again.verify()
    assert not verdict["ok"] and "damaged" in verdict["reason"]
    assert again.read(limit=1)[0]["kind"] == "run.started"


def test_long_values_are_kept_short():
    small = summarize({"content": "x" * 5000, "items": list(range(100)), "n": 3})
    assert len(small["content"]) == 600 and small["content"].endswith("…")
    assert len(small["items"]) == 41 and small["items"][-1] == "… +60"
    assert small["n"] == 3


# ------------------------------------------------------------------ a real run through the backend


def _client(monkeypatch, settings):
    import core.settings as settings_module
    import server.app as app_module
    import server.ws as ws_module
    from server.app import create_app

    for mod in (settings_module, app_module, ws_module):
        monkeypatch.setattr(mod, "get_settings", lambda: settings)
    return TestClient(create_app())


def _drain(ws, wanted: str, limit: int = 200) -> dict:
    for _ in range(limit):
        m = ws.receive_json()
        if m.get("type") == wanted:
            return m
    raise AssertionError(wanted)


def test_a_run_is_journaled_in_order(monkeypatch, settings):
    settings.approval_mode = "manual"
    llm = ScriptedLLM([
        AssistantTurn(tool_calls=[tool_call("write_file", path="notes.txt", content="hello")]),
        AssistantTurn(content="Written."),
    ])
    monkeypatch.setattr(chats_module, "build_llm_client", lambda model=None, **kw: llm)
    with _client(monkeypatch, settings) as tc, tc.websocket_connect("/ws") as ws:
        ws.receive_json()  # ready
        ws.send_json({"type": "run", "task": "write the notes"})
        asked = _drain(ws, "approval.requested")
        ws.send_json({"type": "approval", "request_id": asked["request_id"], "scope": "once"})
        _drain(ws, "run.finished")
        chat = None
        for _ in range(50):
            records = tc.get("/api/journal", params={"limit": 50}).json()["records"]
            if any(r["kind"] == "run.finished" for r in records):
                break
            time.sleep(0.05)
        kinds = [r["kind"] for r in reversed(records)]
        assert kinds == ["user", "run.started", "approval", "tool", "run.finished"]
        by_kind = {r["kind"]: r for r in records}
        chat = by_kind["user"]["chat"]
        assert chat and all(r["chat"] == chat for r in records)
        assert by_kind["user"]["data"]["text"] == "write the notes"
        assert by_kind["approval"]["data"] == {"name": "write_file", "scope": "once", "approved": True,
                                               "reason": by_kind["approval"]["data"]["reason"],
                                               "args": by_kind["approval"]["data"]["args"]}
        assert by_kind["approval"]["data"]["args"]["path"] == "notes.txt"
        assert by_kind["tool"]["data"]["name"] == "write_file" and by_kind["tool"]["data"]["ok"] is True
        assert by_kind["run.finished"]["run"] == by_kind["run.started"]["run"] != ""
        assert tc.get("/api/journal/verify").json()["ok"]
        # Filters through the API.
        only_tools = tc.get("/api/journal", params={"kind": "tool,approval"}).json()["records"]
        assert {r["kind"] for r in only_tools} == {"tool", "approval"}
    assert Path(settings.data_dir / "journal").is_dir()


def test_the_journal_is_for_this_pc_only(monkeypatch, settings):
    import server.app as app_module

    monkeypatch.setattr(app_module, "_LOOPBACK_HOSTS", {"127.0.0.1"})  # the test client as a LAN device
    with _client(monkeypatch, settings) as tc:
        assert tc.get("/api/journal").status_code == 403
        assert tc.get("/api/journal/verify").status_code == 403


def test_journal_off_writes_nothing(monkeypatch, settings):
    settings.journal = False
    settings.approval_mode = "bypass"
    llm = ScriptedLLM([AssistantTurn(content="Hi.")])
    monkeypatch.setattr(chats_module, "build_llm_client", lambda model=None, **kw: llm)
    with _client(monkeypatch, settings) as tc, tc.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "run", "task": "hello"})
        _drain(ws, "run.finished")
        assert tc.get("/api/journal").json()["records"] == []


# ------------------------------------------------------------------ the window and the terminal


def test_the_window_has_a_journal_pane_in_both_languages():
    static = Path(__file__).resolve().parents[1] / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    assert 'id="pane-journal"' in html and 'data-pane="journal"' in html
    js = (static / "redesign.js").read_text(encoding="utf-8")
    assert '"journal"' in js.split("const PANES")[1].split(";")[0] and "function openJournal" in js
    i18n = (static / "i18n.js").read_text(encoding="utf-8")
    for key in ("top.journal", "jr.verify", "jr.whole", "jr.broken", "jr.f.tools", "jr.approval.deny"):
        assert i18n.count(f'"{key}"') == 2, key


def test_the_terminal_prints_journal_lines():
    from cli.journal_view import line
    from cli.texts import Texts

    t = Texts("en")
    record = {"seq": 3, "ts": time.time(), "kind": "approval", "chat": "abc123def",
              "data": {"name": "write_file", "scope": "deny", "approved": False}}
    assert "Denied write_file" in line(record, t).plain and "abc123" in line(record, t).plain
    tool = {"seq": 4, "ts": time.time(), "kind": "tool", "chat": "", "data": {"name": "read_file", "args": {"path": "a.py"}, "ok": True}}
    assert "Read(a.py)" in line(tool, t).plain


def test_a_servers_journal_says_it_was_the_server(tmp_path, monkeypatch, settings):
    """Found live: the journal on the test server stamped its records "pc"."""
    import core.settings as settings_module
    from core.journal import get_journal

    settings.body_kind = "server"
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)
    record = get_journal(tmp_path / "server-journal").append("tool.remote", tool="execute_command")
    assert record["body"] == "server"
