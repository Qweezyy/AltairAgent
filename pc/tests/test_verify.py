"""Тесты перекрёстной проверки differential_check."""

from __future__ import annotations

import asyncio
import subprocess

from core.tools.base import ToolContext
from core.tools.builtin.verify_tools import DifferentialCheckTool, ReviewChangesTool


async def test_matching_outputs(ctx: ToolContext):
    res = await DifferentialCheckTool().run(
        DifferentialCheckTool.Args(
            command_a='python -c "print(sum(range(11)))"',
            command_b='python -c "print(55)"',
        ),
        ctx,
    )
    assert res.ok
    assert "Совпадает" in res.content


async def test_divergent_outputs(ctx: ToolContext):
    res = await DifferentialCheckTool().run(
        DifferentialCheckTool.Args(
            command_a='python -c "print(2+2)"',
            command_b='python -c "print(5)"',
        ),
        ctx,
    )
    assert not res.ok
    assert "Расхождение" in res.content
    assert "строка 1" in res.content


async def test_verdict_asks_for_mutating_commands(ctx: ToolContext):
    tool = DifferentialCheckTool()
    args = DifferentialCheckTool.Args(command_a="python a.py", command_b="python b.py")
    assert tool.auto_verdict(args, ctx) == "ask"


async def test_review_changes_outside_git(ctx: ToolContext):
    # Свежий workspace фикстуры не под git — критик честно предупреждает.
    res = await ReviewChangesTool().run(ReviewChangesTool.Args(), ctx)
    assert "git" in res.content.lower()


async def test_review_changes_reports_untracked(settings, ctx: ToolContext):
    ws = settings.workspace
    await asyncio.to_thread(
        subprocess.run, ["git", "init"], cwd=ws, capture_output=True, check=True
    )
    (ws / "new_module.py").write_text("print('x')\n", encoding="utf-8")
    # Диффа нет (файл неотслеживаемый), но критик должен показать новые файлы —
    # без обращения к модели.
    res = await ReviewChangesTool().run(ReviewChangesTool.Args(), ctx)
    assert "new_module.py" in res.content
