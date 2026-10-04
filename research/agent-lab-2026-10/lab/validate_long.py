import tempfile
from pathlib import Path

import longtasks  # noqa: F401
from tasks import TASKS

REF = {
    "textkit/wrap.py": '''def wrap(text, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return lines
''',
    "textkit/slug.py": '''import re
def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
''',
    "textkit/roman.py": '''VALUES = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
def to_roman(n):
    if n < 1: raise ValueError(n)
    out = ""
    for v, s in VALUES:
        while n >= v: out += s; n -= v
    return out
def from_roman(s):
    m = {"M": 1000, "D": 500, "C": 100, "L": 50, "X": 10, "V": 5, "I": 1}
    total = 0
    for i, ch in enumerate(s):
        v = m[ch]
        total += -v if i + 1 < len(s) and m[s[i + 1]] > v else v
    return total
''',
    "textkit/stats.py": '''import re
def word_freq(text, top=3):
    counts = {}
    for w in re.findall(r"[a-z0-9']+", text.lower()):
        counts[w] = counts.get(w, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
''',
    "textkit/table.py": '''def render(rows):
    if not rows: return ""
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return "\\n".join(" | ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows)
''',
    "textkit/dates.py": '''import datetime as dt
def parse_date(text):
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y"):
        try: return dt.datetime.strptime(text, fmt).date()
        except ValueError: pass
    raise ValueError(text)
''',
}
ws = Path(tempfile.mkdtemp()) / "x" / "ws"
ws.mkdir(parents=True)
TASKS["l_bugs"].setup(ws)
for rel, text in REF.items():
    (ws / rel).write_text(text, encoding="utf-8")
print(TASKS["l_bugs"].check(ws, ""))
