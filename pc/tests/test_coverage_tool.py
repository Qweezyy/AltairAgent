"""Инструмент покрытия тестами."""

from __future__ import annotations

import pytest

from core.tools.base import ToolContext
from core.tools.builtin.coverage_tools import TestCoverageTool, _ranges


def test_ranges_compresses():
    assert _ranges([1, 2, 3, 7, 8, 10]) == "1-3, 7-8, 10"
    assert _ranges([]) == ""
    assert _ranges([5]) == "5"
    assert _ranges([3, 1, 2]) == "1-3"  # сортирует


def _cov_json(rel: str) -> dict:
    return {
        "totals": {"percent_covered_display": "42", "percent_covered": 42.0},
        "files": {
            rel: {
                "summary": {"percent_covered": 90.0, "percent_covered_display": "90"},
                "missing_lines": [155, 156, 157, 158],
            }
        },
    }


def test_format_single_file(settings):
    tool = TestCoverageTool()
    src = settings.workspace / "mod.py"
    src.write_text("x = 1\n", encoding="utf-8")
    data = _cov_json("mod.py")
    result = tool._format(data, src, settings.workspace, tests_failed=False)
    # Для одиночного файла заголовок — процент самого файла, а не общий по папке.
    assert "90%" in result.content
    assert "155-158" in result.content
    assert "напиши тесты" in result.content


def test_format_reports_tests_failed(settings):
    tool = TestCoverageTool()
    src = settings.workspace / "mod.py"
    src.write_text("x = 1\n", encoding="utf-8")
    result = tool._format(_cov_json("mod.py"), src, settings.workspace, tests_failed=True)
    assert "упал" in result.content


def test_format_full_coverage(settings):
    tool = TestCoverageTool()
    src = settings.workspace / "mod.py"
    src.write_text("x = 1\n", encoding="utf-8")
    data = _cov_json("mod.py")
    data["files"]["mod.py"]["missing_lines"] = []
    result = tool._format(data, src, settings.workspace, tests_failed=False)
    assert "покрыто полностью" in result.content
    assert "напиши тесты" not in result.content


@pytest.mark.asyncio
async def test_live_coverage_if_available(settings):
    """Живой прогон coverage, если он установлен."""
    pytest.importorskip("coverage")
    (settings.workspace / "lib.py").write_text(
        "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n",
        encoding="utf-8",
    )
    (settings.workspace / "test_lib.py").write_text(
        "from lib import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    tool = TestCoverageTool()
    result = await tool.run(
        tool.Args(source="lib.py", tests="test_lib.py"), ToolContext(settings=settings)
    )
    # sub() не покрыт — инструмент должен показать непокрытые строки.
    assert "%" in result.content
    if "не установлен" not in result.content:
        assert "непокрыто" in result.content or "покрыто" in result.content
