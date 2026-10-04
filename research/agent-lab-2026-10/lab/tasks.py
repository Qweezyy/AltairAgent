"""Benchmark tasks for the agent: each has a setup (files in the workspace) and a check."""
from __future__ import annotations

import ast
import csv
import random
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PC = Path(__file__).resolve().parents[3] / "pc"


@dataclass
class Task:
    prompt: str
    setup: Callable[[Path], None]
    check: Callable[[Path, str], tuple[bool, str]]
    timeout: int = 900


# ---------------------------------------------------------------- bugfix
STATS = '''"""Small statistics helpers."""


def mean(values):
    return sum(values) / len(values)


def median(values):
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid]


def mode(values):
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return max(counts, key=counts.get)


def variance(values):
    m = mean(values)
    return sum((v - m) ** 2 for v in values) / len(values) - 1
'''
TESTS = '''import pytest
from stats import mean, median, mode, variance


def test_mean():
    assert mean([1, 2, 3, 4]) == 2.5


def test_mean_empty():
    with pytest.raises(ValueError):
        mean([])


def test_median_odd():
    assert median([3, 1, 2]) == 2


def test_median_even():
    assert median([4, 1, 3, 2]) == 2.5


def test_mode_ties_smallest():
    assert mode([3, 1, 3, 1, 2]) == 1


def test_variance_sample():
    assert variance([2, 4, 4, 4, 5, 5, 7, 9]) == pytest.approx(4.571428, rel=1e-5)
'''


def setup_bugfix(ws: Path) -> None:
    (ws / "stats.py").write_text(STATS, encoding="utf-8")
    (ws / "test_stats.py").write_text(TESTS, encoding="utf-8")


def check_bugfix(ws: Path, answer: str) -> tuple[bool, str]:
    if (ws / "test_stats.py").read_text(encoding="utf-8") != TESTS:
        return False, "tests were changed"
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=ws,
                       capture_output=True, text=True, timeout=120)
    return r.returncode == 0, r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-200:]


# ---------------------------------------------------------------- code question
def setup_codeq(ws: Path) -> None:
    for rel in ("core/agent", "server/chats.py", "core/llm"):
        src = PC / rel
        dst = ws / "src" / rel
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def check_codeq(ws: Path, answer: str) -> tuple[bool, str]:
    nums = set(re.findall(r"\d+(?:\.\d+)?", answer))
    ok = ("15" in nums or "15.0" in nums) and "400" in nums and "1500" in nums
    return ok, f"numbers={sorted(nums)[:12]}"


# ---------------------------------------------------------------- data
def _sales_rows():
    rnd = random.Random(42)
    regions = ["North", "South", "East", "West", "Central"]
    rows = []
    for i in range(600):
        month = rnd.randint(1, 4)
        day = rnd.randint(1, 28)
        rows.append({"id": i + 1, "date": f"2026-{month:02d}-{day:02d}", "region": rnd.choice(regions),
                     "units": rnd.randint(1, 40), "unit_price": round(rnd.uniform(3, 90), 2),
                     "returned": rnd.random() < 0.07})
    return rows


def setup_data(ws: Path) -> None:
    rows = _sales_rows()
    with (ws / "sales.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow({**r, "returned": "yes" if r["returned"] else "no"})


def _truth():
    tot = {}
    for r in _sales_rows():
        if r["date"].startswith("2026-03") and not r["returned"]:
            tot[r["region"]] = tot.get(r["region"], 0) + r["units"] * r["unit_price"]
    region = max(tot, key=tot.get)
    return region, round(tot[region], 2)


def check_data(ws: Path, answer: str) -> tuple[bool, str]:
    region, value = _truth()
    f = ws / "answer.txt"
    if not f.exists():
        return False, f"no answer.txt (truth {region};{value})"
    text = f.read_text(encoding="utf-8").strip()
    m = re.match(r"\s*(\w+)\s*;\s*([\d.]+)", text)
    ok = bool(m) and m.group(1) == region and abs(float(m.group(2)) - value) < 0.011
    return ok, f"got {text[:40]!r} truth {region};{value}"


# ---------------------------------------------------------------- build
def setup_build(ws: Path) -> None:
    return None


def check_build(ws: Path, answer: str) -> tuple[bool, str]:
    probe = ("import sys; sys.path.insert(0, '.');\n"
             "from slugify_ru import slugify\n"
             "cases = {'Привет, Мир!': 'privet-mir', '  Hello   World  ': 'hello-world', '': '',\n"
             "         'Ёлка 2026 -- год': None, 'Щука и Жук': None}\n"
             "bad = []\n"
             "for k, v in cases.items():\n"
             "    out = slugify(k)\n"
             "    ok = (out == v) if v is not None else (out == out.lower() and '--' not in out and out.strip('-') == out and out.isascii() and out != '')\n"
             "    bad += [] if ok else [(k, out)]\n"
             "print('BAD', bad)\n")
    r = subprocess.run([sys.executable, "-c", probe], cwd=ws, capture_output=True, text=True, timeout=60)
    tests = list(ws.rglob("test_*.py"))
    t = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=ws,
                       capture_output=True, text=True, timeout=120) if tests else None
    ok = r.returncode == 0 and "BAD []" in r.stdout and t is not None and t.returncode == 0
    return ok, f"probe={r.stdout.strip()[-80:] or r.stderr[-120:]} tests={len(tests)} rc={t.returncode if t else None}"


# ---------------------------------------------------------------- long read (context growth)
def setup_long(ws: Path) -> None:
    shutil.copytree(PC / "core", ws / "src" / "core",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tools", "research", "devserver"))


def _long_truth(ws: Path, limit: int = 80) -> set[str]:
    out = set()
    for f in (ws / "src").rglob("*.py"):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.end_lineno - node.lineno + 1 > limit:
                    out.add(node.name)
    return out


def check_long(ws: Path, answer: str) -> tuple[bool, str]:
    truth = _long_truth(ws)
    f = ws / "report.md"
    text = f.read_text(encoding="utf-8") if f.exists() else ""
    found = {n for n in truth if re.search(rf"\b{re.escape(n)}\b", text)}
    recall = len(found) / len(truth) if truth else 1.0
    return recall >= 0.8, f"recall {len(found)}/{len(truth)}"


TASKS = {
    "bugfix": Task("Run the tests in this folder and fix stats.py so that they all pass. Do not change the tests.",
                   setup_bugfix, check_bugfix),
    "codeq": Task("In the code under src/, when the app generates a chat title with a model: what is the timeout in "
                  "seconds, what max_tokens does it request, and how many characters of the task does it send at "
                  "most? Answer with the three numbers and where you found them.", setup_codeq, check_codeq),
    "data": Task("sales.csv has sales records. Excluding returned items, which region had the highest total revenue "
                 "(units × unit_price) in March 2026? Write it to answer.txt as REGION;VALUE with the value rounded "
                 "to 2 decimals, nothing else.", setup_data, check_data),
    "build": Task("Create a Python module slugify_ru.py with a function slugify(text) that transliterates Russian "
                  "letters to Latin (e.g. Привет -> privet, ё -> e or yo), lowercases, turns every run of characters "
                  "other than a-z and 0-9 into a single hyphen and strips hyphens at the ends. Add pytest tests "
                  "(at least 6 cases) and run them until they pass.", setup_build, check_build),
    "long": Task("Go through every .py file under src/ and find every function or method longer than 80 lines "
                 "(from its def line to its last line). Write report.md with one line per function: "
                 "file, name, length, sorted by length descending.", setup_long, check_long, timeout=1500),
}


# ---------------------------------------------------------------- recall after reading (masking in the real app)
RECALL_FILES = ["core/agent/session.py", "core/updater.py", "core/search_backend.py", "core/browser_net.py",
                "core/reminders.py", "core/memory.py", "core/backend_info.py", "core/version.py"]
RECALL_Q = [
    ("the largest update package size accepted, in MB (updater.py)", r"\b500\b"),
    ("the characters-per-token constant (session.py)", r"\b3\b"),
    ("the default update source repository (updater.py)", r"Qweezyy/AltairAgent"),
    ("the name of the file a running backend writes (backend_info.py)", r"backend\.json"),
    ("the version string returned when VERSION is missing (version.py)", r"0\.0\.0"),
    ("the two external search engines and their order (search_backend.py)", r"tgrep.{0,60}(rg|ripgrep)"),
]


def setup_recall(ws: Path) -> None:
    for rel in RECALL_FILES:
        dst = ws / "src" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PC / rel, dst)


def check_recall(ws: Path, answer: str) -> tuple[bool, str]:
    f = ws / "answers.md"
    text = f.read_text(encoding="utf-8") if f.exists() else ""
    hits = sum(1 for _, pat in RECALL_Q if re.search(pat, text, re.I | re.S))
    return hits == len(RECALL_Q), f"{hits}/{len(RECALL_Q)} right"


TASKS["recall"] = Task(
    "Step 1: read each of these files in full with read_file, one per step, in this order: "
    + ", ".join("src/" + f for f in RECALL_FILES)
    + ". Do not search or grep. Step 2: only after reading all of them, write answers.md answering: "
    + "; ".join(f"({i + 1}) {q}" for i, (q, _) in enumerate(RECALL_Q))
    + ". Use only what you read.", setup_recall, check_recall, timeout=1500)
