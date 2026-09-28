"""Memory as a folder of notes with a one-line index.

What is checked: a note is a file with a header and dates the store stamps; the index has one
line per note and is all the prompt gets (capped); a note can be changed or deleted only after
it was read in this chat and while it is unchanged; old memory.json / .agent/memory.md are
moved over once without losing anything.
"""

from __future__ import annotations

import json
import time

import pytest

from core.folder_memory import FolderMemory
from core.memory import INDEX_MAX_LINES, MemoryStore, parse_note
from core.security.approval import ApprovalRequest
from core.tools.base import ToolContext
from core.tools.builtin.memory_tools import (
    MemoryDeleteTool,
    MemoryEditTool,
    MemoryReadTool,
    RecallTool,
    RememberTool,
    SuggestMemoryTool,
)


@pytest.fixture()
def ctx(settings):
    return ToolContext(settings=settings, memory=MemoryStore(settings.data_dir))


async def _remember(ctx, **kw):
    return await RememberTool().invoke({"type": "project", **kw}, ctx)


# ---------------------------------------------------------------- the store


def test_a_note_is_a_file_with_a_header_and_an_index_line(settings):
    store = MemoryStore(settings.data_dir)
    note = store.create("Prefers pnpm", "Uses pnpm, never npm, in every JS project", "feedback",
                        "Always pnpm.\n\nWhy: the lockfile.\nHow to apply: pnpm install / pnpm add.")
    path = settings.data_dir / "memory" / f"{note.name}.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\nname: prefers-pnpm\ntitle: Prefers pnpm\n")
    assert "type: feedback" in text and "created: " in text and "modified: " in text
    assert "How to apply:" in text
    index = (settings.data_dir / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert "- [Prefers pnpm](prefers-pnpm.md) — Uses pnpm, never npm, in every JS project" in index
    assert parse_note(note.name, text).body.startswith("Always pnpm.")


def test_only_the_index_goes_into_the_prompt(settings):
    store = MemoryStore(settings.data_dir)
    store.create("Deploy", "Deploys go through the staging branch", "project", "SECRET-DETAIL long body text")
    section = store.prompt_section()
    assert section.startswith("<global_memory>") and "staging branch" in section
    assert "SECRET-DETAIL" not in section                       # details are read on demand


def test_the_index_is_capped_and_the_model_is_told(settings):
    store = MemoryStore(settings.data_dir)
    for i in range(INDEX_MAX_LINES + 5):
        store.create(f"Note {i}", f"Fact number {i}", "project", "")
    assert len(store.prompt_section().splitlines()) <= INDEX_MAX_LINES + 2
    assert "over its limit" in store.index_pressure()


def test_modified_is_stamped_by_the_store(settings):
    store = MemoryStore(settings.data_dir)
    note = store.create("City", "Lives in Kazan", "user", "")
    first = note.modified
    time.sleep(1.1)
    note.description = "Lives in Moscow now"
    store.save(note)
    again = store.get(note.name)
    assert again.modified > first and again.created == note.created
    assert "Lives in Moscow now" in store.index_text() and "Kazan" not in store.index_text()


def test_a_note_edited_by_hand_is_read_as_it_is(settings):
    store = MemoryStore(settings.data_dir)
    (settings.data_dir / "memory").mkdir(parents=True)
    (settings.data_dir / "memory" / "handmade.md").write_text("# My note\nplain text, no header\n", encoding="utf-8")
    note = store.get("handmade")
    assert note.title == "handmade" and note.description == "My note" and "plain text" in note.body


# ---------------------------------------------------------------- the tools


async def test_remember_puts_user_facts_globally_and_project_facts_in_the_workspace(ctx, settings):
    r1 = await RememberTool().invoke({"title": "Role", "description": "Backend developer, Go and Python",
                                      "type": "user"}, ctx)
    r2 = await _remember(ctx, title="Test command", description="Tests: pytest -q from pc/")
    assert r1.ok and r2.ok
    assert "Backend developer" in MemoryStore(settings.data_dir).index_text()
    assert "pytest -q" in FolderMemory(settings.workspace).index_text()
    assert (settings.workspace / ".agent" / "memory" / "test-command.md").exists()


async def test_remember_refuses_a_twin_name_and_points_to_similar_notes(ctx):
    await _remember(ctx, title="Build", description="Build with python build_app.py --zip")
    again = await _remember(ctx, title="Build", description="Another build note")
    assert not again.ok and "memory_edit" in again.content
    near = await _remember(ctx, title="Building", description="Build with python build_app.py --zip always")
    assert near.ok and "Similar notes" in near.content


async def test_editing_or_deleting_needs_a_read_first(settings):
    ctx = ToolContext(settings=settings, memory=MemoryStore(settings.data_dir))
    FolderMemory(settings.workspace).create("Port", "The dev server runs on 8137", "project", "Port 8137.")

    refused = await MemoryEditTool().invoke({"name": "port", "old_text": "8137", "new_text": "8150"}, ctx)
    assert not refused.ok and "memory_read" in refused.content
    assert not (await MemoryDeleteTool().invoke({"name": "port"}, ctx)).ok

    read = await MemoryReadTool().invoke({"name": "port"}, ctx)
    assert read.ok and "Port 8137." in read.content and "modified:" in read.content
    edited = await MemoryEditTool().invoke({"name": "port", "old_text": "8137", "new_text": "8150",
                                            "description": "The dev server runs on 8150"}, ctx)
    assert edited.ok
    assert "Port 8150." in FolderMemory(settings.workspace).get("port").body
    # Consecutive edits after one's own write are fine.
    assert (await MemoryEditTool().invoke({"name": "port", "type": "reference"}, ctx)).ok


async def test_a_note_changed_since_it_was_read_must_be_read_again(ctx, settings):
    fm = FolderMemory(settings.workspace)
    fm.create("Stack", "FastAPI + Tauri", "project", "FastAPI backend.")
    await MemoryReadTool().invoke({"name": "stack"}, ctx)
    note = fm.get("stack")
    note.body = "FastAPI backend, edited by the user."          # e.g. the user edited the file
    fm.save(note)
    stale = await MemoryEditTool().invoke({"name": "stack", "body": "overwrite"}, ctx)
    assert not stale.ok and "changed since you read it" in stale.content
    await MemoryReadTool().invoke({"name": "stack"}, ctx)
    assert (await MemoryDeleteTool().invoke({"name": "stack"}, ctx)).ok
    assert fm.get("stack") is None and "stack.md" not in fm.index_text()


async def test_a_read_in_one_chat_does_not_open_edits_in_another(settings):
    FolderMemory(settings.workspace).create("Rule", "No force pushes", "feedback", "Never force-push main.")
    reader = ToolContext(settings=settings)
    other = ToolContext(settings=settings)
    await MemoryReadTool().invoke({"name": "rule"}, reader)
    assert not (await MemoryEditTool().invoke({"name": "rule", "body": "x"}, other)).ok


async def test_read_without_a_name_shows_both_indexes(ctx, settings):
    MemoryStore(settings.data_dir).create("Name", "Called Sasha", "user", "")
    FolderMemory(settings.workspace).create("Repo", "Monorepo: android/ and pc/", "project", "")
    out = (await MemoryReadTool().invoke({}, ctx)).content
    assert "[project memory" in out and "[global memory" in out and "Called Sasha" in out and "Monorepo" in out


async def test_recall_searches_the_notes_bodies(ctx, settings):
    FolderMemory(settings.workspace).create("Release", "How releases are cut", "project",
                                            "Sign with release_sign.py and upload the .sig file.")
    out = (await RecallTool().invoke({"query": "sig upload"}, ctx)).content
    assert "release" in out and "How releases are cut" in out


async def test_suggest_memory_saves_only_what_the_user_allows(settings):
    async def deny(_req: ApprovalRequest) -> bool:
        return False

    async def allow(_req: ApprovalRequest) -> bool:
        return True

    no = ToolContext(settings=settings, approver=deny)
    out = await SuggestMemoryTool().invoke({"title": "Tea", "description": "Likes green tea"}, no)
    assert "chose not to save" in out.content
    assert MemoryStore(settings.data_dir).notes() == []
    yes = ToolContext(settings=settings, approver=allow)
    assert (await SuggestMemoryTool().invoke({"title": "Tea", "description": "Likes green tea"}, yes)).ok
    assert "Likes green tea" in MemoryStore(settings.data_dir).index_text()


# ---------------------------------------------------------------- moving the old stores over


def test_old_memory_json_becomes_notes_once(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    facts = [{"id": "a", "text": "User's name is Sasha", "category": "user", "created_at": 1_700_000_000},
             {"id": "b", "text": "Prefers short answers", "category": "preference", "created_at": 1_700_000_100},
             {"id": "c", "text": "The API uses FastAPI", "category": "fact", "created_at": 1_700_000_200}]
    (settings.data_dir / "memory.json").write_text(json.dumps({"facts": facts}), encoding="utf-8")
    store = MemoryStore(settings.data_dir)
    by_desc = {n.description: n for n in store.notes()}
    assert set(by_desc) == {f["text"] for f in facts}
    assert by_desc["Prefers short answers"].type == "feedback"
    assert by_desc["User's name is Sasha"].created.startswith("2023-11-1")  # the old timestamp is kept
    assert not (settings.data_dir / "memory.json").exists()
    assert (settings.data_dir / "memory.json.migrated").exists()
    assert len(MemoryStore(settings.data_dir).notes()) == 3        # not imported twice


def test_a_long_old_fact_keeps_its_text_but_takes_one_short_index_line(settings):
    long_fact = ("Rule for Habr and other sites: write articles and replies to comments in plain human "
                 "language, like a typical user there. No bureaucratic AI tone, no 'in this article we "
                 "will look at', no piles of lists and disclaimers.")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "memory.json").write_text(json.dumps({"facts": [
        {"text": long_fact, "category": "preference", "created_at": 1_700_000_000}]}), encoding="utf-8")
    store = MemoryStore(settings.data_dir)
    note = store.notes()[0]
    assert note.body == long_fact and note.fact == long_fact           # nothing lost; the phone gets it whole
    assert len(note.description) <= 150 and note.description.startswith("Rule for Habr")
    assert all(len(line) < 300 for line in store.index_text().splitlines())
    assert store.remember(long_fact, "user").name == note.name          # the phone sending it back: no twin


def test_old_folder_memory_md_becomes_notes_once(settings):
    agent = settings.workspace / ".agent"
    agent.mkdir(parents=True)
    (agent / "memory.md").write_text(
        "# Память папки\n\n- [project] 2026-09-01: Сборка через build_app.py\n- [fact] 2026-09-02: Порт 8137\n",
        encoding="utf-8")
    fm = FolderMemory(settings.workspace)
    notes = {n.description: n for n in fm.notes()}
    assert set(notes) == {"Сборка через build_app.py", "Порт 8137"}
    assert notes["Порт 8137"].created.startswith("2026-09-02")
    assert (agent / "memory.md.migrated").exists() and not (agent / "memory.md").exists()
    assert "Сборка через build_app.py" in fm.prompt_section()


# ---------------------------------------------------------------- into the agent's prompt


async def test_a_note_saved_in_one_chat_is_in_the_next_chats_prompt(settings):
    from core.agent.runner import AgentRunner
    from core.agent.session import Session
    from core.llm.base import AssistantTurn
    from core.tools import build_default_registry
    from tests.fakes import ScriptedLLM

    MemoryStore(settings.data_dir).create("Code examples", "Wants answers with code examples", "feedback",
                                          "DETAIL-ONLY-IN-FILE")
    llm = ScriptedLLM([AssistantTurn(content="ok")])
    runner = AgentRunner(llm=llm, registry=build_default_registry(), settings=settings, session=Session())
    await runner.run("any new question")
    system = llm.calls[0]["messages"][0]["content"]
    assert "<global_memory>" in system and "Wants answers with code examples" in system
    assert "DETAIL-ONLY-IN-FILE" not in system
    assert "memory_read" in system                                   # the model is told how to open notes


def test_the_settings_panel_and_the_phone_still_get_one_line_facts(settings):
    store = MemoryStore(settings.data_dir)
    note = store.remember("Timezone is MSK", "user")
    assert note is not None and store.remember("timezone is msk", "user").name == note.name  # no twin
    facts = [(n.id, n.text, n.category) for n in store.all()]
    assert facts == [(note.name, "Timezone is MSK", "user")]
    assert store.forget(note.name) and store.all() == []
