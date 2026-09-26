"""Git-инструменты: статус, дифф, история, коммит, ветки — на настоящем репо."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.git import git_available, is_repo
from core.tools.base import ToolContext
from core.tools.builtin.git_tools import (
    GitBranchTool,
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitStatusTool,
)

pytestmark = pytest.mark.skipif(not git_available(), reason="git не установлен")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture()
def repo(settings):
    """Инициализирует git-репо в workspace с одним коммитом."""
    ws = settings.workspace
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "test@test.local")
    _git(ws, "config", "user.name", "Test")
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(ws, "add", "a.py")
    _git(ws, "commit", "-q", "-m", "первый коммит")
    return settings


@pytest.mark.asyncio
async def test_status_clean_then_dirty(repo):
    ctx = ToolContext(settings=repo)
    tool = GitStatusTool()
    clean = await tool.run(tool.Args(), ctx)
    assert "чистое" in clean.content

    (repo.workspace / "a.py").write_text("x = 2\n", encoding="utf-8")
    (repo.workspace / "new.py").write_text("y = 1\n", encoding="utf-8")
    dirty = await tool.run(tool.Args(), ctx)
    assert "Изменений: 2" in dirty.content
    assert "new.py" in dirty.content


@pytest.mark.asyncio
async def test_diff_shows_changes(repo):
    (repo.workspace / "a.py").write_text("x = 2\ny = 3\n", encoding="utf-8")
    ctx = ToolContext(settings=repo)
    result = await GitDiffTool().run(GitDiffTool.Args(), ctx)
    assert "+y = 3" in result.content
    assert "a.py" in result.content


@pytest.mark.asyncio
async def test_log_lists_commits(repo):
    result = await GitLogTool().run(GitLogTool.Args(count=5), ToolContext(settings=repo))
    assert "первый коммит" in result.content


@pytest.mark.asyncio
async def test_commit_creates_commit(repo):
    (repo.workspace / "a.py").write_text("x = 99\n", encoding="utf-8")
    ctx = ToolContext(settings=repo)
    result = await GitCommitTool().run(GitCommitTool.Args(message="правка a"), ctx)
    assert result.ok
    log = await GitLogTool().run(GitLogTool.Args(), ctx)
    assert "правка a" in log.content


@pytest.mark.asyncio
async def test_commit_nothing_staged(repo):
    result = await GitCommitTool().run(
        GitCommitTool.Args(message="пусто"), ToolContext(settings=repo)
    )
    assert not result.ok
    assert "Нечего коммитить" in result.content


@pytest.mark.asyncio
async def test_branch_create_and_list(repo):
    ctx = ToolContext(settings=repo)
    tool = GitBranchTool()
    created = await tool.run(tool.Args(action="create", name="feature-x"), ctx)
    assert created.ok
    listed = await tool.run(tool.Args(action="list"), ctx)
    assert "feature-x" in listed.content


@pytest.mark.asyncio
async def test_tools_reject_non_repo(settings):
    """Вне git-репозитория инструменты дают понятную ошибку, а не падают."""
    result = await GitStatusTool().run(GitStatusTool.Args(), ToolContext(settings=settings))
    assert not result.ok
    assert "git init" in result.content


@pytest.mark.asyncio
async def test_is_repo_detection(repo, settings, tmp_path):
    assert await is_repo(repo.workspace) is True
    assert await is_repo(tmp_path) is False


@pytest.mark.asyncio
async def test_branch_list_auto_allowed(repo):
    tool = GitBranchTool()
    assert tool.auto_verdict(tool.Args(action="list"), ToolContext(settings=repo)) == "allow"
    assert tool.auto_verdict(tool.Args(action="create", name="x"), ToolContext(settings=repo)) == "ask"


@pytest.mark.asyncio
async def test_restore_discards_changes(repo):
    from core.tools.builtin.git_tools import GitRestoreTool

    (repo.workspace / "a.py").write_text("СЛОМАНО\n", encoding="utf-8")
    ctx = ToolContext(settings=repo)
    result = await GitRestoreTool().run(GitRestoreTool.Args(paths=["a.py"]), ctx)
    assert result.ok
    # Файл вернулся к закоммиченному состоянию.
    assert (repo.workspace / "a.py").read_text(encoding="utf-8") == "x = 1\n"


@pytest.mark.asyncio
async def test_restore_requires_paths(repo):
    from core.tools.builtin.git_tools import GitRestoreTool

    result = await GitRestoreTool().run(GitRestoreTool.Args(paths=[]), ToolContext(settings=repo))
    assert not result.ok
