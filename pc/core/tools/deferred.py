"""Deferred tool loading: send the model only the tools it is likely to need.

With ~100 tools the schemas alone cost ~22k tokens on every request, and a long tool
list measurably hurts tool selection (Anthropic's tool search study: Opus 4 49% -> 74%).
So only a small core set is sent up front; every other tool is listed by name in the
system prompt and becomes available once the model finds it with `tool_search` (or
simply calls it by name). The same approach as Anthropic's `defer_loading` and Claude
Code's deferred tools, done client-side so it works with any provider.

The active set is derived from the conversation itself — core tools, tools loaded by
tool_search in this run, and every tool already called in the history — so it survives
restarts without extra state and only grows, which keeps the prompt cache stable.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

#: Always sent: the everyday tools of reading, editing, running and searching.
CORE_TOOLS = frozenset({
    "tool_search",
    "read_file",
    "list_directory",
    "find_files",
    "grep_search",
    "code_map",
    "write_file",
    "edit_file",
    "apply_patch",
    "execute_command",
    "run_python",
    "run_tests",
    "web_search",
    "fetch_url",
    "update_plan",
    "ask",
    "remember",
    "git_diff",
    "read_skill",
})

#: A registry this small is sent whole: deferral would only add a search round trip.
MIN_TOOLS_TO_DEFER = 30

#: ctx.scratch key with the names tool_search loaded during the current run.
LOADED_KEY = "_loaded_tools"


def deferral_active(registry: Any, enabled: bool) -> bool:
    return enabled and "tool_search" in registry and len(registry) >= MIN_TOOLS_TO_DEFER


def deferred_names(registry: Any, enabled: bool) -> list[str]:
    """Names of the tools that are not sent up front (sorted)."""
    if not deferral_active(registry, enabled):
        return []
    return [name for name in registry.names() if name not in CORE_TOOLS]


def called_tool_names(messages: Iterable[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for message in messages:
        for call in message.get("tool_calls") or ():
            function = call.get("function") if isinstance(call, dict) else None
            if isinstance(function, dict) and function.get("name"):
                names.add(str(function["name"]))
    return names


def active_tool_names(
    registry: Any, enabled: bool, messages: Iterable[dict[str, Any]], scratch: dict[str, Any]
) -> list[str] | None:
    """Tools to send with the next request, or None for "all of them"."""
    if not deferral_active(registry, enabled):
        return None
    wanted = set(CORE_TOOLS) | called_tool_names(messages) | set(scratch.get(LOADED_KEY, ()))
    return [name for name in registry.names() if name in wanted]


def mark_loaded(scratch: dict[str, Any], names: Iterable[str]) -> None:
    loaded: list[str] = scratch.setdefault(LOADED_KEY, [])
    for name in names:
        if name not in loaded:
            loaded.append(name)
