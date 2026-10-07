"""The health gate tells "the checks ran", "the checks passed" and "accepted" apart, and does not
take a lying exit code or a failed launch for success.

From readers of the false-done article (dev.to, Kent Bodrov): a wrapper that exits with 0 while
the tests fail went through the gate, and a check that could not even be started was taken for
"this project has no tests". Both now block a silent "done".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import core.quality as quality
from core.agent.runner import AgentRunner
from core.agent.session import Session
from core.events import RunFinished
from core.llm.base import AssistantTurn
from core.quality import build_report
from core.quality.detect import Command
from core.quality.report import failures_in_output
from core.tools.registry import ToolRegistry
from tests.fakes import EventCollector, ScriptedLLM, tool_call
from tests.test_runner import _NamedTool

# ------------------------------------------------------------------ a zero exit code vs the output


@pytest.mark.parametrize("output", [
    "===== 1 failed, 3 passed in 0.21s =====",                       # pytest
    "3 passed, 2 errors in 1.02s",                                   # pytest -q
    "Ran 4 tests in 0.003s\n\nFAILED (failures=1)",                  # unittest
    "Tests:       1 failed, 4 passed, 5 total",                      # jest
    " Test Files  1 failed | 3 passed (4)",                          # vitest
    "  3 passing (12ms)\n  1 failing",                               # mocha
    "test result: FAILED. 7 passed; 1 failed; 0 ignored",            # cargo
    "--- FAIL: TestParse (0.00s)\nFAIL\tgithub.com/x/y\t0.01s",      # go
])
def test_failures_behind_a_zero_exit_code_turn_the_check_red(output):
    for tool in ("pytest", "npm"):
        report = build_report(tool, output, "", 0, False)
        assert not report.ok and report.exit_code_lied, (tool, output)
        assert report.failures[0].startswith("The exit code was 0, but the output says:")


@pytest.mark.parametrize("output", [
    "===== 12 passed in 0.40s =====",
    "Ran 4 tests in 0.003s\n\nOK",
    "Tests:       5 passed, 5 total",
    "test result: ok. 8 passed; 0 failed; 0 ignored",
    "1 xfailed, 3 passed in 0.2s",
    "Found 0 errors in 3 files",
])
def test_clean_runs_stay_green(output):
    report = build_report("pytest", output, "", 0, False)
    assert report.ok and not report.exit_code_lied, output
    assert failures_in_output(output) == ""


def test_a_failing_exit_code_is_red_as_before():
    report = build_report("pytest", "1 failed in 0.1s", "", 1, False)
    assert not report.ok and not report.exit_code_lied


# ------------------------------------------------------------------ the gate in a real run


def _runner(turns, settings, emitter):
    return AgentRunner(llm=ScriptedLLM(turns), registry=ToolRegistry([_NamedTool("write_file")]),
                       session=Session(), settings=settings, emitter=emitter)


def _finished(emitter: EventCollector) -> RunFinished:
    return [e for e in emitter.events if isinstance(e, RunFinished)][-1]


def _verdict(emitter: EventCollector) -> dict:
    """The run's checks without the tree fingerprints (they are checked on their own)."""
    return {k: v for k, v in _finished(emitter).checks.items() if not k.startswith("tree_")}


async def test_a_wrapper_lying_with_its_exit_code_sends_the_agent_back(settings, monkeypatch):
    """The real gate runs a 'test command' that prints a failure and exits with 0."""
    lying = Command(kind="tests", tool="pytest", description="pytest || true",
                    argv=[sys.executable, "-c", "print('1 failed, 4 passed in 0.12s')"])
    monkeypatch.setattr(quality, "detect_test_commands", lambda base: [lying])
    s = settings.model_copy(update={"health_gate_max_cycles": 1, "health_gate_auto_rollback": False})
    emitter = EventCollector()
    runner = _runner([
        AssistantTurn(tool_calls=[tool_call("write_file", value="x")]),
        AssistantTurn(content="Done, all green."),        # the gate: red → back to work
        AssistantTurn(content="Done, all green."),        # still red → attempts used up
        AssistantTurn(content="The tests still fail; I could not fix them."),
    ], s, emitter)
    result = await runner.run("fix it")
    assert result.text == "The tests still fail; I could not fix them."
    notes = " ".join(str(m.get("content")) for m in runner.session.messages)
    assert "The exit code was 0, but the output says" in notes
    assert _verdict(emitter) == {"executed": True, "passed": False, "acceptance": "unknown", "attempted": True}


async def test_a_check_that_could_not_start_is_unknown_not_absent(settings, monkeypatch):
    def broken(base):
        raise OSError("pytest is not installed in this environment")

    monkeypatch.setattr(quality, "detect_test_commands", broken)
    s = settings.model_copy(update={"verification_gate": False})
    emitter = EventCollector()
    runner = _runner([
        AssistantTurn(tool_calls=[tool_call("write_file", value="x")]),
        AssistantTurn(content="Done."),                            # the gate cannot start → note
        AssistantTurn(content="Changed it, but I could not run the tests: unverified."),
    ], s, emitter)
    result = await runner.run("fix it")
    assert result.text == "Changed it, but I could not run the tests: unverified."
    notes = " ".join(str(m.get("content")) for m in runner.session.messages)
    assert "could not be started" in notes and "UNKNOWN" in notes
    assert _verdict(emitter) == {"executed": False, "passed": None, "acceptance": "unknown", "attempted": True}


async def test_passing_checks_still_leave_acceptance_unknown(settings, monkeypatch):
    honest = Command(kind="tests", tool="pytest", description="pytest",
                     argv=[sys.executable, "-c", "print('5 passed in 0.10s')"])
    monkeypatch.setattr(quality, "detect_test_commands", lambda base: [honest])
    emitter = EventCollector()
    runner = _runner([
        AssistantTurn(tool_calls=[tool_call("write_file", value="x")]),
        AssistantTurn(content="Done."),
    ], settings, emitter)
    result = await runner.run("fix it")
    assert result.ok and result.text == "Done."
    # Green tests are not acceptance: nothing checked the requested behaviour itself yet.
    assert _verdict(emitter) == {"executed": True, "passed": True, "acceptance": "unknown", "attempted": True}


async def test_a_project_without_tests_says_nothing_ran(settings, monkeypatch):
    monkeypatch.setattr(quality, "detect_test_commands", lambda base: [])
    s = settings.model_copy(update={"verification_gate": False})
    emitter = EventCollector()
    runner = _runner([
        AssistantTurn(tool_calls=[tool_call("write_file", value="x")]),
        AssistantTurn(content="Done."),
    ], s, emitter)
    await runner.run("fix it")
    assert _verdict(emitter) == {"executed": False, "passed": None, "acceptance": "unknown", "attempted": True}


# ------------------------------------------------------------------ what the user sees


def test_the_terminal_summary_names_the_checks():
    import io

    from rich.console import Console

    from cli.render import Renderer
    from cli.texts import Texts

    def line(checks):
        buf = io.StringIO()
        r = Renderer(Texts("en"), Console(file=buf, force_terminal=True, color_system=None, width=120))
        r.event({"type": "run.finished", "run_id": "r", "steps": 2, "duration_ms": 900, "checks": checks})
        return buf.getvalue()

    assert "checks passed" in line({"attempted": True, "executed": True, "passed": True})
    assert "checks failed" in line({"attempted": True, "executed": True, "passed": False})
    assert "not verified" in line({"attempted": True, "executed": False, "passed": None})
    # No code changed, no gate: nothing about checks.
    assert "check" not in line({"attempted": False, "executed": False}).split("steps")[1]


def test_the_window_has_the_chip_in_both_languages():
    from pathlib import Path

    static = Path(__file__).resolve().parents[1] / "static"
    i18n = (static / "i18n.js").read_text(encoding="utf-8")
    for key in ("ck.passed", "ck.failed", "ck.notRun", "ck.acceptance", "ck.passed.tip", "ck.notRun.tip"):
        assert i18n.count(f'"{key}"') == 2, key
    js = (static / "redesign.js").read_text(encoding="utf-8")
    assert "checksChip(m.checks)" in js and "checks: e.checks" in js   # live answers and the history



async def test_files_changed_after_the_checks_make_the_verdict_unknown(settings, monkeypatch):
    """A reviewer's fixture: the tree changed after the checks ran. What they said is not about
    what is delivered, so the verdict is "unknown", never "passed"."""
    import core.agent.runner as runner_module
    from core.quality.tree import tree_fingerprint

    honest = Command(kind="tests", tool="pytest", description="pytest",
                     argv=[sys.executable, "-c", "print('5 passed in 0.10s')"])
    monkeypatch.setattr(quality, "detect_test_commands", lambda base: [honest])
    calls = {"n": 0}

    def fingerprint_then_change(root):
        calls["n"] += 1
        if calls["n"] == 2:                      # at the verdict: something touched the tree meanwhile
            (Path(root) / "late_edit.py").write_text("x = 1\n", encoding="utf-8")
        return tree_fingerprint(root)

    monkeypatch.setattr(runner_module, "tree_fingerprint", fingerprint_then_change)
    emitter = EventCollector()
    runner = _runner([AssistantTurn(tool_calls=[tool_call("write_file", value="x")]),
                      AssistantTurn(content="Done.")], settings, emitter)
    await runner.run("fix it")
    checks = _finished(emitter).checks
    assert checks["executed"] is True and checks["passed"] is None and checks["changed_after_checks"] is True
    assert checks["tree_at_checks"] != checks["tree_at_verdict"]


async def test_an_untouched_tree_keeps_the_passed_verdict_with_its_fingerprint(settings, monkeypatch):
    honest = Command(kind="tests", tool="pytest", description="pytest",
                     argv=[sys.executable, "-c", "print('5 passed in 0.10s')"])
    monkeypatch.setattr(quality, "detect_test_commands", lambda base: [honest])
    emitter = EventCollector()
    runner = _runner([AssistantTurn(tool_calls=[tool_call("write_file", value="x")]),
                      AssistantTurn(content="Done.")], settings, emitter)
    await runner.run("fix it")
    checks = _finished(emitter).checks
    assert checks["passed"] is True and "changed_after_checks" not in checks
    assert checks["tree_at_checks"] == checks["tree_at_verdict"] and len(checks["tree_at_checks"]) == 16


def test_the_fingerprint_sees_edits_and_ignores_caches(tmp_path):
    import os

    from core.quality.tree import tree_fingerprint

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("a = 1\n", encoding="utf-8")
    first = tree_fingerprint(tmp_path)
    for cache in ("__pycache__", ".pytest_cache", "node_modules", ".git"):
        (tmp_path / cache).mkdir()
        (tmp_path / cache / "x").write_text("x", encoding="utf-8")
    (tmp_path / ".coverage").write_text("c", encoding="utf-8")
    assert tree_fingerprint(tmp_path) == first                  # the checks' own leftovers
    (tmp_path / "src" / "a.py").write_text("a = 2\n", encoding="utf-8")
    assert tree_fingerprint(tmp_path) != first                  # an edit
    stamp = os.stat(tmp_path / "src" / "a.py")
    os.utime(tmp_path / "src" / "a.py", ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 10**9))
    assert tree_fingerprint(tmp_path) != first                  # same size, touched later


def test_the_terminal_and_the_window_say_unknown_after_a_late_change():
    import io

    from rich.console import Console

    from cli.render import Renderer
    from cli.texts import Texts

    buf = io.StringIO()
    r = Renderer(Texts("en"), Console(file=buf, force_terminal=True, color_system=None, width=120))
    r.event({"type": "run.finished", "run_id": "r", "steps": 2, "duration_ms": 900,
             "checks": {"attempted": True, "executed": True, "passed": None, "changed_after_checks": True}})
    assert "files changed after the checks" in buf.getvalue()
    static = Path(__file__).resolve().parents[1] / "static"
    assert (static / "i18n.js").read_text(encoding="utf-8").count('"ck.changedAfter"') == 2
