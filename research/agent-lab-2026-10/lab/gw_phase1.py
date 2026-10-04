"""Phase 1+2: failure profile by size and mode, then request-parameter ablation. One at a time."""
import asyncio
import json
import random
import sys
import time
from collections import Counter, defaultdict

import httpx

from gw_probe import body_for, call

LOG = "gw_phase1.jsonl"


async def main(per: int):
    conds = []
    for size in ("small", "medium", "large"):
        for stream in (True, False):
            conds.append((f"{size}/{'stream' if stream else 'plain'}", dict(size=size, stream=stream)))
    # parameter ablation on the medium streaming request (what the app sends)
    conds += [
        ("medium/stream/no_stream_options", dict(size="medium", stream=True, stream_options=None)),
        ("medium/stream/tool_choice_auto", dict(size="medium", stream=True, tool_choice="auto")),
        ("medium/stream/max_tokens_8k", dict(size="medium", stream=True, max_tokens=8192)),
        ("medium/stream/thinking_off", dict(size="medium", stream=True, thinking={"type": "disabled"})),
    ]
    order = [c for c in conds for _ in range(per)]
    random.Random(1).shuffle(order)  # interleave so time-of-day effects spread evenly
    stats = defaultdict(Counter)
    times = defaultdict(list)
    async with httpx.AsyncClient() as client:
        for i, (name, kw) in enumerate(order):
            kw = dict(kw)
            size, stream = kw.pop("size"), kw.pop("stream")
            body = body_for(size, random.randint(0, 10**6), stream=stream, **kw)
            if kw.get("stream_options", 1) is None:
                body.pop("stream_options", None)
            r = await call(client, body, read_timeout=180)
            r.update(cond=name, t=time.time())
            stats[name][r["kind"]] += 1
            if r["kind"] == "ok":
                times[name].append(r["s"])
            with open(LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(r) + "\n")
            print(f"{i:4} {name:34} {r['kind']:10} {r['s']:6.1f}s ttfb={r.get('ttfb') or 0:5.1f} in={r.get('in')}", flush=True)
    print("\n=== summary")
    for name, _ in conds:
        c = stats[name]
        n = sum(c.values())
        ts = sorted(times[name])
        p = lambda q: ts[min(len(ts) - 1, int(q * len(ts)))] if ts else 0  # noqa: E731
        print(f"{name:34} ok {c['ok']:3}/{n}  fails {dict((k, v) for k, v in c.items() if k != 'ok')}  "
              f"ok-time p50 {p(.5):5.1f}s p90 {p(.9):5.1f}s")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 15))
