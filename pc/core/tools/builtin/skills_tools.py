"""Tools for skills (skills/): list, read with bundled files, create."""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel, Field

from core.errors import ToolError
from core.i18n import tr
from core.skills.manager import SkillManager
from core.tools.base import EmptyArgs, Tool, ToolContext


class ListSkillsTool(Tool):
    name = "list_skills"
    description = "Lists the available skills with their descriptions."
    Args = EmptyArgs
    category = "read"
    timeout = 15.0

    async def run(self, args: EmptyArgs, ctx: ToolContext) -> str:
        skills = await asyncio.to_thread(SkillManager(ctx.settings).list_skills)
        if not skills:
            return "No skills yet. Create one with create_skill."
        return "Available skills:\n" + "\n".join(f"- {s.name} ({s.scope}): {s.description}" for s in skills)


class ReadSkillArgs(BaseModel):
    name: str = Field(description="Skill name (its folder name in skills/)")


class ReadSkillTool(Tool):
    name = "read_skill"
    description = (
        "Reads a skill's full instructions and lists the files bundled with it (scripts, "
        "references). Read the matching skill from <skills> before starting a task it covers."
    )
    Args = ReadSkillArgs
    category = "read"
    timeout = 15.0

    async def run(self, args: ReadSkillArgs, ctx: ToolContext) -> str:
        try:
            return await asyncio.to_thread(SkillManager(ctx.settings).read, args.name)
        except FileNotFoundError as exc:
            raise ToolError(str(exc)) from exc


class CreateSkillArgs(BaseModel):
    name: str = Field(description="Skill name: latin letters, digits, '_' or '-' (e.g. 'git_workflow')")
    description: str = Field(description="One sentence: when this skill should be used")
    content: str = Field(description="The instructions in Markdown")
    scope: Literal["global", "project"] = Field(
        default="global",
        description=(
            "global: available in every project (default); "
            "project: only in the current workspace (.agent/skills)"
        ),
    )


class CreateSkillTool(Tool):
    name = "create_skill"
    description = (
        "Creates a skill: reusable instructions. Use it when the user describes a rule or a "
        "process that will be useful in future tasks."
    )
    Args = CreateSkillArgs
    category = "edit"
    dangerous = True
    timeout = 20.0

    def approval_reason(self, args: CreateSkillArgs) -> str:  # type: ignore[override]
        return tr("appr.skill", name=args.name, desc=args.description)

    async def run(self, args: CreateSkillArgs, ctx: ToolContext) -> str:
        manager = SkillManager(ctx.settings)
        try:
            skill = await asyncio.to_thread(
                manager.create, args.name, args.description, args.content, args.scope
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        return f"Skill '{skill.name}' ({skill.scope}) saved: {skill.path}."
