"""Тесты поиска: grep_search (контекст, режимы, multiline) и find_files (рекурсия)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from core.tools.base import ToolContext
from core.tools.builtin.search import FindFilesTool, GrepSearchTool


def _write(ws: Path, rel: str, text: str) -> Path:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture()
def tree(settings) -> Path:
    ws = settings.workspace
    _write(ws, "src/app.py", "def login():\n    return True\n\n# login helper\nx = 1\n")
    _write(ws, "src/util/helpers.ts", "export function login() {}\nexport const y = 2;\n")
    _write(ws, "README.md", "Проект. Функция login описана тут.\n")
    _write(ws, "node_modules/pkg/index.js", "login(); // должен быть пропущен\n")
    return ws


async def test_grep_content_with_line_numbers(tree, ctx: ToolContext):
    out = await GrepSearchTool().run(
        GrepSearchTool.Args(query="login", path="."), ctx
    )
    assert "src/app.py:1:" in out.replace("\\", "/")
    # node_modules пропущен
    assert "node_modules" not in out


async def test_grep_context_lines(tree, ctx: ToolContext):
    out = await GrepSearchTool().run(
        GrepSearchTool.Args(query="login helper", path="src/app.py", after=1), ctx
    )
    text = out.replace("\\", "/")
    # строка совпадения через ':', контекст-строка через '-'
    assert "app.py:4: # login helper" in text
    assert "app.py:5- x = 1" in text


async def test_grep_files_mode(tree, ctx: ToolContext):
    out = await GrepSearchTool().run(
        GrepSearchTool.Args(query="login", path=".", output_mode="files"), ctx
    )
    text = out.replace("\\", "/")
    assert "src/app.py" in text
    assert ":" not in text.split("\n", 1)[1]  # это пути, а не строки content


async def test_grep_count_mode(tree, ctx: ToolContext):
    out = await GrepSearchTool().run(
        GrepSearchTool.Args(query="login", path=".", output_mode="count"), ctx
    )
    assert "Совпадений всего:" in out


async def test_grep_glob_path_filter(tree, ctx: ToolContext):
    out = await GrepSearchTool().run(
        GrepSearchTool.Args(query="login", path=".", glob="**/*.ts"), ctx
    )
    text = out.replace("\\", "/")
    assert "helpers.ts" in text
    assert "app.py" not in text


async def test_grep_multiline(tree, ctx: ToolContext):
    _write(tree, "multi.py", "start\nAAA\nBBB\nend\n")
    out = await GrepSearchTool().run(
        GrepSearchTool.Args(query=r"AAA\nBBB", path="multi.py", is_regex=True, multiline=True),
        ctx,
    )
    assert "multi.py:2:" in out.replace("\\", "/")


async def test_grep_regex_invalid(tree, ctx: ToolContext):
    from core.errors import ToolError

    with pytest.raises(ToolError):
        await GrepSearchTool().run(
            GrepSearchTool.Args(query="(unclosed", path=".", is_regex=True), ctx
        )


# ------------------------------------------------ внешний ускоритель grep (tgrep/rg)

from core import search_backend as _sb


async def _grep(ctx, **kw) -> str:
    return await GrepSearchTool().run(GrepSearchTool.Args(**kw), ctx)


async def test_fast_backend_gives_identical_output(tree, ctx, monkeypatch):
    """Внешний предфильтр не должен менять вывод — только скорость."""
    # Подменяем внешний grep фейком-надмножеством (все файлы), эмулируя «движок есть».
    async def fake_cands(query, base, *, case_sensitive, timeout=20.0, **kwargs):
        import os as _os
        found = set()
        for root, _d, files in _os.walk(base):
            for n in files:
                found.add(_os.path.normcase(_os.path.join(root, n)))
        return found

    async def no_backend(query, base, *, case_sensitive, timeout=20.0, **kwargs):
        return None  # эмулирует отсутствие внешнего grep → полный обход (python)

    for kw in ({"query": "login", "path": "."},
               {"query": "login", "path": ".", "output_mode": "files"},
               {"query": "login", "path": ".", "output_mode": "count"},
               {"query": "login", "path": ".", "glob": "**/*.ts"}):
        monkeypatch.setattr("core.tools.builtin.search.candidate_files", fake_cands)
        fast = await _grep(ctx, **kw)
        monkeypatch.setattr("core.tools.builtin.search.candidate_files", no_backend)
        slow = await _grep(ctx, **kw)
        assert fast == slow, kw


def _fail(msg):  # pragma: no cover
    raise AssertionError(msg)


async def test_fast_backend_prunes_non_candidate_files(tree, ctx, monkeypatch):
    """Файл, которого нет в наборе кандидатов, не читается (и не попадает в вывод)."""
    async def only_app(query, base, *, case_sensitive, timeout=20.0, **kwargs):
        import os as _os
        return {_os.path.normcase(str(tree / "src" / "app.py"))}

    monkeypatch.setattr("core.tools.builtin.search.candidate_files", only_app)
    out = (await _grep(ctx, query="login", path=".", output_mode="files")).replace("\\", "/")
    assert "src/app.py" in out
    assert "helpers.ts" not in out  # исключён предфильтром, хотя совпадение там есть


async def test_regex_and_multiline_bypass_fast_backend(tree, ctx, monkeypatch):
    """Regex/multiline идут обычным путём — внешний предфильтр не зовём."""
    monkeypatch.setattr("core.tools.builtin.search.candidate_files",
                        lambda *a, **k: _fail("regex не должен вызывать внешний grep"))
    out = await _grep(ctx, query="log.n", path=".", is_regex=True)
    assert "app.py" in out.replace("\\", "/")


async def test_backend_failure_falls_back_to_python(tree, ctx, monkeypatch):
    """Если внешний grep вернул None (сбой) — ищем как раньше, полным обходом."""
    async def broken(query, base, *, case_sensitive, timeout=20.0, **kwargs):
        return None

    monkeypatch.setattr("core.tools.builtin.search.candidate_files", broken)
    out = (await _grep(ctx, query="login", path=".")).replace("\\", "/")
    assert "src/app.py:1:" in out


def test_can_accelerate_rules():
    assert _sb.can_accelerate("login", is_regex=False, multiline=False)
    assert not _sb.can_accelerate("login", is_regex=True, multiline=False)
    assert not _sb.can_accelerate("login", is_regex=False, multiline=True)
    assert not _sb.can_accelerate("", is_regex=False, multiline=False)
    assert not _sb.can_accelerate("логин", is_regex=False, multiline=False)  # не ASCII


def test_env_off_disables_backends(monkeypatch):
    monkeypatch.setenv("AGENT_GREP_BACKEND", "off")
    _sb.reset_cache()
    assert _sb.available_backends() == []
    _sb.reset_cache()


async def test_find_files_name_mask(tree, ctx: ToolContext):
    out = await FindFilesTool().run(FindFilesTool.Args(pattern="*.py"), ctx)
    text = out.replace("\\", "/")
    assert "src/app.py" in text
    assert "node_modules" not in text


async def test_find_files_recursive_path_glob(tree, ctx: ToolContext):
    out = await FindFilesTool().run(FindFilesTool.Args(pattern="src/**/*.ts"), ctx)
    text = out.replace("\\", "/")
    assert "src/util/helpers.ts" in text
    assert "app.py" not in text


async def test_find_files_sort_modified(tree, ctx: ToolContext):
    ws = tree
    old = _write(ws, "old.log", "x")
    _write(ws, "new.log", "y")
    # Гарантируем разное время модификации.
    past = time.time() - 1000
    os.utime(old, (past, past))
    out = await FindFilesTool().run(
        FindFilesTool.Args(pattern="*.log", sort="modified"), ctx
    )
    lines = out.replace("\\", "/").split("\n")[1:]
    assert lines[0].startswith("new.log")
