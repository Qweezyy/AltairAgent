"""Alti in the app window (static/mascot.js): its poses for the kinds of work.

The mascot is JavaScript, so the check runs it in node (skipped without node): every kind of work
draws its own prop and class, tools map to the right kind, every pose has its motion in the CSS,
and the running status names each kind in both languages.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "static"
ACTS = ["read", "write", "shell", "search", "web", "plan", "agent", "think", "tool", "answer"]

SCRIPT = r"""
global.window = {};
require(process.argv[1]);
const M = window.Mascot;
const out = { svgs: {}, acts: {} };
for (const a of JSON.parse(process.argv[2])) out.svgs[a] = M.svg({ act: a, size: 24 });
for (const t of ["read_file", "edit_file", "apply_patch", "execute_command", "run_tests", "grep_search",
                 "web_search", "browser_click", "update_plan", "spawn_subagent", "mcp__github__issue"])
  out.acts[t] = M.act(t);
out.plain = M.svg({ mood: "idle", size: 24 });
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def mascot() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    result = subprocess.run(  # noqa: S603 - our own script against our own file
        [node, "-e", SCRIPT, str(STATIC / "mascot.js"), json.dumps(ACTS)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_every_kind_of_work_has_its_pose_and_prop(mascot):
    props = {"read": "doc", "write": "pen", "shell": "term", "search": "lens", "web": "globe",
             "plan": "plan", "tool": "gear", "think": "sparks", "answer": "pen"}
    for act in ACTS:
        svg = mascot["svgs"][act]
        assert f"alti-act-{act}" in svg and "alti-busy" in svg, act
        if act in props:
            assert f"alti-prop-{props[act]}" in svg, act
    # Busy Alti keeps its satellites even small: they circle while it thinks.
    assert "alti-sat" in mascot["svgs"]["think"]
    assert "alti-prop" not in mascot["plain"] and "alti-busy" not in mascot["plain"]


def test_tools_map_to_the_kind_of_work(mascot):
    assert mascot["acts"] == {
        "read_file": "read", "edit_file": "write", "apply_patch": "write", "execute_command": "shell",
        "run_tests": "shell", "grep_search": "search", "web_search": "web", "browser_click": "web",
        "update_plan": "plan", "spawn_subagent": "agent", "mcp__github__issue": "tool",
    }


def test_the_poses_move_and_rest_for_reduced_motion():
    css = (STATIC / "redesign.layout.css").read_text(encoding="utf-8")
    for selector in (".alti-prop-pen", ".alti-caret", ".alti-prop-lens", ".alti-meridian", ".alti-prop-gear",
                     ".alti-spark", ".alti-act-think .alti-sat", ".alti-done .alti-core"):
        assert re.search(re.escape(selector) + r"[^{]*\{[^}]*animation", css), selector
    reduced = css[css.index(".alti-busy .alti-core, .alti-pupils"):]
    assert "animation: none" in reduced[:400]


def test_the_status_names_every_kind_in_both_languages():
    i18n = (STATIC / "i18n.js").read_text(encoding="utf-8")
    for act in ACTS:
        assert i18n.count(f'"st.act.{act}"') == 2, act


def test_alti_reacts_and_the_plan_has_no_second_alti():
    """Clicking Alti (the welcome one, the one by "New chat") gets a short reaction; the plan card
    has no working Alti of its own: it looked as if the status Alti moved into the plan."""
    mascot_js = (STATIC / "mascot.js").read_text(encoding="utf-8")
    for name in ("react", "clickable", "idle", "follow"):
        assert re.search(rf"function {name}\(", mascot_js), name
    assert "window.Mascot = { svg, act, react, clickable, idle, follow }" in mascot_js
    assert '<g class="alti-gaze">' in mascot_js
    css = (STATIC / "redesign.layout.css").read_text(encoding="utf-8")
    for fx in ("hop", "twirl", "wink", "giggle", "sparkle", "look", "blink2", "stretch"):
        assert f".alti-fx-{fx}" in css, fx
    js = (STATIC / "redesign.js").read_text(encoding="utf-8")
    plan = js[js.index("function renderPlan"):js.index("const RISK_TIERS")]
    assert "alti(" not in plan.split("const html")[1].split(";")[0] and "altiAt(" not in plan
    assert "Mascot.clickable(brandMark" in js and "Mascot.clickable(wm" in js
    assert '"session.missing"(m)' in js


@pytest.fixture(scope="module")
def reacting() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = r"""
    // A tiny DOM: enough for react() to set and clear its class and lay out particles.
    const classes = new Set(["alti-mascot"]);
    const el = { classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c),
                              contains: (c) => classes.has(c), [Symbol.iterator]: () => classes[Symbol.iterator]() },
                 getBoundingClientRect: () => ({ left: 0, top: 0, width: 96, height: 96 }),
                 closest: () => null };
    const appended = [];
    global.document = { getElementById: () => null, body: { appendChild: (n) => appended.push(n) },
                        createElement: () => ({ style: { setProperty() {} }, appendChild: (n) => appended.push(n) }) };
    global.window = { matchMedia: () => ({ matches: false }) };
    global.setTimeout = () => 0; global.clearTimeout = () => {};
    require(process.argv[1]);
    const kinds = [];
    for (const k of ["hop", "sparkle", undefined]) kinds.push(window.Mascot.react(el, k));
    console.log(JSON.stringify({ kinds, classes: [...classes], particles: appended.length }));
    """
    result = subprocess.run(  # noqa: S603 - our own script against our own file
        [node, "-e", script, str(STATIC / "mascot.js")],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_a_click_plays_one_reaction_with_particles(reacting):
    assert reacting["kinds"][:2] == ["hop", "sparkle"]
    assert reacting["kinds"][2] in ("hop", "twirl", "wink", "giggle", "sparkle")
    # One reaction at a time: the last one's class is on, the earlier ones are gone.
    fx = [c for c in reacting["classes"] if c.startswith("alti-fx-")]
    assert fx == [f"alti-fx-{reacting['kinds'][2]}"]
    assert reacting["particles"] > 0
