"""The chat rail is resizable like the right panes, and tool summaries stay plain lines.
Wiring guards; the dragging itself is verified live in the browser."""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"


def test_rail_has_a_resize_handle():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    rail = html[html.index('<aside class="rail" id="rail">'):]
    assert rail.index('id="rail-resize"') < rail.index("</aside>")


def test_rail_width_is_dragged_clamped_remembered_and_reset():
    js = (STATIC / "redesign.js").read_text(encoding="utf-8")
    assert 'const RAIL_MIN = 200, RAIL_MAX = 520;' in js
    assert '"--rail-w"' in js and 'const RAIL_KEY = "agent_rail_w";' in js
    # The saved width is clamped on load, so a stale value can't hide the chat.
    assert "Math.min(Math.max(RAIL_MIN, savedRail), RAIL_MAX)" in js
    assert 'rh.addEventListener("dblclick"' in js


def test_rail_handle_hidden_when_collapsed_and_on_mobile():
    css = (STATIC / "redesign.layout.css").read_text(encoding="utf-8")
    assert '.app[data-rail="collapsed"] .rail-resize { display: none; }' in css
    mobile = css[css.index("@media (max-width: 900px)"):]
    assert ".rail-resize { display: none; }" in mobile[: mobile.index("\n}")]


def test_tool_summary_is_a_line_not_a_plate():
    css = (STATIC / "premium.css").read_text(encoding="utf-8")
    assert not re.search(r"^\.tools\s*\{[^}]*(background|box-shadow|border)", css, re.M)
