"""Request transforms for ablations, applied by the proxy (PROXY_TRANSFORM=a,b,...).

Each takes the request body (OpenAI chat format) and changes it in place. Only the agent's
main requests are touched (the ones with tools); titles and summaries pass as they are.
"""
from __future__ import annotations

import re

PARALLEL_NUDGE = (
    "\n\n<efficiency>\nEvery step resends the whole conversation, so steps are what costs. When you need several "
    "independent things — reading several files, a few searches, listing and reading — request them all in ONE "
    "step as parallel tool calls instead of one per step. Plan the next 2–4 reads before calling. Only chain calls "
    "when a later one depends on an earlier result.\n</efficiency>"
)
DONE_RULE = (
    "\n\n<finishing>\nWhen the task is done, stop: give a short answer and do not re-verify what you already "
    "verified.\n</finishing>"
)


def _system(body: dict) -> dict | None:
    msgs = body.get("messages") or []
    return msgs[0] if msgs and msgs[0].get("role") == "system" and isinstance(msgs[0].get("content"), str) else None


def _drop_sections(body: dict, names: list[str]) -> None:
    s = _system(body)
    if s:
        for n in names:
            s["content"] = re.sub(rf"\n?<{n}>.*?</{n}>\n?", "\n", s["content"], flags=re.S)


def parallel(body):
    s = _system(body)
    if s:
        s["content"] += PARALLEL_NUDGE


def finish(body):
    s = _system(body)
    if s:
        s["content"] += DONE_RULE


def noskills(body):
    _drop_sections(body, ["skills"])


def lean(body):
    """Keep only what the model cannot infer: environment, workspace, secrets, approvals, honesty."""
    _drop_sections(body, ["approach", "planning", "browser", "editing_code", "navigating_code", "verification",
                          "communication", "memory_and_learning", "web_and_external_content"])


def shorttools(body):
    """Tool descriptions cut to their first sentence; parameter descriptions to 80 chars."""
    for t in body.get("tools") or []:
        f = t.get("function") or {}
        d = f.get("description") or ""
        m = re.match(r"(.+?[.!?])(\s|$)", d, re.S)
        f["description"] = (m.group(1) if m else d)[:200]
        for p in ((f.get("parameters") or {}).get("properties") or {}).values():
            if isinstance(p, dict) and len(p.get("description", "")) > 80:
                p["description"] = p["description"][:80].rsplit(" ", 1)[0]


_NUM = re.compile(r"^ *(\d+) \| ", re.M)


def _tool_outputs(body):
    for m in body.get("messages") or []:
        if m.get("role") == "tool" and isinstance(m.get("content"), str) and " | " in m["content"]:
            yield m


def numfmt(body):
    """read_file lines as '12|text' instead of '    12 | text'."""
    for m in _tool_outputs(body):
        m["content"] = _NUM.sub(lambda x: x.group(1) + "|", m["content"])


def nonum(body):
    """read_file lines without numbers (the header still gives the line range)."""
    for m in _tool_outputs(body):
        m["content"] = _NUM.sub("", m["content"])


_FAIL = re.compile(r"(\d+ failed|FAILED|Traceback \(most recent|AssertionError|Error:|exit code [1-9])")


def _effort(body, level):
    body["reasoning"] = {"effort": level}


def plan_high(body):
    """Think hard on the first step (the plan), then low."""
    first = not any(m.get("role") == "assistant" for m in body.get("messages") or [])
    _effort(body, "high" if first else "low")


def escalate(body):
    """Low, but high right after a failing test run or an error in the latest tool outputs."""
    msgs = body.get("messages") or []
    recent = []
    for m in reversed(msgs):
        if m.get("role") != "tool":
            break
        recent.append(str(m.get("content") or ""))
    _effort(body, "high" if any(_FAIL.search(t) for t in recent) else "low")


ALL = {"plan_high": plan_high, "escalate": escalate, "numfmt": numfmt, "nonum": nonum, "parallel": parallel, "finish": finish, "noskills": noskills, "lean": lean, "shorttools": shorttools}


def apply(body: dict, names: list[str]) -> None:
    if not body.get("tools"):
        if {"plan_high", "escalate"} & set(names):
            _effort(body, "low")  # titles and summaries
        return
    for n in names:
        ALL[n](body)
