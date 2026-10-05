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
