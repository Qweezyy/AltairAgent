"""Диагностика типов через pyright."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.lsp.diagnostics import (
    PyrightUnavailable,
    _flatten,
    _parse,
    type_check,
)
from core.tools.base import ToolContext
from core.tools.builtin.lsp_tools import TypeCheckTool


def _pyright_json(workspace: Path, errors: int = 2) -> str:
    f = str(workspace / "bad.py")
    return json.dumps(
        {
            "generalDiagnostics": [
                {
                    "file": f,
                    "severity": "error",
                    "message": 'Type "int" is not assignable to declared type "str"\n  detail',
                    "range": {"start": {"line": 3, "character": 14}},
                    "rule": "reportAssignmentType",
                },
                {
                    "file": f,
                    "severity": "warning",
                    "message": '"x" is possibly unbound',
                    "range": {"start": {"line": 5, "character": 0}},
                    "rule": "reportPossiblyUnbound",
                },
            ],
            "summary": {"filesAnalyzed": 1, "errorCount": errors, "warningCount": 1},
        }
    )


def test_flatten_normalizes_nbsp():
    msg = 'Type "int" not assignable\n  "int" not assignable to "str"'
    flat = _flatten(msg)
    assert " " not in flat
    assert "В В" not in flat
    assert flat.startswith("Type")
    assert "(" in flat  # вложенная деталь ушла в скобки


def test_parse_builds_diagnostics(tmp_path):
    result = _parse(_pyright_json(tmp_path), tmp_path)
    assert result.error_count == 2
    assert result.warning_count == 1
    assert result.files_analyzed == 1
    first = result.diagnostics[0]
    assert first.rel_path == "bad.py"  # относительный, прямые слэши
    assert first.line == 4  # 0-based -> 1-based
    assert first.col == 15
    assert first.rule == "reportAssignmentType"


def test_parse_non_json_raises():
    with pytest.raises(PyrightUnavailable, match="неожиданный вывод"):
        _parse("pyright failed to download node", Path("."))


@pytest.mark.asyncio
async def test_type_check_tool_reports_errors(settings, monkeypatch):
    import core.tools.builtin.lsp_tools as lt

    async def fake_type_check(target, workspace, *, settings=None):
        from core.lsp.diagnostics import _parse

        return _parse(_pyright_json(settings.workspace), settings.workspace)

    monkeypatch.setattr(lt, "type_check", fake_type_check)
    (settings.workspace / "bad.py").write_text("x=1", encoding="utf-8")

    tool = TypeCheckTool()
    result = await tool.run(tool.Args(path="."), ToolContext(settings=settings))
    assert not result.ok
    assert "reportAssignmentType" in result.content
    assert "bad.py:4:15" in result.content


@pytest.mark.asyncio
async def test_type_check_tool_errors_only(settings, monkeypatch):
    """warnings=False прячет предупреждения, но не ошибки."""
    import core.tools.builtin.lsp_tools as lt

    async def fake(target, workspace, *, settings=None):
        from core.lsp.diagnostics import _parse

        return _parse(_pyright_json(settings.workspace), settings.workspace)

    monkeypatch.setattr(lt, "type_check", fake)
    (settings.workspace / "bad.py").write_text("x=1", encoding="utf-8")

    tool = TypeCheckTool()
    result = await tool.run(tool.Args(path=".", warnings=False), ToolContext(settings=settings))
    assert "reportAssignmentType" in result.content
    assert "reportPossiblyUnbound" not in result.content


@pytest.mark.asyncio
async def test_type_check_unavailable(settings, monkeypatch):
    import core.tools.builtin.lsp_tools as lt

    async def boom(target, workspace, *, settings=None):
        raise PyrightUnavailable("pyright не установлен")

    monkeypatch.setattr(lt, "type_check", boom)
    tool = TypeCheckTool()
    result = await tool.run(tool.Args(path="."), ToolContext(settings=settings))
    assert not result.ok
    assert "недоступна" in result.content


@pytest.mark.asyncio
async def test_live_pyright_if_available(settings):
    """Живой прогон pyright, если он установлен (иначе пропуск)."""
    pytest.importorskip("pyright")
    bad = settings.workspace / "typed.py"
    bad.write_text("x: str = 123\n", encoding="utf-8")
    try:
        result = await type_check(bad, settings.workspace, settings=settings)
    except PyrightUnavailable:
        pytest.skip("pyright не смог запуститься (нет node/сети)")
    assert result.error_count >= 1
    assert any("str" in d.message for d in result.diagnostics)
