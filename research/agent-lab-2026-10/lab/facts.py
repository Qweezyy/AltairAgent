"""Every number the three articles use, recomputed from the saved run results (data/).

Cost: what OpenRouter reported per request when it did; for GateYourWay (no cost in its
answers) the OpenRouter price of the same model: input $0.15/M, cached input $0.03/M, output $0.50/M.
"""
import glob
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


def rows(folder):
    out = []
    for f in glob.glob(str(DATA / folder / "*.jsonl")):
        out += [json.loads(l) for l in open(f, encoding="utf-8")]
    return out


def looped(r):
    log = Path(r.get("dir", "")) / "backend.log"
    try:
        return "превысил" in log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return bool(r.get("_looped"))


def cost(r):
    if r.get("cost"):
        return r["cost"]
    unc = (r["in"] or 0) - (r["cached"] or 0)
    return unc * 0.15e-6 + (r["cached"] or 0) * 0.03e-6 + (r["out"] or 0) * 0.5e-6


def summary(rs):
    n = len(rs)
    return {
        "runs": n,
        "passed": sum(bool(r.get("passed")) for r in rs),
        "usd_per_run": round(st.mean(cost(r) for r in rs), 5),
        "minutes_per_run": round(st.mean((r.get("seconds") or 900) for r in rs) / 60, 2),
        "steps_per_run": round(st.mean(r["requests"] for r in rs), 1),
        "input_tokens_per_run": round(st.mean(r["in"] for r in rs)),
        "output_tokens_per_run": round(st.mean(r["out"] for r in rs)),
        "reasoning_tokens_per_run": round(st.mean(r["reasoning"] for r in rs)),
        "cache_share": round(sum(r["cached"] for r in rs) / max(1, sum(r["in"] for r in rs)), 3),
        "runs_with_loops": sum(looped(r) for r in rs),
    }


def by_config(folder, tasks=None):
    g = defaultdict(list)
    for r in rows(folder):
        if tasks is None or r["task"] in tasks:
            g[r["config"]].append(r)
    return {c: summary(rs) for c, rs in sorted(g.items())}


HARD = {"h_cache", "h_conf", "h_rename", "h_logs", "h_tokens", "h_bizdays"}
facts = {
    "a1_reasoning_openrouter_wave2": by_config("results2", HARD),
    "a1_adaptive_openrouter_wave3": by_config("r_eff"),
    "a1_long_tasks": by_config("r_long"),
    "a1_gateyourway_before": by_config("results_gw"),
    "a1_gateyourway_after_shipping": by_config("r_after"),
    "a3_prompt_ablations_low": by_config("results3"),
    "a2_reviewer": {},
}
# loops per task in wave 2 (default level)
w2 = rows("results2")
facts["a1_loops_by_task_default"] = {t: sum(looped(r) for r in w2 if r["task"] == t and r["config"] in
                                         ("base", "combo", "en", "finish", "lean", "nonum", "noskills", "numfmt", "parallel", "shorttools"))
                                     for t in sorted(HARD)}
# article 2: false "done" in wave 1
w1 = rows("results")


def fake_reason(r):
    a = str((r.get("cli") or {}).get("result") or "")
    if "could not be completed" in a:
        return "gateway_text"
    if "не вернула текстовый" in a:
        return "empty_stream"
    return None


facts["a2_wave1"] = {
    "runs": len(w1),
    "false_done": sum(1 for r in w1 if fake_reason(r)),
    "by_reason": {k: sum(1 for r in w1 if fake_reason(r) == k) for k in ("gateway_text", "empty_stream")},
    "model_failures": sum(1 for r in w1 if not r.get("passed") and not fake_reason(r)),
}
w2_cut = [r for r in w2 if looped(r) and r["config"] not in ("eff_low", "eff_medium", "eff_high")]
facts["a2_wave2_loop_cut_as_done"] = {"runs_cut": len(w2_cut),
                                      "of_runs_without_level": sum(1 for r in w2 if r["config"] not in ("eff_low", "eff_medium", "eff_high"))}
for name in ("verify_low", "verify_high"):
    rs = [r for r in rows("r_ver") if r["config"] == name]
    flagged = [r for r in rs if r.get("review_ok") is False]
    facts["a2_reviewer"][name] = {
        "runs": len(rs), "passed_before": sum(bool(r.get("passed_before_review")) for r in rs),
        "passed_after": sum(bool(r.get("passed")) for r in rs), "flagged": len(flagged),
        "false_alarms": sum(1 for r in flagged if r.get("passed_before_review")),
        "missed": sum(1 for r in rs if r.get("review_ok") and not r.get("passed_before_review")),
        "usd_per_run": round(st.mean(cost(r) for r in rs), 5),
    }
facts["a2_reviewer"]["agent_alone_low"] = facts["a1_adaptive_openrouter_wave3"].get("eff_low")
# masking experiment (article 3 / 2)
mask = json.load(open(DATA / "exp3_result.json", encoding="utf-8"))
facts["a3_masking"] = {k: v for k, v in mask.items() if k != "wrong_examples"}
facts["a3_masking_wrong_were_gateway"] = all("could not be completed" in a for v in mask["wrong_examples"].values() for _, a in v)
# recall under tight budget (article 3)
rec = [r for r in w1 if r["task"] == "recall"]
facts["a3_recall_budget"] = {c: summary([r for r in rec if r["config"] == c]) for c in ("base", "tight")}
# deferral vs all tools (wave 1 easy tasks)
easy = {"bugfix", "codeq", "data", "build", "long"}
facts["a3_alltools_vs_deferred"] = {c: summary([r for r in w1 if r["config"] == c and r["task"] in easy and not fake_reason(r)])
                                    for c in ("base", "alltools")}
# soak (provider stability, article 2)
soak = [json.loads(l) for l in open(DATA / "gw_soak.jsonl", encoding="utf-8")]
facts["a2_soak"] = {"requests": len(soak), "succeeded": sum(r["ok"] for r in soak),
                    "needed_retry": sum(1 for r in soak if r["kinds"][:1] != ["ok"]),
                    "hours": round((soak[-1]["t"] - soak[0]["t"]) / 3600, 1)}
out = DATA / "facts.json"
out.write_text(json.dumps(facts, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(facts, ensure_ascii=False, indent=1))
