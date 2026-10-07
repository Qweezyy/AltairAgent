"""Steadier answers from unsteady providers (core/llm/reliability.py and its users).

Each behaviour here was found in live tests against real providers: replies that were gateway
errors, empty or endless streams the agent took for finished tasks; reasoning models thinking
without end when no level was sent; providers refusing more than 2-3 requests in flight; slow
periods that last hours, where only a backup provider helps.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import openai
import pytest

from core.agent.runner import AgentRunner
from core.agent.session import CLEARED_MARK, Session
from core.errors import LLMError
from core.llm import reliability
from core.llm.anthropic_client import to_anthropic_messages
from core.llm.base import AssistantTurn, LLMClient
from core.llm.fallback import FallbackLLM
from core.llm.openai_client import OpenAICompatClient
from core.llm.reliability import BadTurn, ProviderGate, judge_turn, reasoning_extra
from core.settings import Settings
from core.tools import build_default_registry
from core.tools.base import ToolContext
from core.tools.builtin.context_tools import ToolOutputTool
from core.tools.deferred import CORE_TOOLS, active_tool_names
from tests.fakes import ScriptedLLM, tool_call

OPENROUTER = "https://openrouter.ai/api/v1"
#: The real sleep: the client tests replace asyncio.sleep to skip retry pauses.
_sleep = asyncio.sleep


# ---------------------------------------------------------------- reasoning level per provider


@pytest.mark.parametrize(
    ("level", "base", "model", "expected"),
    [
        ("low", OPENROUTER, "z-ai/glm-5.3-flash", {"reasoning": {"effort": "low"}}),
        ("minimal", OPENROUTER, "z-ai/glm-5.3-flash", {"reasoning": {"effort": "low"}}),  # "none" is refused there
        ("high", OPENROUTER, "x/y", {"reasoning": {"effort": "high"}}),
        ("low", "https://api.gateyourway.com/v1", "glm-5.3-flash", {"thinking": {"type": "disabled"}}),
        ("minimal", "https://api.z.ai/api/paas/v4", "glm-4.6", {"thinking": {"type": "disabled"}}),
        ("high", "https://api.gateyourway.com/v1", "glm-5.3-flash", {"thinking": {"type": "enabled"}}),
        ("medium", "https://api.openai.com/v1", "gpt-5", {"reasoning_effort": "medium"}),
        (None, OPENROUTER, "x/y", {}),
        ("default", OPENROUTER, "x/y", {}),
    ],
)
def test_reasoning_level_in_each_providers_dialect(level, base, model, expected):
    assert reasoning_extra(level, base, model) == expected


def test_dialect_can_be_forced_or_switched_off():
    assert reasoning_extra("low", OPENROUTER, "x", "zai") == {"thinking": {"type": "disabled"}}
    assert reasoning_extra("low", OPENROUTER, "x", "none") == {}


# ---------------------------------------------------------------- judging an answer


def _turn(text="", calls=0, prompt=0):
    return AssistantTurn(content=text, tool_calls=[tool_call("read_file", path="a")] * calls,
                         usage={"prompt_tokens": prompt} if prompt else {})


BIG = [{"role": "system", "content": "x" * 40_000}, {"role": "user", "content": "go"}]


def test_a_real_answer_passes():
    assert judge_turn(_turn("Done: the tests pass.", prompt=9000), BIG, None) is None
    assert judge_turn(_turn("", calls=1), BIG, None) is None  # a tool call is an answer


def test_failures_in_disguise_are_named():
    assert judge_turn(_turn(""), BIG, None) == "empty"
    assert judge_turn(_turn("partial…"), BIG, None, cut=True) == "cut"
    gateway = "The request could not be completed. Please retry later, or reduce the request parameters/content."
    assert judge_turn(_turn(gateway), BIG, None) == "gateway"
    # The provider counted 3K prompt tokens of a ~9K-token request: it did not run the model.
    assert judge_turn(_turn("Sure.", prompt=3000), BIG, None) == "unprocessed"


def test_a_short_real_answer_quoting_a_gateway_phrase_is_not_an_error():
    """From a reader (dev.to): a short legitimate reply that quotes the gateway's phrase was taken
    for the gateway's error, retried and in the end failed."""
    real = ("The request could not be completed the first time: the file was locked by the editor. "
            "I closed it and ran the script again, it works now.")
    assert len(real) < 400
    assert judge_turn(_turn(real), BIG, None) is None
    # The gateway's own text, with a code or an id at most, is still caught.
    assert judge_turn(_turn("Upstream request failed (502, id 7f3a)."), BIG, None) == "gateway"


def test_a_long_gateway_error_is_caught_by_the_token_count():
    """A gateway error with a long diagnostic dump slips past the phrase rule (over 400 chars);
    the provider still did not run the model on the request, and that is what gives it away."""
    dump = "The request could not be completed.\n" + "\n".join(
        f"  at upstream.pool.call (pool.js:{i}:17) retry={i} backend=10.0.3.{i}" for i in range(12))
    assert len(dump) > 400
    assert judge_turn(_turn(dump, prompt=2100), BIG, None) == "unprocessed"


def test_no_false_alarms_on_normal_replies():
    long_reply = "Here is why: " + "the request could not be completed because… " * 20
    assert judge_turn(_turn(long_reply), BIG, None) is None  # long: a real answer about errors
    small = [{"role": "user", "content": "hi"}]
    assert judge_turn(_turn("Hello!", prompt=12), small, None) is None
    # Images are not text: a request with a big picture is not "unprocessed".
    image = [{"role": "user", "content": [{"type": "text", "text": "what is it?"},
                                          {"type": "image_url", "image_url": {"url": "data:" + "A" * 200_000}}]}]
    assert judge_turn(_turn("A cat.", prompt=1500), image, None) is None


# ---------------------------------------------------------------- the client


class _Delta:
    def __init__(self, content=None):
        self.content = content
        self.tool_calls = None


class _Chunk:
    def __init__(self, content=None, usage=None):
        self.choices = [SimpleNamespace(delta=_Delta(content), finish_reason=None)] if content is not None else []
        self.usage = usage


class _Stream:
    """A provider stream: chunks, an optional delay before the first one."""

    def __init__(self, chunks, first_delay=0.0):
        self._chunks = list(chunks)
        self._delay = first_delay
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._delay:
            delay, self._delay = self._delay, 0
            await _sleep(delay)
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)

    async def close(self):
        self.closed = True


def _client(monkeypatch, streams, **settings):
    s = Settings(openrouter_api_key="k", llm_base_url=OPENROUTER, **settings)
    client = OpenAICompatClient(settings=s, model="z-ai/glm-5.3-flash")
    sent = []

    async def fake_create(**params):
        sent.append(params)
        item = streams.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    real_sleep = asyncio.sleep

    async def no_wait(seconds):
        # Retry pauses are skipped; tiny waits (the gate's polling, test delays) still run.
        await real_sleep(seconds if seconds < 1 else 0)

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    monkeypatch.setattr("core.llm.openai_client.asyncio.sleep", no_wait)
    return client, sent


GATEWAY = "The request could not be completed. Please retry later, or reduce the request parameters/content."


async def test_a_gateway_error_reply_is_retried_and_never_shown(monkeypatch):
    usage = SimpleNamespace(prompt_tokens=50, completion_tokens=5, total_tokens=55,
                            prompt_tokens_details=SimpleNamespace(cached_tokens=40))
    client, sent = _client(monkeypatch, [_Stream([_Chunk(GATEWAY)]), _Stream([_Chunk("The answer."), _Chunk(usage=usage)])])
    shown = []

    async def on_text(t):
        shown.append(t)

    turn = await client.complete([{"role": "user", "content": "q"}], on_text=on_text)
    assert turn.content == "The answer."
    assert "".join(shown) == "The answer.", "the gateway's error text must not reach the chat"
    assert len(sent) == 2
    assert turn.usage["cached_tokens"] == 40  # the provider's cache is now visible


async def test_an_empty_stream_is_retried(monkeypatch):
    client, sent = _client(monkeypatch, [_Stream([]), _Stream([_Chunk("ok")])])
    turn = await client.complete([{"role": "user", "content": "q"}])
    assert turn.content == "ok" and len(sent) == 2


async def test_no_first_byte_in_time_gives_up_the_attempt(monkeypatch):
    client, sent = _client(monkeypatch, [_Stream([_Chunk("late")], first_delay=5), _Stream([_Chunk("on time")])],
                           llm_first_byte_timeout=0.3)
    started = time.perf_counter()
    turn = await client.complete([{"role": "user", "content": "q"}])
    assert turn.content == "on time"
    assert time.perf_counter() - started < 3, "the stalled attempt must be dropped at the first-byte limit"
    assert turn.first_byte_s < 1


async def test_a_flowing_stream_is_not_cut_by_the_first_byte_limit(monkeypatch):
    """The phone's bug in reverse: a total time limit cut long but healthy answers."""

    class Slow(_Stream):
        async def __anext__(self):
            await _sleep(0.15)
            return await super().__anext__()

    client, _ = _client(monkeypatch, [Slow([_Chunk("a" * 50) for _ in range(6)])], llm_first_byte_timeout=0.3)
    turn = await client.complete([{"role": "user", "content": "q"}])
    assert len(turn.content) == 300  # 0.9 s in total, well past the 0.3 s first-byte limit


async def test_the_reasoning_level_reaches_the_request(monkeypatch):
    client, sent = _client(monkeypatch, [_Stream([_Chunk("ok")])])
    client.reasoning = "low"
    await client.complete([{"role": "user", "content": "q"}])
    assert sent[0]["extra_body"] == {"reasoning": {"effort": "low"}}


async def test_a_provider_that_refuses_the_reasoning_field_gets_requests_without_it(monkeypatch):
    request = SimpleNamespace(method="POST", url="u", headers={})
    refusal = openai.BadRequestError("Unrecognized request argument supplied: reasoning",
                                     response=SimpleNamespace(status_code=400, request=request, headers={}),
                                     body=None)
    client, sent = _client(monkeypatch, [refusal, _Stream([_Chunk("ok")]), _Stream([_Chunk("again")])])
    client.reasoning = "low"
    assert (await client.complete([{"role": "user", "content": "q"}])).content == "ok"
    assert "extra_body" not in sent[1]
    await client.complete([{"role": "user", "content": "q"}])
    assert "extra_body" not in sent[2], "remembered: not sent again"


async def test_too_many_concurrent_requests_lowers_the_limit(monkeypatch):
    request = SimpleNamespace(method="POST", url="u", headers={})
    busy = openai.RateLimitError("Too many concurrent requests for this API key.",
                                 response=SimpleNamespace(status_code=429, request=request, headers={}), body=None)
    client, _ = _client(monkeypatch, [busy, _Stream([_Chunk("ok")])])
    gate = client._gate
    gate.in_flight = 2  # two other requests are running

    async def others_finish():
        await asyncio.sleep(0.3)
        gate.in_flight -= 2

    finishing = asyncio.create_task(others_finish())
    await client.complete([{"role": "user", "content": "q"}])
    await finishing
    assert gate.limit == 2, "three were in flight when the provider refused: hold two from now on"


async def test_the_gate_holds_requests_and_recovers():
    gate = ProviderGate(limit=1)
    order = []

    async def job(i):
        await gate.acquire()
        order.append(("in", i))
        await asyncio.sleep(0.05)
        order.append(("out", i))
        gate.release()

    await asyncio.gather(job(1), job(2))
    assert order[1][0] == "out", "the second request waited for the first"
    adaptive = ProviderGate()
    adaptive.in_flight = 3
    adaptive.too_many()
    assert adaptive.limit == 2
    adaptive.in_flight = 1
    for _ in range(20):
        adaptive.release()
        adaptive.in_flight = 1
    assert adaptive.limit == 3  # climbs back after clean answers


# ---------------------------------------------------------------- backup provider and breaker


class _Model(LLMClient):
    def __init__(self, model, *, fail=False, first_byte=0.5):
        self.model, self.fail, self.first_byte = model, fail, first_byte
        self.calls, self.seen = 0, []

    async def complete(self, messages, **kwargs):  # type: ignore[override]
        self.calls += 1
        self.seen.append((self.reasoning, self.max_attempts))
        if self.fail:
            raise LLMError(f"{self.model} is down")
        return AssistantTurn(content=f"ok:{self.model}", first_byte_s=self.first_byte)


def _fallback(models):
    built = {}

    def build(**kw):
        built.setdefault(kw["model"], models[kw["model"]])
        return built[kw["model"]]

    return FallbackLLM([{"model": "main"}, {"model": "backup"}], build)


async def test_a_slow_main_provider_hands_the_next_steps_to_the_backup(monkeypatch):
    main, backup = _Model("main", first_byte=55), _Model("backup")
    llm = _fallback({"main": main, "backup": backup})
    monkeypatch.setattr("core.settings.get_settings", lambda: Settings(llm_slow_first_byte=40, llm_breaker_minutes=10))
    assert (await llm.complete([])).content == "ok:main"   # slow once: still the main one
    assert (await llm.complete([])).content == "ok:main"   # slow twice: now taken as sick
    assert (await llm.complete([])).content == "ok:backup"
    assert main.calls == 2
    # After the break the main provider is tried again.
    monkeypatch.setattr("core.llm.reliability.time.time", lambda: time.time_ns() / 1e9 + 11 * 60)
    main.first_byte = 2
    assert (await llm.complete([])).content == "ok:main"


async def test_a_failing_main_provider_is_skipped_for_a_while(monkeypatch):
    main, backup = _Model("main", fail=True), _Model("backup")
    llm = _fallback({"main": main, "backup": backup})
    llm.reasoning = "high"
    assert (await llm.complete([])).content == "ok:backup"
    assert (await llm.complete([])).content == "ok:backup"
    assert main.calls == 1, "the breaker keeps the failed provider out instead of failing on it every step"
    # The level reaches every candidate; the one with a backup behind it gives up sooner.
    assert main.seen[0] == ("high", 2)
    assert backup.seen[0][0] == "high"


def test_backup_models_from_the_settings():
    from server.chats import fallback_models

    s = Settings(llm_fallback_models=" z-ai/glm-5.3-flash, other ,z-ai/glm-5.3-flash, glm-5.3-flash")
    assert fallback_models(s, "glm-5.3-flash") == ["z-ai/glm-5.3-flash", "other"]
    assert fallback_models(Settings(llm_fallback_models=""), "x") == []


# ---------------------------------------------------------------- the runner


class _Recording(ScriptedLLM):
    def __init__(self, turns):
        super().__init__(turns)
        self.levels = []

    async def complete(self, messages, **kwargs):
        self.levels.append(self.reasoning)
        return await super().complete(messages, **kwargs)


async def test_adaptive_reasoning_thinks_harder_right_after_a_failure(settings):
    settings.llm_reasoning = "adaptive"
    (settings.workspace / "ok.txt").write_text("fine", encoding="utf-8")
    llm = _Recording([
        AssistantTurn(tool_calls=[tool_call("read_file", path="missing.txt")]),   # fails
        AssistantTurn(tool_calls=[tool_call("read_file", path="ok.txt")]),         # works
        AssistantTurn(content="done"),
    ])
    await AgentRunner(llm=llm, registry=build_default_registry(), settings=settings, session=Session()).run("go")
    assert llm.levels == ["low", "high", "low"]


async def test_a_fixed_or_provider_default_level(settings):
    for mode, expected in (("medium", "medium"), ("default", None)):
        settings.llm_reasoning = mode
        llm = _Recording([AssistantTurn(content="done")])
        await AgentRunner(llm=llm, registry=build_default_registry(), settings=settings, session=Session()).run("go")
        assert llm.levels == [expected]


async def test_the_history_summary_asks_for_little_reasoning_and_has_room(settings):
    settings.llm_reasoning = "adaptive"
    llm = _Recording([AssistantTurn(content="summary")])
    llm.reasoning = "high"
    runner = AgentRunner(llm=llm, registry=build_default_registry(), settings=settings, session=Session())
    text = await runner._summarize_history([{"role": "user", "content": "a long talk"}])
    assert text == "summary"
    assert llm.levels == ["minimal"]
    assert llm.reasoning == "high", "the run's level comes back after the summary"


# ---------------------------------------------------------------- getting cleared outputs back


async def test_tool_output_returns_a_cleared_output_exactly(settings):
    session = Session()
    session.add_user("task")
    for i in range(6):
        session.messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]})
        session.add_tool_result(f"c{i}", "read_file", f"secret value {i} " + "x" * 3000)
    session.clear_old_tool_results(keep_recent=1)
    view = [m for m in session.view() if m["role"] == "tool"]
    assert view[0]["content"].startswith(CLEARED_MARK) and 'tool_output(id="c0")' in view[0]["content"]

    ctx = ToolContext(settings=settings, session=session)
    tool = ToolOutputTool()
    out = await tool.run(tool.Args(id="c0"), ctx)
    assert "secret value 0 " in out and out.count("x") >= 3000
    missing = await tool.run(tool.Args(id="nope"), ctx)
    assert not missing.ok


async def test_a_long_output_comes_back_in_pages(settings):
    session = Session()
    session.add_tool_result("c1", "execute_command", "A" * 12_000 + "B" * 5_000)
    tool = ToolOutputTool()
    first = await tool.run(tool.Args(id="c1"), ToolContext(settings=settings, session=session))
    assert "start=12000" in first and "B" not in first.split("\n", 1)[1].split("...")[0]
    second = await tool.run(tool.Args(id="c1", start=12000), ToolContext(settings=settings, session=session))
    assert second.count("B") == 5000


def test_tool_output_is_always_at_hand():
    assert "tool_output" in CORE_TOOLS


# ---------------------------------------------------------------- loading tools by family


@pytest.mark.parametrize("used", ["browser_navigate", "start_dev_server", "android_devices", "run_lint"])
def test_one_tool_brings_its_family(used):
    from core.tools.deferred import family

    registry = build_default_registry()
    messages = [{"role": "assistant", "tool_calls": [{"function": {"name": used, "arguments": "{}"}}]}]
    active = set(active_tool_names(registry, True, messages, {}))
    same = {n for n in registry.names() if family(n) == family(used)}
    assert len(same) > 1 and same <= active
    assert not {n for n in registry.names() if n.startswith("browser_")} <= active or used.startswith("browser_")


# ---------------------------------------------------------------- notes in the middle of a chat


def test_anthropic_keeps_mid_chat_notes_where_they_happened():
    system, msgs = to_anthropic_messages([
        {"role": "system", "content": "You are Altair."},
        {"role": "user", "content": "do it"},
        {"role": "assistant", "content": "working"},
        {"role": "system", "content": "The user stopped this task."},
        {"role": "user", "content": "go on"},
    ])
    assert system == "You are Altair."
    assert "stopped" not in system, "a note must not become a standing order in the system prompt"
    assert any("The user stopped this task." in str(m["content"]) for m in msgs)


# ---------------------------------------------------------------- compact line numbers


async def test_read_file_numbers_lines_compactly(settings):
    from core.tools.builtin.files import ReadFileTool

    (settings.workspace / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    tool = ReadFileTool()
    out = await tool.run(tool.Args(path="a.py"), ToolContext(settings=settings))
    assert "1|x = 1" in out and "2|y = 2" in out and " | " not in out


def test_a_rejection_names_the_detector_version():
    """Decisions are comparable only by version: the rejection says which rules made it."""
    from pathlib import Path

    from core.llm import reliability

    assert reliability.DETECTOR_VERSION
    client = (Path(reliability.__file__).parent / "openai_client.py").read_text(encoding="utf-8")
    assert "detector=%s" in client and "reliability.DETECTOR_VERSION" in client
