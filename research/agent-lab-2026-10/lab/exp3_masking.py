"""Experiment 3: what the model does with masked tool outputs.

The agent has read 6 files; then the outputs are masked in different ways and it is asked a
question whose answer was in a masked output. Outcomes per answer:
  correct  — answered right without tools
  tool     — asked for a tool (re-read / restore): costs a step but is honest
  wrong    — answered wrongly (the costly case: a confident mistake)
"""
import copy
import json
import re
import sys
from pathlib import Path
from collections import Counter, defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "pc"))
import ctx  # noqa: E402
from prov import chat, usage  # noqa: E402

SYS = ctx.system_prompt()
HIST = ctx.history(6)
QUESTIONS = [
    ("In core/updater.py, what is the largest update package size the app accepts, in megabytes?", r"\b500\b"),
    ("In core/agent/session.py, how many characters per token does the rough token estimate assume?", r"\b3\b"),
    ("In core/agent/session.py, how many tokens is one image/video/audio attachment estimated at?", r"1[ ,.]?500"),
    ("In core/updater.py, which GitHub repository is the default update source?", r"Qweezyy/AltairAgent"),
    ("In core/search_backend.py, which two external search engines are tried, in which order?", r"tgrep.{0,80}(rg|ripgrep)"),
    ("In core/agent/session.py, how many newest page snapshots / tool screenshots are kept in full?", r"\b2\b"),
    ("In core/updater.py, what value of UPDATE_URL turns update checks off?", r"\boff\b"),
    ("In core/agent/session.py, name two tools whose outputs are never cleared.", r"(ask|read_skill|phone_ask_user|request_secret|tool_search).{0,60}(ask|read_skill|phone_ask_user|request_secret|tool_search)"),
]
TOOL_OUTPUT = {"type": "function", "function": {
    "name": "tool_output", "description": "Return the full original output of an earlier tool call whose output "
    "was cleared from the context (by its tool_call_id). Cheaper and more exact than running the tool again.",
    "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}}


def mask(msgs, style):
    out = copy.deepcopy(msgs)
    for m in out:
        if m.get("role") != "tool":
            continue
        size = len(m["content"])
        if style == "ours":
            m["content"] = f"[Earlier output of read_file ({size} chars) was cleared to save context. Run the tool again if you need it.]"
        elif style == "gist":
            lines = [l for l in m["content"].splitlines() if l.strip()]
            m["content"] = ("[Earlier output of read_file (" + str(size) + " chars) was cleared to save context. "
                            "It began with:\n" + "\n".join(lines[:3]) + "\n…]")
        elif style == "restore":
            m["content"] = (f"[Earlier output of read_file ({size} chars) was cleared to save context. "
                            f"tool_output(id=\"{m['tool_call_id']}\") returns it exactly.]")
    return out


def notes_before_masking(msgs):
    """V4: the model is warned and writes the key facts down; then the outputs are masked."""
    warn = msgs + [{"role": "user", "content": "[Context note] The outputs of the files you read above will be "
                    "cleared from your context now. Write down, briefly, the facts from them you may still need "
                    "(constants, names, values). No tools."}]
    res = chat([{"role": "system", "content": SYS}] + warn, max_tokens=1500)
    note = res["json"]["choices"][0]["message"].get("content") or ""
    return mask(msgs, "ours") + [{"role": "assistant", "content": note}], usage(res)


def ask(history, question, tools):
    msgs = [{"role": "system", "content": SYS}] + history + [{"role": "user", "content": question + " Answer briefly."}]
    res = chat(msgs, tools=tools, max_tokens=1200)
    m = res["json"]["choices"][0]["message"]
    return m, usage(res)


def outcome(m, pattern):
    if m.get("tool_calls"):
        return "tool:" + m["tool_calls"][0]["function"]["name"]
    text = m.get("content") or ""
    return "correct" if re.search(pattern, text, re.I | re.S) else "wrong"


def main(reps=2):
    core = ctx.tools()
    v4_hist, v4_cost = notes_before_masking(HIST)
    print("V4 note cost:", v4_cost, flush=True)
    variants = {
        "V0 no masking": (HIST, core),
        "V1 ours (run again)": (mask(HIST, "ours"), core),
        "V2 gist (first lines)": (mask(HIST, "gist"), core),
        "V3 restore tool": (mask(HIST, "restore"), core + [TOOL_OUTPUT]),
        "V4 notes then mask": (v4_hist, core),
    }
    table = defaultdict(Counter)
    tokens = defaultdict(int)
    wrong_examples = defaultdict(list)
    for name, (hist, tools) in variants.items():
        for q, pat in QUESTIONS:
            for _ in range(reps):
                try:
                    m, u = ask(hist, q, tools)
                except Exception as exc:  # noqa: BLE001
                    table[name]["error"] += 1
                    print("  error", str(exc)[:120])
                    continue
                o = outcome(m, pat)
                table[name][o.split(":")[0]] += 1
                if o.startswith("tool"):
                    table[name][o] += 1
                tokens[name] += u["in"]
                if o == "wrong":
                    wrong_examples[name].append((q[:50], (m.get("content") or "")[:120]))
        c = table[name]
        n = sum(v for k, v in c.items() if ":" not in k)
        print(f"{name:24} correct {c['correct']:2}/{n}  tool {c['tool']:2}  wrong {c['wrong']:2}  "
              f"in-tokens/q {tokens[name] // max(1, n):6}  tools used: "
              f"{ {k.split(':')[1]: v for k, v in c.items() if ':' in k} }", flush=True)
    json.dump({k: dict(v) for k, v in table.items()} | {"wrong_examples": wrong_examples},
              open("exp3_result.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2)
