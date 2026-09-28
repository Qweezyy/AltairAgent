"""Stopping the agent keeps the history whole, and the context ring follows the run live.

Before: a stop during tool calls left calls without answers (providers reject such a history
with HTTP 400) and dropped the results of calls that had finished; a stop while the answer was
streaming lost the part the user had already read; the ring moved only when a run ended.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from core.agent.runner import AgentRunner
from core.agent.session import Session
from core.events import ContextUsage
from core.llm.base import AssistantTurn
from core.tools.base import Tool
from core.tools.registry import ToolRegistry
from tests.fakes import ScriptedLLM, tool_call


class _Args(BaseModel):
    seconds: float = 0.0


class _Sleep(Tool):
    name = "sleep_tool"
    description = "Waits."
    Args = _Args
    category = "read"

    def auto_verdict(self, args, ctx):  # type: ignore[override]
        return "allow"

    async def run(self, args: _Args, ctx):
        await asyncio.sleep(args.seconds)
        return f"slept {args.seconds}"


class _Streaming(ScriptedLLM):
    """Streams the answer word by word, slowly enough to be stopped midway."""

    async def complete(self, messages, *, on_text=None, **kw):
        self.calls.append({"messages": [dict(m) for m in messages]})
        turn = self.turns.pop(0) if self.turns else AssistantTurn(content="ok")
        if turn.content and on_text:
            for word in turn.content.split(" "):
                await on_text(word + " ")
                await asyncio.sleep(0.05)
        return turn


def _runner(settings, llm, session):
    settings.approval_mode = "bypass"
    return AgentRunner(llm=llm, registry=ToolRegistry([_Sleep()]), settings=settings, session=session)


async def _stop_after(runner, task, seconds):
    run = asyncio.create_task(runner.run(task))
    await asyncio.sleep(seconds)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run


def _valid_for_provider(messages):
    """Every tool call is answered right after its assistant message, in order."""
    for i, m in enumerate(messages):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            ids = [c["id"] for c in m["tool_calls"]]
            answers = [x.get("tool_call_id") for x in messages[i + 1 : i + 1 + len(ids)]]
            assert answers == ids, (ids, answers)


async def test_stopping_during_tools_keeps_finished_results_and_answers_every_call(settings):
    session = Session()
    llm = _Streaming([
        AssistantTurn(tool_calls=[tool_call("sleep_tool", "fast", seconds=0.05),
                                  tool_call("sleep_tool", "slow", seconds=10)]),
        AssistantTurn(content="done"),
    ])
    runner = _runner(settings, llm, session)
    await _stop_after(runner, "do two things", 0.6)

    tools = [m for m in session.messages if m.get("role") == "tool"]
    assert len(tools) == 2
    assert tools[0]["content"] == "slept 0.05"                 # the call that finished: its real result
    assert "Stopped by the user" in tools[1]["content"]        # the one cut off: said so

    await runner.run("go on")
    _valid_for_provider(llm.calls[-1]["messages"])             # the next request is accepted


async def test_stopping_a_streaming_answer_keeps_what_the_user_saw(settings):
    session = Session()
    words = " ".join(f"w{i}" for i in range(60))
    llm = _Streaming([AssistantTurn(content=words), AssistantTurn(content="ok")])
    runner = _runner(settings, llm, session)
    await _stop_after(runner, "explain", 0.5)

    kept = [m for m in session.messages if m.get("role") == "assistant"]
    assert kept and kept[-1]["content"].startswith("w0 w1") and "Interrupted by the user" in kept[-1]["content"]
    await runner.run("continue")
    assert any("w0 w1" in str(m.get("content")) for m in llm.calls[-1]["messages"])  # the model sees it


async def test_a_finished_answer_is_not_kept_twice_when_stopped_later(settings):
    session = Session()
    llm = _Streaming([
        AssistantTurn(content="checking now", tool_calls=[tool_call("sleep_tool", seconds=10)]),
        AssistantTurn(content="ok"),
    ])
    runner = _runner(settings, llm, session)
    await _stop_after(runner, "check", 0.6)
    assert sum("checking now" in str(m.get("content")) for m in session.messages) == 1


async def test_the_ring_follows_the_run_live(settings):
    session = Session()
    events: list[ContextUsage] = []

    async def emitter(event):
        if isinstance(event, ContextUsage):
            events.append(event)

    llm = ScriptedLLM([
        AssistantTurn(tool_calls=[tool_call("sleep_tool", seconds=0)], usage={"prompt_tokens": 9_000, "completion_tokens": 20}),
        AssistantTurn(content="done", usage={"prompt_tokens": 9_500, "completion_tokens": 10}),
    ])
    settings.approval_mode = "bypass"
    runner = AgentRunner(llm=llm, registry=ToolRegistry([_Sleep()]), settings=settings, session=session,
                         emitter=emitter)
    await runner.run("go")

    assert len(events) >= 4                                   # during the run, not only at its end
    assert events[0].exact is False                           # before any provider count: an estimate
    exact = [e for e in events if e.exact]
    assert exact and exact[-1].tokens >= 9_500                # anchored on the provider's count
    tokens = [e.tokens for e in exact]
    assert tokens == sorted(tokens)                           # grows as the run adds to the history


def test_masking_lowers_the_live_count(settings):
    session = Session()
    session.add_user("task")
    session.messages.append({"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]})
    session.add_tool_result("c1", "read_file", "x" * 30_000)
    session.context_tokens, session.context_mark = 20_000, session.token_estimate()
    before, exact = session.context_now()
    assert exact and before == 20_000
    session.clear_old_tool_results(keep_recent=0)
    after, _ = session.context_now()
    assert after < before - 5_000                             # masked output leaves the window
    again = Session.from_dict(session.to_dict())
    assert again.context_now() == session.context_now()       # survives a restart
