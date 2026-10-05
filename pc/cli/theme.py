"""The terminal's look: the colours and glyphs of Altair (the gold star of the app window)."""

from __future__ import annotations

#: The app window's accent, hsl(40 100% 64%).
GOLD = "#ffc247"
GOLD_DIM = "#b8862e"
INK = "#e8e6e3"
MUTED = "#949494"
FAINT = "#5f5f5f"
OK = "#5fd787"
ERR = "#ff6b6b"
WARN = "#ffaf5f"
INFO = "#87afff"

#: Diff lines: a tinted background keeps the code's own colours readable on both kinds of themes.
ADDED = "#d7ffd7 on #1f3d27"
REMOVED = "#ffd7d7 on #4a1f24"
ADDED_MARK = "bold #5fd787 on #1f3d27"
REMOVED_MARK = "bold #ff6b6b on #4a1f24"

STAR = "✦"
DOT = "●"
ELBOW = "⎿"
PROMPT = "›"

#: The working indicator: the star breathes.
SPINNER = ("·", "✧", "✦", "✶", "✦", "✧")

#: A glyph per kind of work, before the live status (tools_view.activity).
ACTIVITY = {
    "think": "✧",
    "read": "≡",
    "write": "✎",
    "shell": "❯",
    "search": "⌕",
    "web": "◍",
    "plan": "☰",
    "agent": "◈",
    "tool": "⚙",
    "answer": "✎",
    "retry": "↻",
    "compact": "⇲",
}

#: The shimmer that runs over the status words: from the dim gold to almost white.
SHIMMER_FROM = (184, 134, 46)
SHIMMER_TO = (255, 243, 207)

#: The burst shown for a moment when a task is done.
BURST = ("✦", "✧ ✦ ✧", "·  ✧ ✦ ✧  ·", " ·   ✦   · ", "     ✦     ")

#: A mode's chip in the status line: (label key, style).
MODE_STYLE = {
    "manual": ("mode.manual", MUTED),
    "auto": ("mode.auto", WARN),
    "bypass": ("mode.bypass", ERR),
}

#: The styles prompt_toolkit draws the input area with.
PT_STYLE = {
    "rule": FAINT,
    "prompt": f"bold {GOLD}",
    "spinner": f"bold {GOLD}",
    "verb": f"{GOLD}",
    "dim": MUTED,
    "faint": FAINT,
    "hint": MUTED,
    "mode.manual": MUTED,
    "mode.auto": f"bold {WARN}",
    "mode.bypass": f"bold {ERR}",
    "ctx.ok": MUTED,
    "ctx.warn": WARN,
    "ctx.full": ERR,
    "bottom-toolbar": "noreverse",
    "bottom-toolbar.text": "",
    "auto-suggestion": FAINT,
    "completion-menu": "bg:#1c1c24 #c8c8d0",
    "completion-menu.completion.current": f"bg:#3a3020 {GOLD}",
    "completion-menu.meta.completion": "bg:#1c1c24 #808090",
    "completion-menu.meta.completion.current": "bg:#3a3020 #d0c0a0",
    "select.title": "bold",
    "select.cursor": f"bold {GOLD}",
    "select.current": f"bold {GOLD}",
    "select.option": "",
    "select.desc": MUTED,
    "select.keys": FAINT,
    "select.checked": OK,
}


#: Markdown and tables in Altair's colours instead of rich's defaults (magenta headings, cyan
#: table heads, inline code on black).
RICH_STYLES = {
    "markdown.code": f"{GOLD} on #2b2620",
    "markdown.code_block": "",
    "markdown.h1": f"bold {GOLD}",
    "markdown.h1.border": GOLD_DIM,
    "markdown.h2": f"bold {GOLD}",
    "markdown.h3": f"bold {INK}",
    "markdown.h4": f"bold {MUTED}",
    "markdown.link": f"underline {INFO}",
    "markdown.link_url": INFO,
    "markdown.item.bullet": GOLD,
    "markdown.item.number": GOLD,
    "markdown.block_quote": f"italic {MUTED}",
    "markdown.hr": FAINT,
    "markdown.strong": f"bold {INK}",
    "table.header": f"bold {GOLD}",
    "markdown.table.header": f"bold {GOLD}",
    "markdown.table.border": FAINT,
}


def rich_theme():
    from rich.theme import Theme

    return Theme(RICH_STYLES)
