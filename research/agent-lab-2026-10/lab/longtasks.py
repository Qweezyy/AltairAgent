"""Long tasks (20+ steps): a multi-bug package and an audit of a real codebase."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from hardtasks import run_hidden
from tasks import PC, TASKS, Task

# ---------------------------------------------------------------- L1: six bugs in a package
TEXTKIT = {
    "textkit/__init__.py": "",
    "textkit/wrap.py": '''def wrap(text, width):
    """Greedy word wrap: lines of at most `width` chars; a longer word gets its own line."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) < width:
            cur = (cur + " " + w).strip()
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines
''',
    "textkit/slug.py": '''import re


def slug(text):
    """Lowercase ASCII slug: words joined by single hyphens."""
    return re.sub(r"[^a-z0-9]", "-", text.lower())
''',
    "textkit/roman.py": '''VALUES = [(1000, "M"), (500, "D"), (100, "C"), (50, "L"), (10, "X"), (5, "V"), (1, "I")]


def to_roman(n):
    out = ""
    for v, s in VALUES:
        while n >= v:
            out += s
            n -= v
    return out


def from_roman(s):
    total = 0
    for ch in s:
        total += dict((b, a) for a, b in VALUES)[ch]
    return total
''',
    "textkit/stats.py": '''def word_freq(text, top=3):
    """The `top` most frequent words (case-insensitive), ties broken alphabetically."""
    counts = {}
    for w in text.split():
        counts[w] = counts.get(w, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])[:top]
''',
    "textkit/table.py": '''def render(rows):
    """Render rows (lists of str) as a text table with columns padded to the widest cell."""
    widths = [len(c) for c in rows[0]]
    return "\\n".join(" | ".join(c.ljust(w) for c, w in zip(r, widths)) for r in rows)
''',
    "textkit/dates.py": '''import datetime as dt


def parse_date(text):
    """Accept 2026-10-02, 02.10.2026 and 10/02/2026 (US)."""
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(text)
''',
    "tests/test_textkit.py": '''import datetime as dt
from textkit.wrap import wrap
from textkit.slug import slug
from textkit.roman import to_roman, from_roman
from textkit.stats import word_freq
from textkit.table import render
from textkit.dates import parse_date


def test_wrap():
    assert wrap("aa bb cc", 5) == ["aa bb", "cc"]

def test_slug():
    assert slug("Hello, World!") == "hello-world"

def test_roman():
    assert to_roman(1994) == "MCMXCIV" and from_roman("MCMXCIV") == 1994

def test_freq():
    assert word_freq("b a B a c", 2) == [("a", 2), ("b", 2)]

def test_table():
    assert render([["a", "bb"], ["ccc", "d"]]) == "a   | bb\\nccc | d"

def test_dates():
    assert parse_date("10/02/2026") == dt.date(2026, 10, 2)
''',
}
L1_HIDDEN = '''
import datetime as dt
import pytest
from textkit.wrap import wrap
from textkit.slug import slug
from textkit.roman import to_roman, from_roman
from textkit.stats import word_freq
from textkit.table import render
from textkit.dates import parse_date

def test_wrap_exact_width_and_long_word():
    assert wrap("aaaa bb", 7) == ["aaaa bb"]
    assert wrap("x verylongword y", 4) == ["x", "verylongword", "y"]
    assert wrap("", 5) == []

def test_slug_collapses_and_strips():
    assert slug("  --Hello,   World!!--  ") == "hello-world"
    assert slug("a_b c") == "a-b-c"

def test_roman_subtractive_both_ways():
    for n in (4, 9, 14, 40, 90, 400, 900, 1994, 2026, 3999):
        assert from_roman(to_roman(n)) == n
    assert to_roman(4) == "IV" and to_roman(3999) == "MMMCMXCIX"
    with pytest.raises(ValueError):
        to_roman(0)

def test_freq_case_ties_and_punctuation():
    assert word_freq("The cat. the CAT, the dog!", 2) == [("the", 3), ("cat", 2)]
    assert word_freq("b a", 5) == [("a", 1), ("b", 1)]

def test_table_widest_in_any_row():
    assert render([["a", "b"], ["long", "x"]]) == "a    | b\\nlong | x"
    assert render([]) == ""

def test_dates_all_formats():
    assert parse_date("2026-10-02") == dt.date(2026, 10, 2)
    assert parse_date("02.10.2026") == dt.date(2026, 10, 2)
    assert parse_date("10/02/2026") == dt.date(2026, 10, 2)
    with pytest.raises(ValueError):
        parse_date("2026/10/02")
'''


def setup_textkit(ws: Path) -> None:
    for rel, text in TEXTKIT.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(text, encoding="utf-8")


TASKS["l_bugs"] = Task(
    "The textkit package has bugs in every module. Run the tests and fix all of them; then make sure each "
    "function really does what its docstring says, including edge cases the tests do not cover yet (wrapping "
    "at exactly the width, very long words, empty input; slugs with repeated or leading/trailing punctuation and "
    "underscores; roman numerals with subtractive pairs in both directions and ValueError for numbers below 1; "
    "word frequencies that ignore case and punctuation; tables whose widest cell is in any row, and an empty "
    "table; all three date formats, where the slash format is US month/day/year). Add tests for these cases.",
    setup_textkit, lambda ws, a: run_hidden(ws, "textkit", L1_HIDDEN), timeout=1500)

# ---------------------------------------------------------------- L2: audit a real codebase
AUDIT_Q = [
    ("the value of CLEARING_CEILING_TOKENS", r"100[_ ,]?000"),
    ("how many newest page snapshots are kept in full (KEEP_PAGE_STATES)", r"\b2\b"),
    ("the default update source (DEFAULT_SOURCE)", r"Qweezyy/AltairAgent"),
    ("the largest update package accepted, in MB", r"\b500\b"),
    ("the characters-per-token constant of the rough token estimate", r"\b3\b"),
    ("the tokens counted for one image/audio/video attachment", r"1[ ,_]?500"),
    ("the file a running backend writes so others can find it", r"backend\.json"),
    ("the default LLM request timeout in seconds", r"\b120\b"),
    ("the default context token budget", r"120[_ ,]?000"),
    ("how many recent tool outputs are kept when old ones are cleared", r"\b8\b"),
    ("after how many identical tool calls the agent stops running the call", r"\b20\b"),
    ("the name of the method that masks old tool outputs", r"clear_old_tool_results"),
]


def setup_audit(ws: Path) -> None:
    shutil.copytree(PC / "core", ws / "src" / "core",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "research", "devserver"))


def check_audit(ws: Path, answer: str) -> tuple[bool, str]:
    f = ws / "audit.md"
    text = f.read_text(encoding="utf-8") if f.exists() else ""
    lines = [l for l in text.splitlines() if l.strip()]
    hits = 0
    for i, (_, pat) in enumerate(AUDIT_Q, 1):
        line = next((l for l in lines if re.match(rf"\s*\(?{i}[).:\s]", l)), "")
        hits += bool(re.search(pat, line, re.I))
    return hits >= 11, f"{hits}/{len(AUDIT_Q)} right"


TASKS["l_audit"] = Task(
    "Audit the codebase under src/ and write audit.md with one line per question, numbered like '1. answer (file)': "
    + "; ".join(f"({i + 1}) {q}" for i, (q, _) in enumerate(AUDIT_Q))
    + ". Give the exact values from the code.", setup_audit, check_audit, timeout=1500)
