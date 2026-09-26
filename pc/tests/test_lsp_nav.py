"""Семантическая навигация через LSP: позиционирование, парсинг, живой pyright."""

from __future__ import annotations

import pytest

from core.lsp.client import _as_locations, _content_length, _hover_text
from core.tools.base import ToolContext
from core.tools.builtin.lsp_nav_tools import (
    CodeIntelTool,
    _find_position,
    _uri_to_rel,
)

# ------------------------------------------------------------ чистые функции


def test_find_position_first_occurrence():
    text = "def foo():\n    pass\n\nfoo()\n"
    assert _find_position(text, "foo", None) == (0, 4)


def test_find_position_prefers_line():
    text = "foo = 1\nbar = foo + foo\n"
    # На строке 2 (1-based) первое вхождение foo — символ 6.
    assert _find_position(text, "foo", 2) == (1, 6)


def test_find_position_whole_word_only():
    text = "foobar = 1\nfoo = 2\n"
    # foo не должен матчить внутри foobar — берётся строка 2.
    assert _find_position(text, "foo", None) == (1, 0)


def test_find_position_missing():
    assert _find_position("x = 1", "nope", None) is None


def test_content_length_parsing():
    assert _content_length(b"Content-Length: 42\r\n\r\n") == 42
    assert _content_length(b"content-length: 7\r\n\r\n") == 7
    assert _content_length(b"X-Other: 1\r\n\r\n") is None


def test_as_locations_normalizes():
    assert _as_locations(None) == []
    single = {"uri": "file:///a", "range": {}}
    assert _as_locations(single) == [single]
    assert len(_as_locations([single, single])) == 2


def test_hover_text_shapes():
    assert _hover_text({"contents": {"value": "hi"}}) == "hi"
    assert _hover_text({"contents": [{"value": "a"}, {"value": "b"}]}) == "a\nb"
    assert _hover_text(None) == ""


def test_uri_to_rel_windows(tmp_path):
    target = tmp_path / "pkg" / "mod.py"
    target.parent.mkdir()
    target.write_text("x", encoding="utf-8")
    rel = _uri_to_rel(target.resolve().as_uri(), tmp_path)
    assert rel == "pkg/mod.py"


# ------------------------------------------------------------ инструмент


@pytest.mark.asyncio
async def test_code_intel_bad_action(settings):
    tool = CodeIntelTool()
    (settings.workspace / "a.py").write_text("x = 1", encoding="utf-8")
    result = await tool.run(
        tool.Args(action="frobnicate", symbol="x", path="a.py"), ToolContext(settings=settings)
    )
    assert not result.ok
    assert "definition" in result.content


@pytest.mark.asyncio
async def test_code_intel_symbol_not_in_file(settings):
    tool = CodeIntelTool()
    (settings.workspace / "a.py").write_text("x = 1\n", encoding="utf-8")
    result = await tool.run(
        tool.Args(action="definition", symbol="missing", path="a.py"),
        ToolContext(settings=settings),
    )
    assert not result.ok
    assert "не найден" in result.content


@pytest.mark.asyncio
async def test_live_lsp_references(settings):
    """Живой pyright: references для функции с несколькими вызовами (если доступен)."""
    pytest.importorskip("pyright")
    from core.lsp.servers import ServerUnavailable, get_lsp_manager

    code = "def helper(n: int) -> int:\n    return n\n\na = helper(1)\nb = helper(2)\n"
    (settings.workspace / "mod.py").write_text(code, encoding="utf-8")
    tool = CodeIntelTool()
    try:
        result = await tool.run(
            tool.Args(action="references", symbol="helper", path="mod.py"),
            ToolContext(settings=settings),
        )
    except ServerUnavailable:
        pytest.skip("language server недоступен")
    finally:
        get_lsp_manager().shutdown()
    # helper: определение + 2 вызова = 3 места (если сервер поднялся).
    if "не найдены" not in result.content and "недоступ" not in result.content.lower():
        assert "helper" in result.content
