"""Old writes reach the model without the text they wrote.

Every write_file / edit_file / apply_patch keeps its whole text in the history the model gets,
so a task that writes a few big files resent them on every request. With the old outputs
(the same batch, so the prompt cache breaks no more often), old writes lose their big text in
the model's view: the path stays, the key is dropped (no placeholder to copy), the stored
chat keeps everything.
"""

from __future__ import annotations

import json

from core.agent.session import CALLS_VIEW, Session


def _write(session: Session, n: int, name: str = "write_file", args: dict | None = None) -> None:
    call_id = f"c{n}"
    args = args or {"path": f"src/file{n}.py", "content": f"# file {n}\n" + "x = 1\n" * 400}
    session.messages.append({"role": "assistant", "content": None, "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]})
    session.add_tool_result(call_id, name, f"Wrote src/file{n}.py (2400 chars).")


def test_old_writes_lose_their_text_for_the_model_only():
    s = Session()
    s.add_user("write twelve files")
    for n in range(12):
        _write(s, n)
    count, freed = s.clear_old_tool_results(keep_recent=8, min_chars=1_000)
    assert freed > 3 * 2_000
    view = s.view()
    calls = [m["tool_calls"][0] for m in view if m.get("tool_calls")]
    old, recent = calls[:4], calls[4:]
    assert all(json.loads(c["function"]["arguments"]) == {"path": f"src/file{i}.py"} for i, c in enumerate(old))
    assert all("content" in json.loads(c["function"]["arguments"]) for c in recent)
    # The chat itself keeps every byte; ids and pairing are untouched.
    stored = [m for m in s.messages if m.get("tool_calls")]
    assert all("content" in json.loads(m["tool_calls"][0]["function"]["arguments"]) for m in stored)
    assert [c["id"] for c in calls] == [f"c{n}" for n in range(12)]
    # Folded once: a second pass changes nothing (the prompt cache stays).
    assert s.clear_old_tool_results(keep_recent=8, min_chars=1_000) == (0, 0)
    assert Session.from_dict(s.to_dict()).view() == view


def test_edits_and_patches_are_folded_and_other_tools_are_not():
    s = Session()
    s.add_user("go")
    _write(s, 0, "edit_file", {"path": "a.py", "old_text": "a" * 900, "new_text": "b" * 900})
    _write(s, 1, "apply_patch", {"patch": "*** Begin Patch\n" + "+x\n" * 600})
    _write(s, 2, "run_python", {"code": "print(1)\n" * 300})
    for n in range(3, 12):
        _write(s, n)
    s.clear_old_tool_results(keep_recent=8, min_chars=1_000)
    view_calls = {c["id"]: json.loads(c["function"]["arguments"]) for m in s.view() for c in m.get("tool_calls") or []}
    assert view_calls["c0"] == {"path": "a.py"}
    assert view_calls["c1"] == {}
    assert "code" in view_calls["c2"]                         # not a write: kept as is


def test_small_writes_and_a_small_gain_are_left_alone():
    s = Session()
    s.add_user("go")
    for n in range(12):
        _write(s, n, args={"path": f"f{n}.txt", "content": "short"})
    assert s.clear_old_tool_results(keep_recent=8, min_chars=1_000, min_free_chars=10_000) == (0, 0)
    assert not any(CALLS_VIEW in m for m in s.messages)
