"""Token economy: deferred tool loading (tool_search) and clearing old tool outputs.

Why these exist: with ~100 tools the schemas cost ~22k tokens on every request, and old
tool outputs dominate long histories. Both are cut here without losing capability:
deferred tools stay one search away, cleared outputs can be re-run.
"""

from __future__ import annotations

import json
import re

from core.agent.prompt import build_system_prompt
from core.agent.runner import AgentRunner
from core.agent.session import CLEARED_MARK, CLEARING_PROTECTED_TOOLS, Session
from core.llm.base import AssistantTurn
from core.tools import build_default_registry
from core.tools.base import ToolContext
from core.tools.builtin.tool_search import ToolSearchTool, rank_tools
from core.tools.deferred import CORE_TOOLS, LOADED_KEY, active_tool_names, deferred_names
from tests.fakes import ScriptedLLM, tool_call

# ------------------------------------------------------------ deferred loading


def test_core_tools_all_exist():
    registry = build_default_registry()
    missing = sorted(CORE_TOOLS - set(registry.names()))
    assert not missing, missing


def test_active_set_is_core_plus_history_plus_loaded():
    registry = build_default_registry()
    messages = [{"role": "assistant", "tool_calls": [{"function": {"name": "git_log", "arguments": "{}"}}]}]
    active = active_tool_names(registry, True, messages, {LOADED_KEY: ["browser_click"]})

    assert active is not None
    assert set(active) == set(CORE_TOOLS) | {"git_log", "browser_click"}
    assert active == sorted(active)  # stable order keeps the prompt cache prefix stable


def test_deferral_off_or_small_registry_sends_everything():
    registry = build_default_registry()
    assert active_tool_names(registry, False, [], {}) is None
    small = registry.clone()
    for name in small.names()[20:]:
        small.remove(name)
    assert active_tool_names(small, True, [], {}) is None
    assert deferred_names(small, True) == []


def test_core_schemas_are_small_and_english():
    registry = build_default_registry()
    core = json.dumps(registry.schemas(active_tool_names(registry, True, [], {})), ensure_ascii=False)
    everything = json.dumps(registry.schemas(), ensure_ascii=False)
    # Model-facing text is English (Cyrillic costs ~1.5-2x the tokens) ...
    assert not re.search(r"[а-яё]", core, re.I), re.findall(r".{25}[а-яё]+", core, re.I)[:3]
    # ... and the up-front schemas are a small fraction of the full set.
    assert len(core) < len(everything) / 4


def test_rank_tools_finds_by_words_in_name_and_description():
    tools = build_default_registry().all()
    assert rank_tools(tools, "browser click", 3)[0].name == "browser_click"
    assert rank_tools(tools, "git commit", 3)[0].name == "git_commit"
    assert "db_query" in [t.name for t in rank_tools(tools, "sqlite query", 5)]
    assert rank_tools(tools, "", 3) == []


async def test_tool_search_select_loads_exact_names():
    registry = build_default_registry()
    ctx = ToolContext(registry=registry)
    result = await ToolSearchTool().invoke({"query": "select:git_log, browser_tabs, nope_tool"}, ctx)

    assert result.ok, result.content
    assert ctx.scratch[LOADED_KEY] == ["git_log", "browser_tabs"]
    assert "git_log(" in result.content and "Not found: nope_tool" in result.content


async def test_tool_search_without_matches_explains():
    ctx = ToolContext(registry=build_default_registry())
    result = await ToolSearchTool().invoke({"query": "zzzqqq"}, ctx)
    assert not result.ok
    assert "deferred_tools" in result.content


def test_prompt_lists_deferred_tools_by_name_only(settings):
    registry = build_default_registry()
    prompt = build_system_prompt(settings=settings, registry=registry)
    section = prompt.split("<deferred_tools>")[1].split("</deferred_tools>")[0]

    assert "browser_click" in section and "git_commit" in section
    assert "read_file" not in section  # core tools are not deferred
    assert len(section) < 3_000  # names only — the whole point is to be cheap


async def test_runner_sends_only_active_tools_and_loads_on_search(settings):
    """End to end: search a deferred tool, and the next request carries its schema."""
    settings.tool_search = True
    llm = ScriptedLLM([
        AssistantTurn(tool_calls=[tool_call("tool_search", query="select:git_log")]),
        AssistantTurn(content="done"),
    ])
    runner = AgentRunner(llm=llm, registry=build_default_registry(), settings=settings, session=Session())
    await runner.run("show the history")

    first = {t["function"]["name"] for t in llm.calls[0]["tools"]}
    second = {t["function"]["name"] for t in llm.calls[1]["tools"]}
    assert "git_log" not in first and "read_file" in first
    assert "git_log" in second
    assert len(first) < 30


# ------------------------------------------------------------ clearing old tool outputs


def _history(n: int, size: int = 5_000, name: str = "read_file") -> Session:
    session = Session()
    session.add_user("task")
    for i in range(n):
        session.messages.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": "{}"}}],
        })
        session.add_tool_result(f"c{i}", name, f"output {i} " + "x" * size)
    return session


def test_clearing_replaces_old_outputs_and_keeps_recent():
    session = _history(12)
    cleared, freed = session.clear_old_tool_results(keep_recent=4)

    assert cleared == 8 and freed > 30_000
    tools = [m for m in session.messages if m["role"] == "tool"]
    assert all(m["content"].startswith(CLEARED_MARK) for m in tools[:8])
    assert all(m["content"].startswith("output") for m in tools[8:])
    # Calls stay intact, so the history remains valid for the provider.
    assert sum(1 for m in session.messages if m.get("tool_calls")) == 12


def test_clearing_is_idempotent_and_skips_small_and_protected():
    session = _history(10)
    session.add_tool_result("s", "list_directory", "tiny")
    session.messages.insert(2, {"role": "tool", "tool_call_id": "a", "name": "ask", "content": "y" * 5_000})
    session.clear_old_tool_results(keep_recent=2)

    assert session.clear_old_tool_results(keep_recent=2) == (0, 0)  # nothing left to clear
    ask = next(m for m in session.messages if m.get("name") == "ask")
    assert ask["content"] == "y" * 5_000  # user answers are never cleared
    assert any(m["content"] == "tiny" for m in session.messages if m["role"] == "tool")


def test_clearing_waits_until_it_is_worth_a_cache_break():
    session = _history(6, size=1_500)
    assert session.clear_old_tool_results(keep_recent=2, min_free_chars=50_000) == (0, 0)
    assert session.clear_old_tool_results(keep_recent=2, min_free_chars=1_000)[0] == 4


def test_protected_tool_names_exist():
    names = set(build_default_registry().names())
    assert CLEARING_PROTECTED_TOOLS <= names, CLEARING_PROTECTED_TOOLS - names


async def test_runner_clears_old_outputs_past_half_budget(settings):
    settings.context_token_budget = 20_000
    settings.tool_result_clearing = True
    settings.tool_result_keep_recent = 2
    session = _history(10, size=4_000)  # ~13k tokens > half of the budget
    llm = ScriptedLLM([AssistantTurn(content="ok")])
    runner = AgentRunner(llm=llm, registry=build_default_registry(), settings=settings, session=session)
    await runner.run("next")

    sent = [m for m in llm.calls[0]["messages"] if m["role"] == "tool"]
    assert sum(m["content"].startswith(CLEARED_MARK) for m in sent) == 8
    assert sent[-1]["content"].startswith("output 9")
