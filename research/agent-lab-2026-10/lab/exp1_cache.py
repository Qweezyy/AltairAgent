"""Experiment 1: what keeps and what breaks the provider's prompt cache."""
import copy
import json
import sys
from pathlib import Path
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "pc"))
import ctx  # noqa: E402
from prov import chat, usage  # noqa: E402

SYS = ctx.system_prompt()
CORE = list(ctx.CORE)
HIST = ctx.history()
EXTRA_TOOL = "git_status" if "git_status" in ctx.REG.names() else sorted(set(ctx.REG.names()) - set(CORE))[0]


def req(msgs, names, label, prov):
    res = chat(msgs, tools=ctx.tools(names), max_tokens=64, only=prov)
    u = usage(res)
    rate = u["cached"] / u["in"] if u["in"] else 0
    print(f"  {label:44} in={u['in']:6} cached={u['cached']:6} ({rate:4.0%}) {res['seconds']:5.1f}s", flush=True)
    return u


def run(prov):
    base = [{"role": "system", "content": SYS}] + HIST
    print(f"provider {prov}: extra tool = {EXTRA_TOOL}")
    req(base, CORE, "1 cold", prov)
    time.sleep(2)
    req(base, CORE, "2 same again", prov)
    grown = base + [{"role": "assistant", "content": "Now the next file."},
                    {"role": "user", "content": "Go on."}]
    req(grown, CORE, "3 history grew (append)", prov)
    req(grown, CORE + [EXTRA_TOOL], "4 tool appended at the END of tools", prov)
    req(grown, sorted(CORE + [EXTRA_TOOL]), "5 tool inserted in name order (ours)", prov)
    req(grown, sorted(CORE + [EXTRA_TOOL]), "5b same again", prov)
    masked = copy.deepcopy(grown)
    masked[4]["content"] = "[Earlier output of read_file (12000 chars) was cleared to save context.]"
    req(masked, sorted(CORE + [EXTRA_TOOL]), "6 old tool output masked (mid-history)", prov)
    req(masked, sorted(CORE + [EXTRA_TOOL]), "6b same again", prov)
    noted = masked + [{"role": "system", "content": "The user stopped this task. Continue from here."}]
    req(noted, sorted(CORE + [EXTRA_TOOL]), "7 a system note appended at the end", prov)


for p in (0, 1):
    try:
        run(p)
    except Exception as exc:  # noqa: BLE001
        print("  provider failed:", str(exc)[:200])
