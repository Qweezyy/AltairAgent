"""The context window: what fills it, what is dropped, and what the ring shows.

A browsing task filled 40% of a 1M window in a few short requests: every browser action
returned the whole page, and old snapshots stayed in the history and were resent on every
request, because clearing only began at half of the (1M) budget.
"""

from __future__ import annotations

from core.agent.runner import CLEARING_CEILING_TOKENS, AgentRunner
from core.agent.session import CLEARED_MARK, KEEP_PAGE_STATES, TOOL_MEDIA_MARK, Session
from core.llm.base import AssistantTurn
from core.settings import Settings
from core.tools import build_default_registry
from tests.fakes import ScriptedLLM

IMAGE = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}


def _browsing(n_pages: int, n_shots: int = 0, size: int = 8_000) -> Session:
    session = Session()
    session.add_user("fill in my profile", parts=[IMAGE])  # the user's own attachment
    for i in range(n_pages):
        session.messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"b{i}", "type": "function", "function": {"name": "browser_click", "arguments": "{}"}}]})
        session.add_tool_result(f"b{i}", "browser_click", f"URL: https://example.com/{i}\n" + "- node\n" * (size // 7))
    for i in range(n_shots):
        session.add_user(f"{TOOL_MEDIA_MARK} is the avatar updated? #{i}", parts=[IMAGE])
    return session


def test_stale_page_snapshots_and_screenshots_are_dropped():
    session = _browsing(10, n_shots=5)
    before = session.token_estimate()
    dropped, freed = session.supersede_page_states()

    assert dropped == (10 - KEEP_PAGE_STATES) + (5 - KEEP_PAGE_STATES)
    view = session.view()                                            # what the model gets
    pages = [m for m in view if m.get("role") == "tool"]
    assert all(m["content"].startswith(CLEARED_MARK) for m in pages[:-KEEP_PAGE_STATES])
    assert "https://example.com/0" in pages[0]["content"]          # which page it was stays
    assert all(m["content"].startswith("URL:") for m in pages[-KEEP_PAGE_STATES:])
    shots = [m for m in view if m["role"] == "user" and TOOL_MEDIA_MARK in str(m["content"])]
    assert all(isinstance(m["content"], str) for m in shots[:-KEEP_PAGE_STATES])  # image gone, text kept
    assert all(isinstance(m["content"], list) for m in shots[-KEEP_PAGE_STATES:])
    assert isinstance(view[0]["content"], list)                    # the user's own image is kept
    # The chat itself keeps every page and screenshot whole.
    stored = [m for m in session.messages if m.get("role") == "tool"]
    assert all(m["content"].startswith("URL:") for m in stored)
    assert all(isinstance(m["content"], list) for m in session.messages if m["role"] == "user")
    assert session.token_estimate() < before / 3 and freed > 0
    assert session.supersede_page_states() == (0, 0)               # idempotent


def test_superseding_waits_for_a_worthwhile_batch():
    session = _browsing(3, size=2_000)  # one stale snapshot of 2K chars: not worth a cache break
    assert session.supersede_page_states() == (0, 0)
    assert session.supersede_page_states(min_free_chars=1_000)[0] == 1


async def test_runner_sends_only_fresh_page_states(settings):
    settings.tool_result_clearing = True
    settings.context_token_budget = 1_000_000
    session = _browsing(12, n_shots=4)
    llm = ScriptedLLM([AssistantTurn(content="done")])
    runner = AgentRunner(llm=llm, registry=build_default_registry(), settings=settings, session=session)
    await runner.run("next")

    sent = llm.calls[0]["messages"]
    fresh = [m for m in sent if m["role"] == "tool" and not m["content"].startswith(CLEARED_MARK)]
    assert len(fresh) == KEEP_PAGE_STATES
    images = sum(1 for m in sent if isinstance(m.get("content"), list)
                 for p in m["content"] if p.get("type") == "image_url")
    assert images == 1 + KEEP_PAGE_STATES  # the user's attachment + the newest screenshots


async def test_a_huge_budget_does_not_postpone_clearing(settings):
    """With a 1M budget old outputs used to stay until 500K; the ceiling caps that."""
    settings.tool_result_clearing = True
    settings.context_token_budget = 1_000_000
    settings.tool_result_keep_recent = 2
    session = Session()
    session.add_user("task")
    for i in range(40):
        session.messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]})
        session.add_tool_result(f"c{i}", "read_file", "x" * 10_000)
    assert session.token_estimate() > CLEARING_CEILING_TOKENS
    llm = ScriptedLLM([AssistantTurn(content="ok")])
    await AgentRunner(llm=llm, registry=build_default_registry(), settings=settings, session=session).run("go")
    sent = [m for m in llm.calls[0]["messages"] if m["role"] == "tool"]
    assert sum(m["content"].startswith(CLEARED_MARK) for m in sent) == 38


async def test_the_ring_uses_the_providers_own_count(settings):
    session = Session()
    turn = AssistantTurn(content="ok", usage={"prompt_tokens": 1_000, "context_tokens": 23_000, "completion_tokens": 50})
    await AgentRunner(llm=ScriptedLLM([turn]), registry=build_default_registry(), settings=settings,
                      session=session).run("hi")
    assert session.context_tokens == 23_050  # the cached prefix counts: it is in the window
    again = Session.from_dict(session.to_dict())
    assert again.context_tokens == 23_050
    again.reset()
    assert again.context_tokens == 0


async def test_ring_falls_back_to_prompt_tokens(settings):
    session = Session()
    turn = AssistantTurn(content="ok", usage={"prompt_tokens": 9_000, "completion_tokens": 10})
    await AgentRunner(llm=ScriptedLLM([turn]), registry=build_default_registry(), settings=settings,
                      session=session).run("hi")
    assert session.context_tokens == 9_010


def test_settings_understand_1m_and_friends():
    for text, want in {"1M": 1_000_000, "1м": 1_000_000, "200K": 200_000, "200 тыс": 200_000,
                       "1 000 000": 1_000_000, "1,000,000": 1_000_000, "1.5M": 1_500_000,
                       "1,5M": 1_500_000, "262144": 262_144}.items():
        assert Settings(context_token_budget=text, _env_file=None).context_token_budget == want, text
    assert Settings(max_run_tokens="2M", _env_file=None).max_run_tokens == 2_000_000


async def test_anthropic_counts_the_cached_prefix_as_context(settings):
    import json as _json

    import httpx

    from core.llm.anthropic_client import AnthropicClient

    events = [
        ("message_start", {"type": "message_start", "message": {"usage": {
            "input_tokens": 120, "cache_read_input_tokens": 30_000, "cache_creation_input_tokens": 500}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 7}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    body = "".join(f"event: {name}\ndata: {_json.dumps(data)}\n\n" for name, data in events)
    client = AnthropicClient(settings=settings, model="claude-x", base_url="https://api.anthropic.com/v1", api_key="k")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})))
    turn = await client.complete([{"role": "user", "content": "hi"}])
    assert turn.usage["prompt_tokens"] == 120            # what is billed at the full price
    assert turn.usage["context_tokens"] == 30_620        # what the window holds


def test_ws_ring_reports_exact_count_and_404_page(monkeypatch, settings):
    from fastapi.testclient import TestClient

    import core.settings as settings_module
    import server.app as app_module
    import server.ws as ws_module

    for mod in (settings_module, app_module, ws_module):
        monkeypatch.setattr(mod, "get_settings", lambda s=settings: s)
    turn = AssistantTurn(content="ok", usage={"prompt_tokens": 40_000, "completion_tokens": 100})
    monkeypatch.setattr(ws_module, "build_llm_client", lambda model=None, **kw: ScriptedLLM([turn]))
    with TestClient(app_module.create_app()) as tc:
        with tc.websocket_connect("/ws") as ws:
            first = ws.receive_json()
            assert first["type"] == "ready"
            ws.send_json({"type": "run", "task": "hi"})
            usage = None
            for _ in range(80):
                m = ws.receive_json()
                if m["type"] == "context.usage":
                    usage = m
                if m["type"] == "run.finished":
                    break
            for _ in range(10):
                m = ws.receive_json()
                if m["type"] == "context.usage":
                    usage = m
                    break
        assert usage == {"type": "context.usage", "tokens": 40_100, "exact": True}
        # A stray link in the window: a page with a way back, not bare JSON.
        page = tc.get("/habr-0.1.0/article.md", headers={"accept": "text/html"})
        assert page.status_code == 404 and 'href="/"' in page.text and "text/html" in page.headers["content-type"]
        api = tc.get("/api/nope", headers={"accept": "text/html"})
        assert api.headers["content-type"].startswith("application/json")


def _page_history(shapes: str) -> Session:
    """F = a whole page, d = a page diff, in the order the browser returned them."""
    from core.agent.session import FULL_PAGE_MARK, PAGE_CHANGES_MARK

    session = Session()
    session.add_user("task")
    for i, kind in enumerate(shapes):
        session.messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"p{i}", "type": "function", "function": {"name": "browser_click", "arguments": "{}"}}]})
        body = (f"URL: https://x/{i}\n\n{FULL_PAGE_MARK}; use refs):\n" + "- node\n" * 3_000 if kind == "F"
                else f"URL: https://x/{i}\n\n{PAGE_CHANGES_MARK} (...):\n+ added {i}")
        session.add_tool_result(f"p{i}", "browser_click", body)
    return session


def test_page_diffs_keep_the_page_they_were_taken_against():
    session = _page_history("FddFdddFdddddd")
    session.supersede_page_states(keep=2, min_free_chars=0)
    tools = [m["content"] for m in session.view() if m["role"] == "tool"]
    alive = "".join("x" if c.startswith(CLEARED_MARK) else ("F" if c.count("\n") > 100 else "d") for c in tools)
    # Everything before the second-to-last whole page goes; that page and all after it stay.
    assert alive == "xxxFdddFdddddd"


def test_budget_clearing_never_takes_the_latest_whole_page():
    session = _page_history("F" + "d" * 12)
    session.messages[-1]["content"] += "y" * 2_000   # recent outputs are big enough to clear
    session.clear_old_tool_results(keep_recent=2, min_chars=10)
    first = next(m for m in session.messages if m["role"] == "tool")
    assert not first["content"].startswith(CLEARED_MARK)  # the base of the diffs after it
