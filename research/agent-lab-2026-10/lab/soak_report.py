import json
from collections import defaultdict, Counter
rs = [json.loads(l) for l in open("gw_soak.jsonl", encoding="utf-8")]
print("records:", len(rs), "hours:", round((rs[-1]["t"] - rs[0]["t"]) / 3600, 1))
g = defaultdict(list)
for r in rs: g[(r["phase"], r["strategy"])].append(r)
for k, v in sorted(g.items()):
    ok = [r for r in v if r["ok"]]; ts = sorted(r["s"] for r in ok)
    p = lambda q: ts[min(len(ts)-1, int(q*len(ts)))] if ts else 0
    first_try = sum(1 for r in v if r["kinds"][:1] == ["ok"])
    print(f"{k[0]:6} {k[1]:9} success {len(ok):3}/{len(v):<3} first-try {first_try:3}  p50 {p(.5):5.1f}s p90 {p(.9):6.1f}s max {ts[-1] if ts else 0:5.0f}s  >60s {sum(1 for t in ts if t > 60)}  fails {dict(Counter(x for r in v for x in r['kinds'] if x != 'ok'))}")
