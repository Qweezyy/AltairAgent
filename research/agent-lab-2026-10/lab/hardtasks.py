"""Harder tasks with hidden tests: the agent never sees them; the check runs them afterwards."""
from __future__ import annotations

import json
import random
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from tasks import TASKS, Task

HIDDEN = Path(__file__).resolve().parent / "hidden"


def run_hidden(ws: Path, name: str, code: str) -> tuple[bool, str]:
    d = HIDDEN / f"{name}_{ws.parent.name}"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    (d / f"test_hidden_{name}.py").write_text(textwrap.dedent(code), encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(d)], cwd=ws,
                       capture_output=True, text=True, timeout=180,
                       env={**__import__("os").environ, "PYTHONPATH": str(ws)})
    last = (r.stdout.strip().splitlines() or ["?"])[-1]
    return r.returncode == 0, last[:120]


# ---------------------------------------------------------------- H1: TTL + LRU cache
H1_HIDDEN = '''
import pytest
from ttl_cache import TTLCache

class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t

def mk(cap=3, ttl=10):
    c = Clock(); return TTLCache(cap, ttl, clock=c), c

def test_basic():
    k, _ = mk(); k.put("a", 1); assert k.get("a") == 1 and len(k) == 1

def test_default():
    k, _ = mk(); assert k.get("x") is None and k.get("x", 5) == 5

def test_lru_eviction():
    k, _ = mk(2); k.put("a", 1); k.put("b", 2); k.get("a"); k.put("c", 3)
    assert k.get("b") is None and k.get("a") == 1 and k.get("c") == 3

def test_update_refreshes_recency_and_value():
    k, _ = mk(2); k.put("a", 1); k.put("b", 2); k.put("a", 9); k.put("c", 3)
    assert k.get("a") == 9 and k.get("b") is None

def test_expiry():
    k, c = mk(ttl=5); k.put("a", 1); c.t = 4.9; assert k.get("a") == 1
    c.t = 5.0; assert k.get("a") is None

def test_len_excludes_expired():
    k, c = mk(ttl=5); k.put("a", 1); k.put("b", 2); c.t = 3; k.put("c", 3); c.t = 6
    assert len(k) == 1

def test_put_resets_ttl():
    k, c = mk(ttl=5); k.put("a", 1); c.t = 4; k.put("a", 2); c.t = 8; assert k.get("a") == 2

def test_expired_do_not_cause_eviction_of_live():
    k, c = mk(2, ttl=5); k.put("a", 1); c.t = 6; k.put("b", 2); k.put("c", 3)
    assert k.get("b") == 2 and k.get("c") == 3

def test_delete():
    k, _ = mk(); k.put("a", 1); k.delete("a"); assert k.get("a") is None and len(k) == 0
    k.delete("missing")

def test_zero_capacity():
    k, _ = mk(0); k.put("a", 1); assert k.get("a") is None and len(k) == 0

def test_bad_args():
    with pytest.raises(ValueError): TTLCache(-1, 5)
    with pytest.raises(ValueError): TTLCache(2, 0)
'''
TASKS["h_cache"] = Task(
    "Create ttl_cache.py with a class TTLCache(capacity, ttl, clock=time.monotonic): put(key, value), "
    "get(key, default=None), delete(key) (no error if missing) and len(). It keeps at most `capacity` live items, "
    "evicting the least recently used (get and put both count as use); an item expires `ttl` seconds after its "
    "last put (an item is expired when now - put_time >= ttl). Expired items are never returned, are not counted "
    "by len() and must not push out live items. capacity 0 stores nothing. Raise ValueError for capacity < 0 or "
    "ttl <= 0. Write your own tests too.",
    lambda ws: None, lambda ws, a: run_hidden(ws, "cache", H1_HIDDEN))

# ---------------------------------------------------------------- H2: fix a parser from bug reports
CONF = '''"""A tiny INI-style config parser."""


def parse(text):
    data = {}
    section = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            section = line[1:-1]
            data[section] = {}
            continue
        key, value = line.split("=")
        data[section][key.strip()] = value.strip()
    return data
'''
CONF_TESTS = '''from confparse import parse


def test_simple():
    assert parse("[a]\\nx = 1") == {"a": {"x": "1"}}
'''
H2_HIDDEN = '''
import pytest
from confparse import parse

def test_semicolon_comments():
    assert parse("; c\\n[a]\\nx=1\\n; y=2") == {"a": {"x": "1"}}

def test_equals_in_value():
    assert parse("[db]\\nurl = a=b=c") == {"db": {"url": "a=b=c"}}

def test_quoted_value_keeps_spaces():
    assert parse('[a]\\nname = "  hi there "') == {"a": {"name": "  hi there "}}

def test_sections_case_insensitive_and_merged():
    assert parse("[Main]\\na=1\\n[main]\\nb=2") == {"main": {"a": "1", "b": "2"}}

def test_duplicate_key_last_wins():
    assert parse("[a]\\nx=1\\nx=2") == {"a": {"x": "2"}}

def test_key_before_section_goes_to_default():
    assert parse("x=1\\n[a]\\ny=2") == {"default": {"x": "1"}, "a": {"y": "2"}}

def test_continuation_lines():
    assert parse("[a]\\ntext = one\\n    two\\n    three") == {"a": {"text": "one two three"}}

def test_line_without_equals_is_error_with_line_number():
    with pytest.raises(ValueError, match="line 3"):
        parse("[a]\\nx=1\\nbroken")

def test_simple_still_works():
    assert parse("[a]\\nx = 1") == {"a": {"x": "1"}}
'''


def setup_conf(ws: Path) -> None:
    (ws / "confparse.py").write_text(CONF, encoding="utf-8")
    (ws / "test_confparse.py").write_text(CONF_TESTS, encoding="utf-8")


TASKS["h_conf"] = Task(
    "Users of confparse.py report problems: comments starting with ';' are not ignored; values that contain '=' "
    "crash the parser; quoted values lose their inner spaces (quotes should be removed, spaces kept); [Main] and "
    "[main] become two sections (sections are case-insensitive and should be merged, lowercase); when a key "
    "repeats, the last value should win; keys before any section crash (they belong to a 'default' section); "
    "indented lines after a key should continue its value (joined with single spaces); a line without '=' "
    "should raise ValueError mentioning its line number like 'line 3'. Fix the parser and add tests for each case.",
    setup_conf, lambda ws, a: run_hidden(ws, "conf", H2_HIDDEN))

# ---------------------------------------------------------------- H3: cross-file rename + new parameter
SHOP = {
    "shop/__init__.py": "from .pricing import calc_total\n",
    "shop/pricing.py": "def calc_total(items):\n    \"\"\"Sum of price * qty.\"\"\"\n    return round(sum(i['price'] * i['qty'] for i in items), 2)\n",
    "shop/cart.py": "from .pricing import calc_total\n\n\nclass Cart:\n    def __init__(self):\n        self.items = []\n\n    def add(self, price, qty=1):\n        self.items.append({'price': price, 'qty': qty})\n\n    def total(self):\n        return calc_total(self.items)\n",
    "shop/report.py": "from shop.pricing import calc_total\n\n\ndef summary(orders):\n    return {name: calc_total(items) for name, items in orders.items()}\n",
    "tests/test_shop.py": "from shop import calc_total\nfrom shop.cart import Cart\n\n\ndef test_total():\n    assert calc_total([{'price': 2.5, 'qty': 2}]) == 5.0\n\n\ndef test_cart():\n    c = Cart(); c.add(1.1, 3); assert c.total() == 3.3\n",
}
H3_HIDDEN = '''
import pathlib, re
import pytest
from shop import order_total
from shop.cart import Cart
from shop.report import summary

def test_new_name_and_discount():
    assert order_total([{"price": 10, "qty": 3}]) == 30
    assert order_total([{"price": 10, "qty": 3}], discount=10) == 27
    assert order_total([{"price": 9.99, "qty": 1}], discount=15) == 8.49

def test_discount_validation():
    with pytest.raises(ValueError):
        order_total([], discount=101)
    with pytest.raises(ValueError):
        order_total([], discount=-1)

def test_cart_discount():
    c = Cart(); c.add(20, 2); assert c.total() == 40 and c.total(discount=50) == 20

def test_report_discount():
    assert summary({"a": [{"price": 5, "qty": 2}]}, discount=20) == {"a": 8}

def test_old_name_is_gone():
    for f in pathlib.Path("shop").rglob("*.py"):
        assert "calc_total" not in f.read_text(), f
'''


def setup_shop(ws: Path) -> None:
    for rel, text in SHOP.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(text, encoding="utf-8")


TASKS["h_rename"] = Task(
    "In the shop package rename calc_total to order_total everywhere (no trace of the old name in shop/), and give "
    "it a keyword argument discount (a percent, 0 by default, ValueError outside 0..100) applied before rounding to "
    "2 decimals. Cart.total() and report.summary() must accept and pass discount too (default 0). Update the "
    "tests and keep them passing.", setup_shop, lambda ws, a: run_hidden(ws, "rename", H3_HIDDEN))

# ---------------------------------------------------------------- H4: log percentiles
def _logs():
    rnd = random.Random(7)
    eps = ["/api/users", "/api/orders", "/api/search", "/health", "/api/login"]
    out = []
    for i in range(3000):
        ep = rnd.choice(eps)
        status = rnd.choices([200, 201, 404, 500, 503], [70, 10, 10, 6, 4])[0]
        out.append({"ts": 1_790_000_000 + i, "endpoint": ep, "status": status,
                    "ms": round(rnd.lognormvariate(4, 0.6), 1)})
    return out


def _p95(xs):
    xs = sorted(xs)
    import math
    k = math.ceil(0.95 * len(xs)) - 1
    return xs[k]


def setup_logs(ws: Path) -> None:
    with (ws / "requests.log").open("w", encoding="utf-8") as fh:
        for r in _logs():
            fh.write(json.dumps(r) + "\n")


def check_logs(ws: Path, answer: str) -> tuple[bool, str]:
    groups = {}
    for r in _logs():
        if r["endpoint"] == "/health" or r["status"] >= 500:
            continue
        groups.setdefault(r["endpoint"], []).append(r["ms"])
    truth = sorted(((ep, _p95(v), len(v)) for ep, v in groups.items()), key=lambda x: -x[1])
    f = ws / "p95.csv"
    if not f.exists():
        return False, "no p95.csv"
    lines = [l.strip() for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    body = [l for l in lines if not l.lower().startswith("endpoint")]
    got = [tuple(x.strip() for x in l.split(",")) for l in body]
    ok = len(got) == len(truth) and all(g[0] == t[0] and abs(float(g[1]) - t[1]) < 0.051 and int(g[2]) == t[2]
                                        for g, t in zip(got, truth))
    return ok, f"got {got[:2]} truth {truth[:2]}"


TASKS["h_logs"] = Task(
    "requests.log has one JSON request per line (endpoint, status, ms). Ignore /health and every 5xx response. "
    "For each remaining endpoint compute the 95th percentile of ms using the nearest-rank method (the value at "
    "position ceil(0.95*n) in the sorted list) and the number of requests. Write p95.csv with the header "
    "endpoint,p95_ms,count, one row per endpoint, sorted by p95_ms descending, p95 with one decimal.",
    setup_logs, check_logs)

# ---------------------------------------------------------------- H5: token counts
H5_HIDDEN = '''
import pytest
from tokcount import parse_count

@pytest.mark.parametrize("text,value", [
    ("1M", 1_000_000), ("200K", 200_000), ("200k", 200_000), ("1.5M", 1_500_000), ("1,5M", 1_500_000),
    ("1 000 000", 1_000_000), ("1,000,000", 1_000_000), ("128000", 128_000), ("2b", 2_000_000_000),
    ("  64 k ", 64_000), ("200к", 200_000), ("1,5 млн", 1_500_000), ("10 тыс", 10_000),
])
def test_ok(text, value):
    assert parse_count(text) == value

@pytest.mark.parametrize("bad", ["", "abc", "1.2.3M", "-5K", "5X", "1,00"])
def test_bad(bad):
    with pytest.raises(ValueError):
        parse_count(bad)
'''
TASKS["h_tokens"] = Task(
    "Write tokcount.py with parse_count(text) -> int that reads human token counts: plain numbers with optional "
    "thousands separators (spaces, commas: '1 000 000', '1,000,000'), a decimal point or decimal comma before a "
    "suffix ('1.5M', '1,5M'), suffixes k/K, m/M, b/B and Russian к, тыс, м, млн (case-insensitive, spaces allowed "
    "around), surrounding spaces ignored. Raise ValueError for anything else, including negative numbers, unknown "
    "suffixes, several dots, and a comma that is neither a thousands separator nor a decimal comma before a "
    "suffix (e.g. '1,00'). Add tests.", lambda ws: None, lambda ws, a: run_hidden(ws, "tokens", H5_HIDDEN))

# ---------------------------------------------------------------- H6: business days
BD = '''import datetime as dt


def business_days(start, end, holidays=()):
    """Working days between two dates."""
    days = 0
    d = start
    while d < end:
        if d.weekday() < 5:
            days += 1
        d += dt.timedelta(days=1)
    return days
'''
H6_HIDDEN = '''
import datetime as dt
import pytest
from bizdays import business_days
D = dt.date

def test_inclusive_both_ends():
    assert business_days(D(2026, 10, 5), D(2026, 10, 9)) == 5

def test_same_day():
    assert business_days(D(2026, 10, 5), D(2026, 10, 5)) == 1
    assert business_days(D(2026, 10, 4), D(2026, 10, 4)) == 0

def test_holidays_skipped_and_weekend_holiday_ignored():
    assert business_days(D(2026, 10, 5), D(2026, 10, 11), holidays=[D(2026, 10, 7), D(2026, 10, 10)]) == 4

def test_reversed_range_is_negative():
    assert business_days(D(2026, 10, 9), D(2026, 10, 5)) == -5

def test_datetimes_accepted():
    assert business_days(dt.datetime(2026, 10, 5, 23, 0), dt.datetime(2026, 10, 6, 1, 0)) == 2

def test_long_range_is_fast():
    import time
    t = time.perf_counter()
    assert business_days(D(2000, 1, 3), D(2099, 12, 31)) == 26089
    assert time.perf_counter() - t < 0.05

def test_duplicate_holidays_count_once():
    assert business_days(D(2026, 10, 5), D(2026, 10, 9), holidays=[D(2026, 10, 6), D(2026, 10, 6)]) == 4
'''


def setup_bd(ws: Path) -> None:
    (ws / "bizdays.py").write_text(BD, encoding="utf-8")


TASKS["h_bizdays"] = Task(
    "bizdays.business_days(start, end, holidays=()) is wrong. Make it count Mon–Fri days from start to end with "
    "BOTH ends included, skip dates in holidays (a holiday on a weekend changes nothing, duplicates count once), "
    "return a negative count when end is before start (the same count, negated), accept datetimes (only the date "
    "part matters) and stay fast for ranges of a hundred years (no day-by-day loop). Add tests.",
    setup_bd, lambda ws, a: run_hidden(ws, "bizdays", H6_HIDDEN))
