"""A test stand: proxy + Altair backend in a temp app folder; tasks run through `altair -p`.

python stand.py CONFIG REPS TASK[,TASK...]   → results/<config>.jsonl (one line per run)
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

LAB = Path(__file__).resolve().parent
PC = Path(__file__).resolve().parents[3] / "pc"
sys.path.insert(0, str(LAB))
import tasks as T  # noqa: E402
import hardtasks  # noqa: E402,F401  (registers the hard tasks)
import longtasks  # noqa: E402,F401

CONFIGS = {
    # name: (settings env, proxy extra by provider)
    "base": ({}, {}),
    "lowthink": ({}, {"0": {"thinking": {"type": "disabled"}}, "1": {"reasoning": {"effort": "low"}}}),
    "alltools": ({"TOOL_SEARCH": "false"}, {}),
    "noclear": ({"TOOL_RESULT_CLEARING": "false"}, {}),
    "tight": ({"CONTEXT_TOKEN_BUDGET": "40000"}, {}),
}
# Ablations through the proxy: name -> (settings env, extra by provider, proxy env)
for _name, _penv in {
    "parallel": {"PROXY_TRANSFORM": "parallel"},
    "finish": {"PROXY_TRANSFORM": "finish"},
    "noskills": {"PROXY_TRANSFORM": "noskills"},
    "lean": {"PROXY_TRANSFORM": "lean"},
    "shorttools": {"PROXY_TRANSFORM": "shorttools"},
    "openrouter": {"PROXY_ORDER": "1,0"},
    "combo": {"PROXY_TRANSFORM": "parallel,noskills,shorttools"},
    "numfmt": {"PROXY_TRANSFORM": "numfmt"},
    "nonum": {"PROXY_TRANSFORM": "nonum"},
}.items():
    CONFIGS[_name] = ({}, {}, _penv)
CONFIGS["en"] = ({"AGENT_LANGUAGE": "English"}, {}, {})
for _e in ("low", "medium", "high"):
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
        if f.is_file() and f.suffix in (".py", ".md", ".csv", ".txt", ".json") and "__pycache__" not in f.parts \
                and f.stat().st_size < 40000 and not f.name.startswith("sales") and f.name != "requests.log":
            parts.append(f"--- {f.relative_to(ws).as_posix()} ---\n" + f.read_text(encoding="utf-8", errors="replace"))
    text = "\n\n".join(parts)
    return text[:limit]


def review(task, ws, pport, effort):
    body = {"model": "glm-5.3-flash", "max_tokens": 20000, "reasoning": {"effort": effort},
            "messages": [{"role": "user", "content": REVIEW_PROMPT.format(task=task.prompt, files=workspace_text(ws))}]}
    r = httpx.post(f"http://127.0.0.1:{pport}/v1/chat/completions", json=body, timeout=600)
    data = r.json()
    return (data["choices"][0]["message"].get("content") or "").strip()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, seconds: float = 90) -> None:
    end = time.time() + seconds
    while time.time() < end:
        try:
            if httpx.get(url, timeout=3).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"not up: {url}")


def run(config: str, reps: int, task_names: list[str]) -> None:
    if config.endswith("+low") and config not in CONFIGS:
        se, ex, *rs = CONFIGS[config[:-4]]
        CONFIGS[config] = (se, {**ex, "1": {"reasoning": {"effort": "low"}}}, *(rs or [{}]))
    settings_env, extra, *rest = CONFIGS[config]
    proxy_env = rest[0] if rest else {}
    review_effort = rest[1] if len(rest) > 1 else None
    root = LAB / "runs" / config
    root.mkdir(parents=True, exist_ok=True)
    out_file = LAB / os.environ.get("RESULTS_DIR", "results") / f"{config}.jsonl"
    out_file.parent.mkdir(exist_ok=True)
    for rep in range(reps):
        for name in task_names:
            task = T.TASKS[name]
            run_dir = root / f"{name}-{rep}-{int(time.time())}"
            ws, app = run_dir / "ws", run_dir / "app"
            ws.mkdir(parents=True)
            app.mkdir()
            task.setup(ws)
            plog = run_dir / "requests.jsonl"
            pport, bport = free_port(), free_port()
            penv = dict(os.environ, PROXY_EXTRA_BY_PROV=json.dumps(extra), PYTHONPATH=str(LAB), **proxy_env)
            if os.environ.get("FORCE_ORDER"):
                penv["PROXY_ORDER"] = os.environ["FORCE_ORDER"]
            proxy = subprocess.Popen([sys.executable, str(LAB / "proxy.py"), str(pport), str(plog)],
                                     cwd=LAB, env=penv, stdout=subprocess.DEVNULL, stderr=open(run_dir / "proxy.err", "w"))
            env = dict(os.environ, APP_PATH=str(app), WORKSPACE_PATH=str(ws), BRIDGE_LAN="false",
                       LLM_BASE_URL=f"http://127.0.0.1:{pport}/v1", LLM_API_KEY="lab", DEFAULT_MODEL="glm-5.3-flash",
                       MODEL_ROUTING="false", APPROVAL_MODE="bypass", UPDATE_URL="off", PYTHONIOENCODING="utf-8",
                       **settings_env)
            backend = subprocess.Popen([sys.executable, "main.py", "--server", "--host", "127.0.0.1", "--port", str(bport)],
                                       cwd=PC, env=env, stdout=open(run_dir / "backend.log", "w"), stderr=subprocess.STDOUT)
            rec = {"config": config, "task": name, "rep": rep, "dir": str(run_dir)}
            try:
                wait_http(f"http://127.0.0.1:{pport}/v1/models")
                wait_http(f"http://127.0.0.1:{bport}/api/health")
                started = time.time()
                cli = subprocess.run([sys.executable, "altair_cli.py", "-p", "--output-format", "json", "--mode", "bypass",
                                      "--cwd", str(ws), task.prompt], cwd=PC, env=env, capture_output=True,
                                     text=True, encoding="utf-8", errors="replace", timeout=task.timeout)
                rec["seconds"] = round(time.time() - started, 1)
                rec["exit"] = cli.returncode
                try:
                    rec["cli"] = json.loads(cli.stdout.strip().splitlines()[-1])
                except (ValueError, IndexError):
                    rec["cli_raw"] = (cli.stdout[-800:], cli.stderr[-800:])
                answer = (rec.get("cli") or {}).get("result") or (rec.get("cli") or {}).get("answer") or ""
                rec["passed"], rec["check"] = task.check(ws, answer)
                if review_effort:
                    rec["passed_before_review"] = rec["passed"]
                    verdict = review(task, ws, pport, review_effort)
                    rec["review"] = verdict[:600]
                    rec["review_ok"] = verdict.strip().upper().startswith("OK")
                    if not rec["review_ok"]:
                        fix = subprocess.run([sys.executable, "altair_cli.py", "-p", "-c", "--output-format", "json",
                                              "--mode", "bypass", "A reviewer checked your work against the task and "
                                              "reports these problems:\n" + verdict[:6000] + "\nCheck each one; fix the "
                                              "real ones (ignore any that are wrong) and run the tests again."],
                                             cwd=PC, env=env, capture_output=True, text=True, encoding="utf-8",
                                             errors="replace", timeout=task.timeout)
                        rec["passed"], rec["check"] = task.check(ws, "")
                    rec["seconds"] = round(time.time() - started, 1)
            except subprocess.TimeoutExpired:
                rec["timeout"] = True
                rec["passed"], rec["check"] = task.check(ws, "")
            except Exception as exc:  # noqa: BLE001
                rec["error"] = repr(exc)[:300]
                rec["passed"] = False
            finally:
                backend.terminate()
                time.sleep(1.5)
                proxy.terminate()
                for p in (backend, proxy):
                    try:
                        p.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        p.kill()
            reqs = [json.loads(l) for l in plog.read_text(encoding="utf-8").splitlines()] if plog.exists() else []
            rec["requests"] = len(reqs)
            for k in ("in", "cached", "out", "reasoning"):
                rec[k] = sum(r.get(k) or 0 for r in reqs)
            rec["cost"] = round(sum(r.get("cost") or 0 for r in reqs), 6)
            rec["providers"] = sorted({r.get("provider") or "FAILED" for r in reqs})
            rec["fallbacks"] = sum(1 for r in reqs if r.get("errors"))
            rec["tool_sets"] = len({r.get("tools_hash") for r in reqs if r.get("n_tools")})
            with out_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"{config} {name}#{rep}: passed={rec.get('passed')} {rec.get('check')!s:.80} req={rec['requests']} "
                  f"in={rec['in']} cached={rec['cached']} out={rec['out']} (reason {rec['reasoning']}) "
                  f"{rec.get('seconds')}s prov={rec['providers']} fb={rec['fallbacks']}", flush=True)


if __name__ == "__main__":
    run(sys.argv[1], int(sys.argv[2]), sys.argv[3].split(","))
