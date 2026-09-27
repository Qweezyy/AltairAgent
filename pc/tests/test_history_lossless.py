"""The whole conversation is kept: the model gets a shortened view, the chat keeps everything.

The UI feed used to keep only its last 400 entries, and clearing, superseding and folding
edited the stored messages in place, so the start of long chats and old tool outputs were
lost for good.
"""

from __future__ import annotations

import json

from core.agent.runner import AgentRunner
from core.agent.session import CLEARED_MARK, Session
from core.agent.storage import SessionStore
from core.chat_search import search_chats
from core.llm.base import AssistantTurn
from core.tools.registry import ToolRegistry
from tests.fakes import ScriptedLLM


def test_the_feed_keeps_every_entry_across_save_and_load(settings):
    session = Session()
    for i in range(1_500):
        session.append_timeline({"kind": "user" if i % 2 == 0 else "answer", "text": f"entry {i}"})
    store = SessionStore(settings=settings)
    store.save(session)
    again = store.load(session.id)
    assert len(again.timeline) == 1_500
    assert again.timeline[0]["text"] == "entry 0" and again.timeline[-1]["text"] == "entry 1499"


async def test_folding_and_clearing_lose_nothing_and_send_no_marks(settings):
    """A long chat past its budget: the model gets a summary and masked outputs, while the
    chat file still has every message word for word."""
    settings.context_token_budget = 3_000
    settings.context_compaction = True
    settings.tool_result_clearing = True
    settings.tool_result_keep_recent = 1
    session = Session()
    session.set_system_prompt("system")
    for i in range(20):
        session.add_user(f"question {i} " + "q" * 300)
        session.messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]})
        session.add_tool_result(f"c{i}", "read_file", f"file body {i} " + "f" * 2_000)
        session.add_assistant_turn(AssistantTurn(content=f"answer {i} " + "a" * 300))
    # Everything but the system prompt, which each run rebuilds (it is not the conversation).
    originals = [json.dumps(m, ensure_ascii=False) for m in session.messages[1:]]

    llm = ScriptedLLM([AssistantTurn(content="summary of the start"), AssistantTurn(content="done")])
    await AgentRunner(llm=llm, registry=ToolRegistry([]), session=session, settings=settings).run("next")

    sent = llm.calls[-1]["messages"]
    assert not any(k.startswith("_") for m in sent for k in m)          # marks never reach the provider
    assert any(str(m.get("content", "")).startswith(CLEARED_MARK) or "summary" in str(m.get("content", ""))
               for m in sent)                                          # the model got a shortened view
    assert len(json.dumps(sent)) < sum(len(o) for o in originals) / 2

    SessionStore(settings=settings).save(session)
    stored = SessionStore(settings=settings).load(session.id)
    kept = {json.dumps({k: v for k, v in m.items() if not k.startswith("_")}, ensure_ascii=False)
            for m in stored.messages}
    missing = [o[:80] for o in originals if o not in kept]
    assert not missing, missing


def test_the_agent_can_search_the_folded_part(settings):
    session = Session(title="Long chat")
    session.append_timeline({"kind": "user", "text": "the launch code word is PELICAN"})
    for i in range(600):
        session.append_timeline({"kind": "answer", "text": f"filler {i}"})
    session.add_user("the launch code word is PELICAN")
    session.replace_prefix(1, "[folded]")
    SessionStore(settings=settings).save(session)
    hits = search_chats(settings.storage_dir, "PELICAN", current_session_id=session.id, scope="current")
    assert hits and "PELICAN" in hits[0].snippet


def test_chats_saved_before_this_change_still_load(settings):
    old = {"id": "abc123", "title": "old", "messages": [
        {"role": "system", "content": "p"},
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "x", "name": "read_file",
         "content": "[Earlier output of read_file (9000 chars) was cleared to save context.]"},
    ], "timeline": [{"kind": "user", "text": "hi"}]}
    store = SessionStore(settings=settings)
    (store.storage_dir / "abc123.json").write_text(json.dumps(old), encoding="utf-8")
    loaded = store.load("abc123")
    assert [m["content"] for m in loaded.view()] == [m["content"] for m in old["messages"]]
