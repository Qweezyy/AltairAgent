"""GateYourWay stability lab: one request = one agent-like call, its outcome classified.

Outcome kinds:
  ok          real answer (text or tool call), prompt actually processed
  disguised   HTTP 200 but a gateway error text, or prompt tokens far below the request size
  empty       stream/body ended with no text and no tool call
  cut         the connection broke mid-stream (after the first byte)
  timeout     no first byte within the read timeout
  http_XXX    non-200 status
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
from pathlib import Path
import time

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "pc"))
import ctx  # noqa: E402
from prov import PROVIDERS  # noqa: E402

GW = PROVIDERS[0]
SYS = ctx.system_prompt()
TOOLS = ctx.tools()
GATEWAY_PHRASES = ("could not be completed", "retry later", "reduce the request", "rate limit",
                   "upstream", "overloaded", "service unavailable", "internal error")
SIZES = {"small": (0, 0), "medium": (4, 9000), "large": (8, 16000)}


def make_messages(size: str, salt: int) -> list[dict]:
    n, chars = SIZES[size]
    hist = ctx.history(n, chars) if n else [{"role": "user", "content": "Hi"}]
    hist = hist + [{"role": "user", "content": f"(q{salt}) In one sentence: which module looks riskiest, or read one more file?"}]
    return [{"role": "system", "content": SYS}] + hist


def expected_tokens(body: dict) -> float:
    return (len(json.dumps(body.get("messages"), ensure_ascii=False)) + len(json.dumps(body.get("tools") or [], ensure_ascii=False))) / 4.5


def classify(text: str, calls: int, usage: dict | None, body: dict) -> str:
    pin = (usage or {}).get("prompt_tokens") or 0
    low = text.lower()
    if not calls and any(p in low for p in GATEWAY_PHRASES) and len(text) < 400:
        return "disguised"
    if pin and pin < 0.6 * expected_tokens(body):
        return "disguised"
    if not text.strip() and not calls:
        return "empty"
    return "ok"


async def call(client: httpx.AsyncClient, body: dict, *, read_timeout: float = 120.0,
               ttfb_timeout: float | None = None) -> dict:
    """One attempt. ttfb_timeout: give up if the first byte takes longer (a watchdog)."""
    t0 = time.perf_counter()
    out = {"ttfb": None}
    try:
        if body.get("stream"):
            async with client.stream("POST", f"{GW['base_url']}/chat/completions", json=body,
                                     headers={"Authorization": f"Bearer {GW['key']}"},
                                     timeout=httpx.Timeout(read_timeout, connect=15)) as r:
                if r.status_code != 200:
                    await r.aread()
                    return {**out, "kind": f"http_{r.status_code}", "s": time.perf_counter() - t0}
                text, calls, usage = "", 0, None
                it = r.aiter_lines()
                while True:
                    try:
                        wait = ttfb_timeout if (ttfb_timeout and out["ttfb"] is None) else read_timeout
                        line = await asyncio.wait_for(it.__anext__(), wait)
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        kind = "timeout" if out["ttfb"] is None else "cut"
                        return {**out, "kind": kind, "s": time.perf_counter() - t0}
                    if out["ttfb"] is None:
                        out["ttfb"] = time.perf_counter() - t0
                    if not line.startswith("data: ") or line.endswith("[DONE]"):
                        continue
                    d = json.loads(line[6:])
                    usage = d.get("usage") or usage
                    for c in d.get("choices") or []:
                        dl = c.get("delta") or {}
                        text += dl.get("content") or ""
                        calls += 1 if dl.get("tool_calls") else 0
        else:
            coro = client.post(f"{GW['base_url']}/chat/completions", json=body,
                               headers={"Authorization": f"Bearer {GW['key']}"},
                               timeout=httpx.Timeout(read_timeout, connect=15))
            r = await (asyncio.wait_for(coro, ttfb_timeout) if ttfb_timeout else coro)
            out["ttfb"] = time.perf_counter() - t0
            if r.status_code != 200:
                return {**out, "kind": f"http_{r.status_code}", "s": time.perf_counter() - t0}
            d = r.json()
            m = (d.get("choices") or [{}])[0].get("message") or {}
            text, calls, usage = m.get("content") or "", len(m.get("tool_calls") or []), d.get("usage")
    except asyncio.TimeoutError:
        return {**out, "kind": "timeout", "s": time.perf_counter() - t0}
    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.PoolTimeout):
        return {**out, "kind": "timeout" if out["ttfb"] is None else "cut", "s": time.perf_counter() - t0}
    except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as exc:
        return {**out, "kind": "cut" if out["ttfb"] else "conn", "err": repr(exc)[:80], "s": time.perf_counter() - t0}
    u = usage or {}
    return {**out, "kind": classify(text, calls, usage, body), "s": time.perf_counter() - t0,
            "in": u.get("prompt_tokens"), "cached": (u.get("prompt_tokens_details") or {}).get("cached_tokens"),
            "out": u.get("completion_tokens")}


def body_for(size: str, salt: int, *, stream: bool = True, **extra) -> dict:
    b = {"model": GW["model"], "messages": make_messages(size, salt), "tools": TOOLS, "stream": stream}
    if stream:
        b["stream_options"] = {"include_usage": True}
    b.update(extra)
    return {k: v for k, v in b.items() if v is not None}
