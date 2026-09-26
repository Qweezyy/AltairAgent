"""Инструмент управления интерактивным планом задачи (Manus-style Planning)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.errors import ToolError
from core.events import PlanStep, PlanUpdate
from core.tools.base import Tool, ToolContext


class UpdatePlanArgs(BaseModel):
    steps: list[PlanStep] = Field(
        description="Plan steps with statuses: 'pending', 'in_progress', 'completed' or 'failed'"
    )


class UpdatePlanTool(Tool):
    name = "update_plan"
    description = (
        "Creates or updates the task's step-by-step plan, shown to the user as a live checklist. "
        "Use it for multi-step work and move statuses along (pending -> in_progress -> completed) "
        "as you go; simple tasks need no plan."
    )
    Args = UpdatePlanArgs
    category = "read"
    dangerous = False
    timeout = 10.0

    async def run(self, args: UpdatePlanArgs, ctx: ToolContext) -> str:
        if not args.steps:
            raise ToolError("План не может быть пустым. Укажи хотя бы один шаг.")

        # Отправляем событие в UI для живого отображения виджета
        await ctx.emitter(PlanUpdate(steps=args.steps))

        # Формируем сводку для модели
        lines = ["Текущий план выполнения задачи:"]
        icons = {
            "pending": "[ ]",
            "in_progress": "[>]",
            "completed": "[x]",
            "failed": "[!]",
        }
        for idx, step in enumerate(args.steps, 1):
            icon = icons.get(step.status, "[ ]")
            lines.append(f"{idx}. {icon} {step.title} ({step.status})")

        completed_count = sum(1 for s in args.steps if s.status == "completed")
        lines.append(f"\nПрогресс: {completed_count}/{len(args.steps)} шагов завершено.")
        return "\n".join(lines)
