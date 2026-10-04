"""Experiment 4: exact token weight (the provider's own count) of each part of a request."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "pc"))
import ctx  # noqa: E402
from prov import chat, usage, text_of  # noqa: E402

PING = [{"role": "user", "content": "hi"}]


def tokens(messages, tools=None):
    return usage(chat(messages, tools=tools, max_tokens=1, only=1, extra={"reasoning": {"effort": "low"}}))["in"]


base = tokens(PING)
SYS = ctx.system_prompt()
print("empty request:", base)
print("system prompt:", tokens([{"role": "system", "content": SYS}] + PING) - base, "tokens,", len(SYS), "chars")
for m in re.finditer(r"<([a-z_]+)>(.*?)</\1>", SYS, re.S):
    if len(m.group(2)) > 900:
        t = tokens([{"role": "system", "content": m.group(0)}] + PING) - base
        print(f"  <{m.group(1)}> {t} tokens, {len(m.group(2))} chars")
core_t = tokens(PING, ctx.tools()) - base
all_t = tokens(PING, ctx.REG.schemas()) - base
print(f"core tools ({len(ctx.CORE)}): {core_t} tokens; all tools ({len(ctx.REG.names())}): {all_t} tokens")
heavy = []
for name in ctx.CORE:
    heavy.append((tokens(PING, ctx.REG.schemas([name])) - base, name))
print("per core tool:", sorted(heavy, reverse=True)[:8])

skills = re.search(r"<skills>(.*?)</skills>", SYS, re.S).group(1)
en = text_of(chat([{"role": "user", "content": "Translate to English, keep the structure, names and paths "
                    "exactly, output only the translation:\n\n" + skills}], max_tokens=4000))
ru_t = tokens([{"role": "system", "content": skills}] + PING) - base
en_t = tokens([{"role": "system", "content": en}] + PING) - base
print(f"skills list RU: {ru_t} tokens; EN: {en_t} tokens ({(ru_t - en_t) / ru_t:.0%} less)")
