"""Summary of the stand runs: per config and per task, plus suspicious answers."""
import glob
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

LAB = Path(__file__).resolve().parent
rows = []
for f in glob.glob(str(LAB / "results" / "*.jsonl")):
    rows += [json.loads(l) for l in open(f, encoding="utf-8")]


def agg(rs):
    n = len(rs)
    if not n:
        return ""
    passed = sum(1 for r in rs if r.get("passed"))
    m = lambda k: st.mean(r.get(k) or 0 for r in rs)  # noqa: E731
    cached = sum(r.get("cached") or 0 for r in rs) / max(1, sum(r.get("in") or 0 for r in rs))
    return (f"pass {passed}/{n}  req {m('requests'):4.1f}  in {m('in'):8.0f}  cached {cached:4.0%}  out {m('out'):6.0f}"
            f"  reason {m('reasoning'):5.0f}  {m('seconds'):5.0f}s  toolsets {m('tool_sets'):3.1f}  fb {sum(r.get('fallbacks') or 0 for r in rs)}")


by_cfg = defaultdict(list)
by_cfg_task = defaultdict(list)
for r in rows:
    by_cfg[r["config"]].append(r)
    by_cfg_task[(r["config"], r["task"])].append(r)
print("=== per config")
for c, rs in sorted(by_cfg.items()):
    print(f"{c:9} {agg(rs)}")
print("=== per config × task")
for (c, t), rs in sorted(by_cfg_task.items(), key=lambda x: (x[0][1], x[0][0])):
    print(f"{t:7} {c:9} {agg(rs)}")

# Requests the provider did not really process: far fewer prompt tokens than the request's size.
print("=== suspicious answers (prompt tokens < 60% of chars/4.5)")
sus = 0
total = 0
for f in glob.glob(str(LAB / "runs" / "*" / "*" / "requests.jsonl")):
    for r in map(json.loads, open(f, encoding="utf-8")):
        if not r.get("n_tools"):
            continue
        total += 1
        expect = (r["chars"] + r["tools_chars"]) / 4.5
        if r.get("in") and r["in"] < 0.6 * expect:
            sus += 1
            print(f"  {Path(f).parent.name}: in={r['in']} expected≈{expect:.0f} out={r['out']} provider={r.get('provider')}")
print(f"  {sus} of {total} agent requests")
fails = [r for r in rows if not r.get("passed")]
print("=== failed runs")
for r in fails:
    print(f"  {r['config']} {r['task']}#{r['rep']}: {str(r.get('check'))[:100]} | answer: {str((r.get('cli') or {}).get('result'))[:140]!r}")
