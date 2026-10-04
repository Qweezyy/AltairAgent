from pathlib import Path

p = Path(__file__).resolve().parent / "stand.py"
s = p.read_text(encoding="utf-8")


def rep(a, b):
    global s
    assert s.count(a) == 1, a[:60]
    s = s.replace(a, b, 1)


rep("import hardtasks  # noqa: E402,F401  (registers the hard tasks)\n",
    "import hardtasks  # noqa: E402,F401  (registers the hard tasks)\nimport longtasks  # noqa: E402,F401\n")
rep('''for _e in ("low", "medium", "high"):
    CONFIGS["eff_" + _e] = ({}, {"1": {"reasoning": {"effort": _e}}}, {})''',
    '''for _e in ("low", "medium", "high"):
    CONFIGS["eff_" + _e] = ({}, {"1": {"reasoning": {"effort": _e}}}, {})
CONFIGS["plan_high"] = ({}, {}, {"PROXY_TRANSFORM": "plan_high"})
CONFIGS["escalate"] = ({}, {}, {"PROXY_TRANSFORM": "escalate"})
# A reviewer after "done": (settings, extra, proxy env, review effort)
CONFIGS["verify_low"] = ({}, {"1": {"reasoning": {"effort": "low"}}}, {}, "low")
CONFIGS["verify_high"] = ({}, {"1": {"reasoning": {"effort": "low"}}}, {}, "high")

REVIEW_PROMPT = """You are a strict reviewer. A coding agent was given this task:

<task>
{task}
</task>

These are the files in its workspace now:

{files}

Check the work against EVERY requirement of the task (edge cases, error handling, exact formats, names).
Do not run anything; reason from the code. If everything is met, answer exactly: OK
Otherwise list each concrete violation on its own line, starting with "- ", saying what input or case fails and why.
Do not suggest style changes."""


def workspace_text(ws, limit=60000):
    parts = []
    for f in sorted(ws.rglob("*")):
        if f.is_file() and f.suffix in (".py", ".md", ".csv", ".txt", ".json") and "__pycache__" not in f.parts \\
                and f.stat().st_size < 40000 and not f.name.startswith("sales") and f.name != "requests.log":
            parts.append(f"--- {f.relative_to(ws).as_posix()} ---\\n" + f.read_text(encoding="utf-8", errors="replace"))
    text = "\\n\\n".join(parts)
    return text[:limit]


def review(task, ws, pport, effort):
    body = {"model": "glm-5.3-flash", "max_tokens": 20000, "reasoning": {"effort": effort},
            "messages": [{"role": "user", "content": REVIEW_PROMPT.format(task=task.prompt, files=workspace_text(ws))}]}
    r = httpx.post(f"http://127.0.0.1:{pport}/v1/chat/completions", json=body, timeout=600)
    data = r.json()
    return (data["choices"][0]["message"].get("content") or "").strip()''')
rep('''    settings_env, extra, *rest = CONFIGS[config]
    proxy_env = rest[0] if rest else {}''', '''    settings_env, extra, *rest = CONFIGS[config]
    proxy_env = rest[0] if rest else {}
    review_effort = rest[1] if len(rest) > 1 else None''')
rep('''                rec["passed"], rec["check"] = task.check(ws, answer)
            except subprocess.TimeoutExpired:''', '''                rec["passed"], rec["check"] = task.check(ws, answer)
                if review_effort:
                    rec["passed_before_review"] = rec["passed"]
                    verdict = review(task, ws, pport, review_effort)
                    rec["review"] = verdict[:600]
                    rec["review_ok"] = verdict.strip().upper().startswith("OK")
                    if not rec["review_ok"]:
                        fix = subprocess.run([sys.executable, "altair_cli.py", "-p", "-c", "--output-format", "json",
                                              "--mode", "bypass", "A reviewer checked your work against the task and "
                                              "reports these problems:\\n" + verdict[:6000] + "\\nCheck each one; fix the "
                                              "real ones (ignore any that are wrong) and run the tests again."],
                                             cwd=PC, env=env, capture_output=True, text=True, encoding="utf-8",
                                             errors="replace", timeout=task.timeout)
                        rec["passed"], rec["check"] = task.check(ws, "")
                    rec["seconds"] = round(time.time() - started, 1)
            except subprocess.TimeoutExpired:''')
p.write_text(s, encoding="utf-8")
print("ok")
