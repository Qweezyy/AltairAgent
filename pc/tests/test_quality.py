"""Определение проверок проекта и сжатие их вывода."""

from __future__ import annotations

import json

from core.quality import describe_project, detect_lint_commands, detect_test_commands
from core.quality.report import build_report, parse_lint, parse_pytest
from core.tools.builtin.quality_tools import RunLintTool, RunTestsTool

PYTEST_OUTPUT = """
tests/test_calc.py .F                                                    [100%]
=================================== FAILURES ===================================
______________________________ test_zero_raises _______________________________
tests/test_calc.py:10: in test_zero_raises
    divide(1, 0)
calc.py:2: in divide
    return a / b
E   ZeroDivisionError: division by zero
=========================== short test summary info ============================
FAILED tests/test_calc.py::test_zero_raises - ZeroDivisionError: division by zero
1 failed, 1 passed in 0.29s
"""

RUFF_OUTPUT = """
core/app.py:12:1: F401 [*] `os` imported but unused
core/app.py:44:5: E722 Do not use bare `except`
Found 2 errors.
"""


# ------------------------------------------------------------ определение


def test_detects_pytest_in_plain_project(tmp_path):
    """Проект без pyproject.toml — просто скрипты и tests/ — самый частый случай."""
    (tmp_path / "calc.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text("def test_ok(): assert True\n", encoding="utf-8")

    commands = detect_test_commands(tmp_path)

    assert commands and commands[0].tool == "pytest"
    assert "-m" in commands[0].argv and "pytest" in commands[0].argv


def test_detects_npm_test(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest"}}), encoding="utf-8"
    )
    commands = detect_test_commands(tmp_path)
    assert any(c.tool == "npm" for c in commands)


def test_detects_go_and_cargo(tmp_path):
    (tmp_path / "go.mod").write_text("module demo\n", encoding="utf-8")
    assert any(c.tool == "go" for c in detect_test_commands(tmp_path))

    (tmp_path / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    assert any(c.tool == "cargo" for c in detect_test_commands(tmp_path))


def test_detects_ruff_and_eslint(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
    (tmp_path / ".eslintrc.json").write_text("{}", encoding="utf-8")

    tools = {c.tool for c in detect_lint_commands(tmp_path)}
    assert {"ruff", "eslint"} <= tools


def test_empty_folder_has_no_commands(tmp_path):
    assert detect_test_commands(tmp_path) == []
    assert "не обнаружены" in describe_project(tmp_path)


# ---------------------------------------------------------------- разбор


def test_pytest_report_keeps_only_what_matters():
    report = parse_pytest(PYTEST_OUTPUT, "", 1, False)

    assert not report.ok
    assert report.summary == "1 failed, 1 passed in 0.29s"
    assert report.counts == {"failed": 1, "passed": 1}
    assert len(report.failures) == 1
    assert "test_zero_raises" in report.failures[0]
    assert "ZeroDivisionError" in report.details

    rendered = report.render()
    assert len(rendered) < len(PYTEST_OUTPUT) * 1.5  # не раздуваем вывод


def test_pytest_success_is_compact():
    report = parse_pytest("125 passed in 6.88s\n", "", 0, False)

    assert report.ok
    assert report.summary == "125 passed in 6.88s"
    assert report.failures == []
    assert len(report.render()) < 120


def test_pytest_timeout_is_marked():
    report = parse_pytest("", "", -1, True)
    assert not report.ok
    assert "таймаут" in report.summary


def test_lint_report_lists_file_and_line():
    report = parse_lint("ruff", RUFF_OUTPUT, "", 1, False)

    assert not report.ok
    assert len(report.failures) == 2
    assert report.failures[0].startswith("core/app.py:12:1")


def test_lint_success():
    report = parse_lint("ruff", "All checks passed!\n", "", 0, False)
    assert report.ok and report.summary == "замечаний нет"


def test_build_report_dispatches_by_tool():
    assert build_report("pytest", "1 passed in 0.1s", "", 0, False).tool == "pytest"
    assert build_report("cargo", "", "boom", 101, False).ok is False


# ----------------------------------------------------------- инструменты


async def test_run_tests_reports_failure_and_how_to_fix(ctx):
    root = ctx.settings.workspace
    (root / "calc.py").write_text("def divide(a, b):\n    return a / b\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_calc.py").write_text(
        "import sys, os\n"
        "sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))\n"
        "from calc import divide\n\n"
        "def test_zero():\n"
        "    try:\n"
        "        divide(1, 0)\n"
        "    except ValueError:\n"
        "        return\n"
        "    raise AssertionError('нужен ValueError')\n",
        encoding="utf-8",
    )

    result = await RunTestsTool().invoke({"path": "."}, ctx)

    assert not result.ok
    assert "test_zero" in result.content
    assert "ZeroDivisionError" in result.content
    assert "запусти проверку снова" in result.content


async def test_run_tests_reports_success(ctx):
    root = ctx.settings.workspace
    (root / "tests").mkdir()
    (root / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    result = await RunTestsTool().invoke({"path": "."}, ctx)

    assert result.ok
    assert "успех" in result.content
    assert len(result.content) < 400  # успех должен быть дешёвым по токенам


async def test_run_tests_explains_when_nothing_detected(ctx):
    result = await RunTestsTool().invoke({"path": "."}, ctx)

    assert not result.ok
    assert "command" in result.content  # подсказка задать команду вручную


async def test_missing_linters_is_not_an_error(ctx):
    """Нет линтеров — это не поломка кода, модель не должна так это понимать."""
    result = await RunLintTool().invoke({"path": "."}, ctx)

    assert result.ok
    assert "не настроены" in result.content
    assert "run_tests" in result.content


async def test_run_tests_respects_custom_command(ctx):
    (ctx.settings.workspace / "tests").mkdir()
    (ctx.settings.workspace / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )

    result = await RunTestsTool().invoke(
        {"path": ".", "command": "python -m pytest tests -q"}, ctx
    )

    assert result.ok, result.content
    assert "pytest" in result.content
