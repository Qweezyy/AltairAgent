"""Phase 3: client strategies against GateYourWay, live. One logical request = one agent step.

  now       the app today: up to 5 attempts, backoff 2/4/8/10 s, retries HTTP 408/429/5xx and
            timeouts/broken connections; a disguised error or an empty answer is ACCEPTED (= failure)
  detect    the same, but a disguised error or an empty answer is retried too
  watchdog  detect + give up an attempt whose first byte takes longer than W seconds
  hedge     detect + if the first byte has not come in H seconds, start a second copy; the first
            good answer wins, the other is cancelled
Then a concurrency scenario: 3 logical requests at once (an agent step, a title, another chat),
with and without a client-side limit of 2 requests in flight.
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
import time
from collections import Counter

import httpx

from gw_probe import body_for, call

LOG = "gw_phase3.jsonl"
RETRY_KINDS = {"timeout", "cut", "conn"} | {f"http_{c}" for c in (408, 429, 500, 502, 503, 504, 520, 521, 522, 524)}
BUDGET = 300.0  # a logical request that has not succeeded in 5 minutes counts as failed


async def strategy(client, body, name, *, watchdog=None, hedge=None, sem=None):
    t0 = time.perf_counter()
    attempts, kinds = 0, []
    for a in range(1, 6):
        attempts = a
        remaining = BUDGET - (time.perf_counter() - t0)
        if remaining <= 5:
            break
        if hedge:
            r = await hedged(client, body, hedge, sem, min(180, remaining))
        else:
            async with (sem or _null()):
                r = await call(client, body, read_timeout=min(120, remaining), ttfb_timeout=watchdog)
        kinds.append(r["kind"])
        if r["kind"] == "ok":
            return {"ok": True, "s": time.perf_counter() - t0, "attempts": a, "kinds": kinds}
        if name == "now" and r["kind"] in ("disguised", "empty"):
            break  # the app takes it as the answer
        if name == "now" and r["kind"] not in RETRY_KINDS:
            break
        await asyncio.sleep(min(2 ** a, 10) + random.uniform(0.5, 1.1))
    return {"ok": False, "s": time.perf_counter() - t0, "attempts": attempts, "kinds": kinds}


class _null:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *a):
        return False


async def hedged(client, body, delay, sem, timeout):
    async def one():
        async with (sem or _null()):
            return await call(client, body, read_timeout=timeout)

    first = asyncio.create_task(one())
    done, _ = await asyncio.wait({first}, timeout=delay)
    if done:
        return first.result()
    second = asyncio.create_task(one())
    pending = {first, second}
    result = None
    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            r = t.result()
            if r["kind"] == "ok":
                for p in pending:
                    p.cancel()
                r["hedged"] = True
                return r
            result = r
    return result


async def sequential(n, watchdog, hedge_after):
    names = ["now", "detect", "watchdog", "hedge"]
    order = [nm for nm in names for _ in range(n)]
    random.Random(3).shuffle(order)
    res = {nm: [] for nm in names}
    async with httpx.AsyncClient() as client:
        for i, nm in enumerate(order):
            body = body_for("medium", random.randint(0, 10**6))
            r = await strategy(client, body, nm, watchdog=watchdog if nm == "watchdog" else None,
                               hedge=hedge_after if nm == "hedge" else None)
            res[nm].append(r)
            with open(LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"phase": "seq", "strategy": nm, **r}) + "\n")
            print(f"{i:4} {nm:9} ok={r['ok']!s:5} {r['s']:6.1f}s attempts={r['attempts']} {r['kinds']}", flush=True)
    report(res)


async def concurrent(rounds, limit):
    """3 requests at once per round (agent step + title-sized + another chat), limit or not."""
    res = {"no_limit": [], f"limit_{limit}": []}
    async with httpx.AsyncClient() as client:
        for i in range(rounds):
            for label in res:
                sem = asyncio.Semaphore(limit) if label.startswith("limit") else None
                bodies = [body_for("medium", random.randint(0, 10**6)), body_for("small", random.randint(0, 10**6)),
                          body_for("medium", random.randint(0, 10**6))]
                rs = await asyncio.gather(*(strategy(client, b, "detect", sem=sem) for b in bodies))
                for r in rs:
                    res[label].append(r)
                    with open(LOG, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"phase": "conc", "strategy": label, **r}) + "\n")
                print(f"round {i} {label:9} " + " | ".join(f"{r['ok']} {r['s']:.0f}s {r['kinds']}" for r in rs), flush=True)
    report(res)


def report(res):
    print("\n=== summary")
    for nm, rs in res.items():
        if not rs:
            continue
        ok = [r for r in rs if r["ok"]]
        ts = sorted(r["s"] for r in ok)
        p = lambda q: ts[min(len(ts) - 1, int(q * len(ts)))] if ts else 0  # noqa: E731
        kinds = Counter(k for r in rs for k in r["kinds"] if k != "ok")
        print(f"{nm:10} success {len(ok):3}/{len(rs)}  time p50 {p(.5):5.1f}s p90 {p(.9):5.1f}s max {ts[-1] if ts else 0:5.0f}s  "
              f"attempts avg {sum(r['attempts'] for r in rs) / len(rs):.2f}  failed attempts {dict(kinds)}", flush=True)


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "seq":
        asyncio.run(sequential(int(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])))
    else:
        asyncio.run(concurrent(int(sys.argv[2]), int(sys.argv[3])))
