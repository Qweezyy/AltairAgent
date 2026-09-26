"""Субагенты: ИИ сам запускает вложенного агента с произвольной задачей.

Зачем: изолированный контекст под подзадачу (разведка по большому кодовому базису,
независимый критик-ревьюер, параллельная ветка работы) — субагент не засоряет
основной диалог и возвращает наверх только итог.

Доступность управляется настройкой `allow_subagents` (галочка в настройках). Если
она выключена — инструмент отказывается работать. Рекурсия исключена: реестр
субагента не содержит самого spawn_subagent, поэтому вложенный агент не породит
ещё одного.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.events import LogEvent, RunFailed, ToolFinished, ToolStarted
from core.i18n import tr
from core.tools.base import Tool, ToolContext, ToolResult

#: Потолок шагов субагента — он решает ОДНУ подзадачу, а не живёт как основной.
_SUBAGENT_MAX_STEPS = 40


def _make_forwarding_emitter(parent_emitter, label: str):
    """Эмиттер субагента: показывает наверх только высокоуровневый прогресс.

    Стриминг текста/размышлений субагента наружу НЕ пускаем — иначе он затрёт
    ответ основного агента в ленте. Показываем лишь запуск/итог инструментов и
    ошибки, коротким логом с пометкой субагента.
    """

    async def emit(event) -> None:
        try:
            if isinstance(event, ToolStarted):
                await parent_emitter(LogEvent(text=f"↳ [{label}] {event.name}", level="debug"))
            elif isinstance(event, ToolFinished):
                mark = "ok" if event.ok else "ошибка"
                await parent_emitter(
                    LogEvent(text=f"↳ [{label}] {event.name}: {mark}", level="debug")
                )
            elif isinstance(event, RunFailed):
                await parent_emitter(
                    LogEvent(text=f"↳ [{label}] сбой: {event.message}", level="warning")
                )
        except Exception:  # noqa: BLE001 - проблемы форвардинга не должны ронять субагента
            pass

    return emit


class SpawnSubagentArgs(BaseModel):
    task: str = Field(description="Задача субагенту — сформулируй самостоятельно и полно")
    context: str = Field(
        default="", description="Доп. контекст/факты, которые субагенту стоит знать (необязательно)"
    )
    label: str = Field(default="субагент", description="Короткая метка для логов, например «ревьюер»")


class SpawnSubagentTool(Tool):
    name = "spawn_subagent"
    description = (
        "Запускает вложенного ИИ-агента с ПРОИЗВОЛЬНОЙ задачей, которую ты сам формулируешь, "
        "в изолированном контексте (та же рабочая папка и инструменты, но чистая история). "
        "Возвращает только итоговый результат субагента. Полезно для разведки по большому "
        "проекту, независимого ревью или отдельной ветки работы. Доступен, только если в "
        "настройках включено «Разрешить субагентов». Субагент не может порождать субагентов."
    )
    Args = SpawnSubagentArgs
    category = "execute"
    dangerous = True
    timeout = None

    def approval_reason(self, args: SpawnSubagentArgs) -> str:  # type: ignore[override]
        return tr("appr.subagent", label=args.label, task=args.task[:120])

    def auto_verdict(self, args: SpawnSubagentArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        # Разрешаем без вопроса — но только когда пользователь включил субагентов;
        # иначе run() всё равно откажет.
        return "allow" if ctx.settings.allow_subagents else "ask"

    async def run(self, args: SpawnSubagentArgs, ctx: ToolContext) -> ToolResult:
        if not ctx.settings.allow_subagents:
            return ToolResult.fail(
                "Запуск субагентов выключен. Включи галочку «Разрешить субагентов» в настройках, "
                "чтобы я мог сам их запускать."
            )

        task = args.task.strip()
        if not task:
            return ToolResult.fail("Пустая задача субагенту.")

        # Импорты внутри: избегаем циклической загрузки (builtin → subagent → runner).
        import core.llm as llm_mod
        from core.agent.runner import AgentRunner
        from core.agent.session import Session
        from core.tools.builtin import builtin_tools
        from core.tools.registry import ToolRegistry

        # Реестр субагента = всё, КРОМЕ порождения субагентов (защита от рекурсии)
        # и работы с секретами (F8: субагент не наследует доступ к .env/секретам).
        _blocked = {self.name, "request_secret", "list_secrets"}
        tools = [t for t in builtin_tools() if t.name not in _blocked]
        registry = ToolRegistry(tools)

        # Субагент решает одну подзадачу: ограничиваем его потолок шагов.
        sub_settings = ctx.settings.model_copy(
            update={"max_steps": min(ctx.settings.max_steps, _SUBAGENT_MAX_STEPS)}
        )

        label = args.label.strip() or "субагент"
        await ctx.emitter(LogEvent(text=f"Запускаю субагента «{label}»…", level="info"))

        prompt = task if not args.context.strip() else f"{task}\n\nКонтекст:\n{args.context.strip()}"

        llm = llm_mod.build_llm_client(sub_settings.default_model, sub_settings)
        runner = AgentRunner(
            llm=llm,
            registry=registry,
            session=Session(workspace=str(sub_settings.workspace)),
            settings=sub_settings,
            emitter=_make_forwarding_emitter(ctx.emitter, label),
            approver=ctx.approver,  # опасные действия субагента тоже спросят пользователя
        )
        # F8: помечаем контекст, чтобы shell/python не подставляли секреты из .env,
        # а чтение самого .env было запрещено. Субагент работает без секретов.
        try:
            runner.tool_context.scratch["no_secrets"] = True
        except Exception:  # noqa: BLE001 - структура раннера могла измениться
            pass
        try:
            result = await runner.run(prompt)
        finally:
            await llm.aclose()

        await ctx.emitter(LogEvent(text=f"Субагент «{label}» завершил работу.", level="info"))

        if not result.ok:
            return ToolResult.fail(
                f"Субагент «{label}» не справился: {result.error or 'неизвестная ошибка'}"
            )
        answer = (result.text or "").strip() or "(субагент не вернул текста)"
        return ToolResult(
            content=f"Результат субагента «{label}» (шагов: {result.steps}):\n\n{answer}"
        )
