"""Experiment 6: provider reliability with an agent-sized request, and whether a retry helps.

For each provider, N requests (system prompt + core tools + a ~25K-token history, streaming like
the app). An answer counts as:
  ok        — a real answer (text or tool call) and prompt tokens >= 60% of the expected size
  disguised — HTTP 200, but the prompt was not processed (prompt tokens far too low) or the
              text is a gateway error message
  empty     — the stream ended with no text and no tool call
  http      — a non-200 status / connection error
After a bad answer the same request is retried once at once: does the retry come back ok?
"""
import json
import random
import statistics as st
import sys
from pathlib import Path
import time

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "pc"))
import ctx  # noqa: E402
from prov import PROVIDERS  # noqa: E402

SYS = ctx.system_prompt()
TOOLS = ctx.tools()
GATEWAY_PHRASES = ("could not be completed", "retry later", "rate limit", "upstream", "overloaded", "service unavailable")


def history(seed):
    h = ctx.history(4, 9000)
    h.append({"role": "user", "content": f"(run {seed}) Which of these modules looks riskiest? One sentence, or read one more file."})
    return h


def once(prov, msgs):
    body = {"model": prov["model"], "messages": msgs, "tools": TOOLS, "stream": True,
            "stream_options": {"include_usage": True}}
    t0 = time.perf_counter()
    ttfb = None
    text, calls, usage = "", 0, None
    try:
        with httpx.stream("POST", f"{prov['base_url']}/chat/completions", json=body, timeout=httpx.Timeout(240, connect=20),
                          headers={"Authorization": f"Bearer {prov['key']}"}) as r:
            if r.status_code != 200:
                return {"kind": "http", "status": r.status_code, "s": time.perf_counter() - t0}
            for line in r.iter_lines():
                if not line.startswith("data: ") or line.endswith("[DONE]"):
                    continue
                if ttfb is None:
                    ttfb = time.perf_counter() - t0
                d = json.loads(line[6:])
                usage = d.get("usage") or usage
                for c in d.get("choices") or []:
                    dl = c.get("delta") or {}
                    text += dl.get("content") or ""
                    calls += len(dl.get("tool_calls") or []) and 1
    except Exception as exc:  # noqa: BLE001
        return {"kind": "http", "status": repr(exc)[:60], "s": time.perf_counter() - t0}
    u = usage or {}
    pin = u.get("prompt_tokens") or 0
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    expected = (len(json.dumps(msgs, ensure_ascii=False)) + len(json.dumps(TOOLS, ensure_ascii=False))) / 4.5
    low = text.lower()
    if pin and pin < 0.6 * expected or (not calls and any(p in low for p in GATEWAY_PHRASES)):
        kind = "disguised"
    elif not text.strip() and not calls:
        kind = "empty"
    else:
        kind = "ok"
    return {"kind": kind, "s": time.perf_counter() - t0, "ttfb": ttfb, "in": pin, "cached": cached,
            "no_usage": usage is None}


def run(idx, n):
    prov = PROVIDERS[idx]
    rows, retries = [], []
    for i in range(n):
        msgs = [{"role": "system", "content": SYS}] + history(random.randint(0, 3))
        r = once(prov, msgs)
        rows.append(r)
        if r["kind"] != "ok":
            retries.append(once(prov, msgs)["kind"])
        print(f"  [{prov['name']}] {i:3} {r['kind']:9} {r['s']:6.1f}s in={r.get('in')} cached={r.get('cached')}", flush=True)
    kinds = {k: sum(1 for r in rows if r["kind"] == k) for k in ("ok", "disguised", "empty", "http")}
    s = sorted(r["s"] for r in rows)
    tt = sorted(r["ttfb"] for r in rows if r.get("ttfb"))
    q = lambda xs, p: xs[min(len(xs) - 1, int(p * len(xs)))] if xs else 0  # noqa: E731
    cache = sum(r.get("cached") or 0 for r in rows) / max(1, sum(r.get("in") or 0 for r in rows))
    print(f"== {prov['name']}: {kinds}  retry ok {sum(1 for k in retries if k == 'ok')}/{len(retries)}  "
          f"time p50 {q(s, .5):.1f}s p90 {q(s, .9):.1f}s max {s[-1]:.0f}s  ttfb p50 {q(tt, .5):.1f}s p90 {q(tt, .9):.1f}s  "
          f"cache {cache:.0%}  no-usage {sum(1 for r in rows if r.get('no_usage'))}", flush=True)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    for idx in (0, 1):
        run(idx, n)
