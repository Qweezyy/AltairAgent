"""Режим архитектора (Spec-Driven): письменный план-документ перед правками.

Для сложных задач агент сначала фиксирует замысел в `implementation_plan.md`:
цель, подход, какие файлы затронет, шаги, как проверит, риски. Документ виден
пользователю в «Превью» — его можно прочитать и поправить ДО того, как агент
начнёт менять код. Заодно шаги плана попадают в живой чек-лист.

Отличие от `update_plan`: тот — лёгкий чек-лист статусов в памяти; здесь —
продуманный проектный документ, который сохраняется в рабочей папке и служит
контрактом на всю задачу.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.events import ArtifactCreated, PlanStep, PlanUpdate
from core.i18n import tr
from core.security.paths import resolve_path, safe_relpath
from core.tools.base import Tool, ToolContext, ToolResult


class PlanFile(BaseModel):
    path: str = Field(description="Путь к файлу, который будет создан или изменён")
    change: str = Field(description="Что именно и зачем меняется в этом файле")


class WritePlanArgs(BaseModel):
    title: str = Field(description="Короткое название задачи")
    goal: str = Field(description="Что нужно сделать и зачем (результат для пользователя)")
    approach: str = Field(
        default="", description="Замысел решения: архитектура, контракты, ключевые решения"
    )
    files: list[PlanFile] = Field(
        default_factory=list, description="Файлы, которые будут затронуты, с пояснением по каждому"
    )
    steps: list[str] = Field(
        default_factory=list, description="Упорядоченные шаги реализации"
    )
    verification: str = Field(
        default="", description="Как проверить результат: тесты, линт, ручная проверка"
    )
    risks: str = Field(default="", description="Риски, компромиссы, что может пойти не так")
    path: str = Field(
        default="implementation_plan.md", description="Куда сохранить план в рабочей папке"
    )


def _render(args: WritePlanArgs) -> str:
    """Собирает Markdown плана из полей."""
    lines = [f"# План: {args.title}", "", "## Цель", "", args.goal.strip(), ""]
    if args.approach.strip():
        lines += ["## Подход", "", args.approach.strip(), ""]
    if args.files:
        lines += ["## Затрагиваемые файлы", ""]
        for item in args.files:
            lines.append(f"- **`{item.path}`** — {item.change.strip()}")
        lines.append("")
    if args.steps:
        lines += ["## Шаги", ""]
        for index, step in enumerate(args.steps, 1):
            lines.append(f"{index}. {step.strip()}")
        lines.append("")
    if args.verification.strip():
        lines += ["## Проверка", "", args.verification.strip(), ""]
    if args.risks.strip():
        lines += ["## Риски", "", args.risks.strip(), ""]
    return "\n".join(lines).rstrip() + "\n"


class WritePlanTool(Tool):
    name = "write_plan"
    description = (
        "Записывает продуманный план-документ (implementation_plan.md) ПЕРЕД тем, как менять код "
        "в сложной задаче: цель, подход, затрагиваемые файлы, шаги, проверка, риски. Документ "
        "показывается пользователю для просмотра и правок до начала работы, а шаги попадают в "
        "живой чек-лист. Используй в начале нетривиальных задач кодинга и рефакторинга."
    )
    Args = WritePlanArgs
    category = "edit"
    dangerous = False
    timeout = 15.0

    def auto_verdict(self, args: WritePlanArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        # План-документ безопасен и обратим (снимок делается автоматически):
        # не дёргаем пользователя подтверждением ради него.
        return "allow"

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.plan", title=args.title)

    async def run(self, args: WritePlanArgs, ctx: ToolContext) -> ToolResult:
        content = _render(args)

        name = args.path.strip() or "implementation_plan.md"
        if not name.lower().endswith(".md"):
            name += ".md"
        destination = resolve_path(name, settings=ctx.settings)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        relative = safe_relpath(destination, ctx.settings).replace("\\", "/")

        await ctx.emitter(
            ArtifactCreated(
                path=relative,
                name=destination.name,
                kind="markdown",
                size_bytes=destination.stat().st_size,
            )
        )
        # Шаги плана — в живой чек-лист (все в очереди).
        if args.steps:
            await ctx.emitter(
                PlanUpdate(steps=[PlanStep(title=s.strip(), status="pending") for s in args.steps])
            )

        return ToolResult(
            content=(
                f"План сохранён: {relative} (виден в «Превью», можно поправить до начала работы).\n\n"
                f"{content}"
            )
        )
