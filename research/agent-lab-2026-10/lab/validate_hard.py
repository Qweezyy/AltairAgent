"""Reference solutions: every hidden test must pass on them (else the task is broken)."""
import shutil
import tempfile
from pathlib import Path

import hardtasks as H
from tasks import TASKS

REF = {}
REF["h_cache"] = {"ttl_cache.py": '''
import time
from collections import OrderedDict
class TTLCache:
    def __init__(self, capacity, ttl, clock=time.monotonic):
        if capacity < 0 or ttl <= 0: raise ValueError("bad args")
        self.cap, self.ttl, self.clock, self.d = capacity, ttl, clock, OrderedDict()
    def _purge(self):
        now = self.clock()
        for k in [k for k, (v, t) in self.d.items() if now - t >= self.ttl]: del self.d[k]
    def put(self, key, value):
        self._purge()
        if self.cap == 0: return
        if key in self.d: del self.d[key]
        self.d[key] = (value, self.clock())
        while len(self.d) > self.cap: self.d.popitem(last=False)
    def get(self, key, default=None):
        self._purge()
        if key not in self.d: return default
        self.d.move_to_end(key); return self.d[key][0]
    def delete(self, key): self.d.pop(key, None)
    def __len__(self): self._purge(); return len(self.d)
'''}
REF["h_conf"] = {"confparse.py": '''
def parse(text):
    data, section, last = {}, None, None
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.strip()[0] in "#;": continue
        if raw[0] in " \\t" and last is not None:
            s, k = last; data[s][k] = (data[s][k] + " " + raw.strip()).strip(); continue
        line = raw.strip()
        if line.startswith("["):
            section = line[1:-1].strip().lower(); data.setdefault(section, {}); last = None; continue
        if "=" not in line: raise ValueError(f"line {n}: no '='")
        k, v = line.split("=", 1); v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] == '"': v = v[1:-1]
        s = section or "default"; data.setdefault(s, {})[k.strip()] = v; last = (s, k.strip())
    return data
'''}
REF["h_rename"] = {
    "shop/__init__.py": "from .pricing import order_total\n",
    "shop/pricing.py": "def order_total(items, discount=0):\n    if not 0 <= discount <= 100: raise ValueError('discount')\n    return round(sum(i['price'] * i['qty'] for i in items) * (100 - discount) / 100, 2)\n",
    "shop/cart.py": "from .pricing import order_total\nclass Cart:\n    def __init__(self): self.items = []\n    def add(self, price, qty=1): self.items.append({'price': price, 'qty': qty})\n    def total(self, discount=0): return order_total(self.items, discount=discount)\n",
    "shop/report.py": "from shop.pricing import order_total\ndef summary(orders, discount=0):\n    return {n: order_total(i, discount=discount) for n, i in orders.items()}\n",
}
REF["h_tokens"] = {"tokcount.py": '''
import re
SUF = {"k": 10**3, "к": 10**3, "тыс": 10**3, "m": 10**6, "м": 10**6, "млн": 10**6, "b": 10**9}
def parse_count(text):
    t = text.strip().lower()
    m = re.fullmatch(r"(\\d[\\d ,.]*?)\\s*(k|к|тыс|m|м|млн|b)?", t)
    if not m or not t: raise ValueError(text)
    num, suf = m.group(1).strip(), m.group(2)
    if re.fullmatch(r"\\d{1,3}([ ,]\\d{3})+|\\d+", num): v = int(re.sub(r"[ ,]", "", num))
    elif suf and re.fullmatch(r"\\d+[.,]\\d+", num): v = float(num.replace(",", "."))
    else: raise ValueError(text)
    return int(round(v * SUF.get(suf, 1)))
'''}
REF["h_bizdays"] = {"bizdays.py": '''
import datetime as dt
def _d(x): return x.date() if isinstance(x, dt.datetime) else x
def _count(a, b, hol):
    days = (b - a).days + 1
    full, rest = divmod(days, 7)
    n = full * 5 + sum(1 for i in range(rest) if (a + dt.timedelta(days=full * 7 + i)).weekday() < 5)
    return n - sum(1 for h in hol if a <= h <= b and h.weekday() < 5)
def business_days(start, end, holidays=()):
    s, e = _d(start), _d(end); hol = {_d(h) for h in holidays}
    return _count(s, e, hol) if s <= e else -_count(e, s, hol)
'''}

for name, files in REF.items():
    ws = Path(tempfile.mkdtemp()) / "x" / "ws"
    ws.mkdir(parents=True)
    TASKS[name].setup(ws)
    for rel, text in files.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(text.lstrip("\n"), encoding="utf-8")
    print(name, TASKS[name].check(ws, ""))
# logs: compute with the same truth function and write it back
ws = Path(tempfile.mkdtemp()) / "x" / "ws"; ws.mkdir(parents=True); TASKS["h_logs"].setup(ws)
import json, math
g = {}
for line in open(ws / "requests.log"):
    r = json.loads(line)
    if r["endpoint"] != "/health" and r["status"] < 500: g.setdefault(r["endpoint"], []).append(r["ms"])
rows = sorted(((e, sorted(v)[math.ceil(.95 * len(v)) - 1], len(v)) for e, v in g.items()), key=lambda x: -x[1])
(ws / "p95.csv").write_text("endpoint,p95_ms,count\n" + "".join(f"{e},{p:.1f},{n}\n" for e, p, n in rows))
print("h_logs", TASKS["h_logs"].check(ws, ""))
