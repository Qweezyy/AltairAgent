"""Commands may run outside the workspace — with the user's approval every time, no rollback."""

from __future__ import annotations

import sys

import pytest

from core.errors import ToolError
from core.i18n import set_ui_language
from core.tools.base import ToolContext
from core.tools.builtin.background_tools import RunBackgroundTool
from core.tools.builtin.shell import ExecuteCommandTool, resolve_command_cwd


@pytest.fixture()
def outside(tmp_path):
    folder = tmp_path / "other-project"
    folder.mkdir()
    (folder / "marker.txt").write_text("outside!", encoding="utf-8")
    return folder


def _list_cmd() -> str:
    return "Get-ChildItem -Name" if sys.platform == "win32" else "ls"


def test_resolve_inside_and_outside(settings, outside):
    ctx = ToolContext(settings=settings)
    inside, is_out = resolve_command_cwd(".", ctx)
    assert inside == settings.workspace.resolve() and is_out is False
    folder, is_out = resolve_command_cwd(str(outside), ctx)
    assert folder == outside.resolve() and is_out is True
    with pytest.raises(ToolError, match="does not exist"):
        resolve_command_cwd(str(outside / "nope"), ctx)


async def test_command_runs_in_an_outside_folder(settings, outside):
    tool = ExecuteCommandTool()
    ctx = ToolContext(settings=settings)  # the default approver allows
    result = await tool.invoke({"command": _list_cmd(), "cwd": str(outside)}, ctx)
    assert result.ok, result.content
    assert "marker.txt" in result.content and "outside the workspace" in result.content


def test_outside_always_asks_even_for_read_only_commands(settings, outside):
    ctx = ToolContext(settings=settings)
    tool = ExecuteCommandTool()
    inside_args = tool.parse_args({"command": "dir", "cwd": "."})
    outside_args = tool.parse_args({"command": "dir", "cwd": str(outside)})
    assert tool.auto_verdict(inside_args, ctx) == "allow"
    assert tool.auto_verdict(outside_args, ctx) == "ask"
    bg = RunBackgroundTool()
    assert bg.auto_verdict(bg.parse_args({"command": "dir", "cwd": str(outside)}), ctx) == "ask"


def test_approval_says_it_is_outside(settings, outside):
    set_ui_language("en")
    tool = ExecuteCommandTool()
    text = tool.approval_reason(tool.parse_args({"command": "git status", "cwd": str(outside)}))
    assert "OUTSIDE the working folder" in text and str(outside) in text and "no rollback" in text
    plain = tool.approval_reason(tool.parse_args({"command": "git status"}))
    assert "OUTSIDE" not in plain
    bg = RunBackgroundTool()
    assert "outside the working folder" in bg.approval_reason(bg.parse_args({"command": "npm run dev", "cwd": str(outside)}))


async def test_denied_outside_command_does_not_run(settings, outside):
    async def deny(request):
        return False

    ctx = ToolContext(settings=settings.model_copy(update={"approval_mode": "manual"}), approver=deny)
    marker = outside / "created.txt"
    cmd = f"New-Item -ItemType File '{marker}'" if sys.platform == "win32" else f"touch '{marker}'"
    result = await ExecuteCommandTool().invoke({"command": cmd, "cwd": str(outside)}, ctx)
    assert not result.ok
    assert not marker.exists()
