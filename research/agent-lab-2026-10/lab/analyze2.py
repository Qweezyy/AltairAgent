"""Wave 2 summary (OpenRouter-pinned): quality, cost (provider-reported), time, loops."""
import glob
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

LAB = Path(__file__).resolve().parent
DIR = sys.argv[1] if len(sys.argv) > 1 else "results2"
rows = []
for f in glob.glob(str(LAB / DIR / "*.jsonl")):
    rows += [json.loads(l) for l in open(f, encoding="utf-8")]


def loops(r):
    log = Path(r["dir"]) / "backend.log"
    return log.exists() and "превысил" in log.read_text(encoding="utf-8", errors="replace")


def cost(r):
    if r.get("cost"):
        return r["cost"]
    unc = (r["in"] or 0) - (r["cached"] or 0)
    return unc * 0.15e-6 + (r["cached"] or 0) * 0.03e-6 + (r["out"] or 0) * 0.5e-6


tasks = sorted({r["task"] for r in rows})
by = defaultdict(list)
for r in rows:
    by[r["config"]].append(r)
print(f"{'config':11} {'pass':>7} {'pass*':>7} {'$/run':>8} {'$/pass':>8} {'out':>7} {'reason':>7} {'in':>7} {'steps':>5} {'min':>5} loops  per-task pass")
for c, rs in sorted(by.items(), key=lambda x: x[0]):
    n = len(rs)
    p = sum(r.get("passed") for r in rs)
    nl = sum(loops(r) for r in rs)
    ok_rs = [r for r in rs if not loops(r)]
    p2 = sum(r.get("passed") for r in ok_rs)
    tot = sum(cost(r) for r in rs)
    per = " ".join(f"{t[2:6]}:{sum(r['passed'] for r in rs if r['task'] == t)}/{sum(1 for r in rs if r['task'] == t)}" for t in tasks)
    print(f"{c:11} {p:3}/{n:<3} {p2:3}/{len(ok_rs):<3} {tot / n * 1000:7.2f}m {tot / max(1, p) * 1000:7.2f}m "
          f"{st.mean(r['out'] for r in rs):7.0f} {st.mean(r['reasoning'] for r in rs):7.0f} {st.mean(r['in'] for r in rs):7.0f} "
          f"{st.mean(r['requests'] for r in rs):5.1f} {st.mean((r.get('seconds') or 900) for r in rs) / 60:5.1f} {nl:3}    {per}")
print("pass* = excluding runs where the model looped (stream cut at the char limit)")
