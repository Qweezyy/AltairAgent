"""Цикл агента: модель -> инструменты -> модель -> ... -> ответ.

Это самая важная часть системы. Правки здесь ломают всё, поэтому логика
намеренно линейная и покрыта тестами (tests/test_runner.py).

Гарантии, которые даёт цикл:
  * история диалога всегда остаётся валидной (assistant.tool_calls ↔ tool);
  * ни один сбой инструмента не роняет задачу — ошибка возвращается модели;
  * есть жёсткий лимит шагов, лимит параллельных инструментов и защита от зацикливания;
  * остановка пользователем (CancelledError) корректно завершает задачу.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from core.agent.prompt import build_system_prompt
from core.agent.run_options import RunOptions
from core.agent.run_state import RunStateStore
from core.agent.session import TOOL_MEDIA_MARK, Session, estimate_tokens
from core.checkpoints import CheckpointStore
from core.cost import estimate_cost, load_pricing
from core.errors import AgentError, LLMError
from core.events import (
    CheckpointRestored,
    ContextUsage,
    Emitter,
    LogEvent,
    ReasoningDelta,
    Reconnecting,
    RunCancelled,
    RunFailed,
    RunFinished,
    RunStarted,
    StepStarted,
    TextDelta,
    ToolFinished,
    ToolPending,
    ToolStarted,
    UsageUpdated,
    noop_emitter,
)
from core.i18n import tr
from core.llm.base import AssistantTurn, LLMClient, ToolCall
from core.logging_setup import get_logger
from core.memory import MemoryStore
from core.security.approval import Approver, always_allow
from core.settings import Settings, get_settings
from core.skills.manager import SkillManager
from core.tools.base import ToolContext, ToolResult
from core.tools.deferred import active_tool_names
from core.tools.registry import ToolRegistry

#: Old tool outputs are masked past half the context budget, but never later than this:
#: models use a huge window poorly, and every request resends the whole history.
CLEARING_CEILING_TOKENS = 100_000

logger = get_logger("agent")

#: Номер одинакового вызова, с которого агент получает предупреждение.
REPEAT_WARNING_LIMIT = 4

#: Инструменты, которые МЕНЯЮТ код проекта. Если ими пользовались, а проверок не
#: запускали — перед «готово» агента один раз подтолкнём проверить результат.
EDIT_TOOL_NAMES = frozenset({"write_file", "edit_file", "apply_patch", "delete_path"})

#: Инструменты, которые считаются проверкой результата (закрывают «ворота»).
VERIFY_TOOL_NAMES = frozenset(
    {
        "run_tests", "run_lint", "type_check", "test_coverage", "differential_check",
        "review_changes", "run_python", "execute_command", "run_background",
        "screenshot_ui", "audit_ui",
    }
)


def _call_signature(call: ToolCall) -> str:
    """Устойчивая подпись вызова (имя + аргументы) для детекции повторов.

    Одинаковая подпись у двух вызовов = одно и то же действие. Разные аргументы
    (например правка другого файла) дают разные подписи — это прогресс.
    """
    try:
        args = call.parsed_arguments()
    except Exception:  # noqa: BLE001 - кривой JSON аргументов тоже часть подписи
        args = {"__raw__": call.arguments}
    return f"{call.name}:{json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)}"

#: Файл в workspace с проектными правилами, дописываемыми в системный промпт.
PROJECT_RULES_FILES = ("AGENTS.md", "AGENT.md", "CLAUDE.md")


@dataclass
class RunResult:
    text: str = ""
    ok: bool = True
    steps: int = 0
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    #: Длительность задачи в миллисекундах (её показывают CLI и интерфейс).
    duration_ms: int = 0


class AgentRunner:
    """Выполняет задачи пользователя в рамках одной сессии."""

    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        *,
        session: Session | None = None,
        settings: Settings | None = None,
        emitter: Emitter = noop_emitter,
        approver: Approver = always_allow,
    ) -> None:
        self.llm = llm
        #: Полный набор инструментов. `registry` может быть сужен на один
        #: запуск (например, при выключенном веб-поиске), поэтому исходный
        #: список хранится отдельно.
        self._base_registry = registry
        self.registry = registry
        self.settings = settings or get_settings()
        self.session = session or Session()
        self.emitter = emitter
        self.approver = approver
        self.skills = SkillManager(self.settings)
        #: Снимки файлов на эту сессию — источник отката правок.
        self.checkpoints = CheckpointStore(
            self.settings.storage_dir, self.session.id, self.settings.workspace
        )
        #: Долгосрочная память — общая для всех чатов.
        self.memory = MemoryStore(self.settings.data_dir)
        #: След активного прогона на диске — чтобы пережить падение/перезапуск
        #: и предложить продолжить прерванную задачу.
        self.run_state = RunStateStore(self.settings.data_dir)
        #: Контекст живёт столько же, сколько сессия: тогда ctx.scratch реально
        #: работает как общая память инструментов между шагами и задачами.
        self.tool_context = ToolContext(
            settings=self.settings,
            emitter=self._emit,
            approver=self._approve,
            run_id=self.session.id,
            checkpoints=self.checkpoints,
            memory=self.memory,
            session=self.session,
        )
        #: Имена инструментов, вызванных за текущий запуск (заполняется в run()).
        self._called_tools: set[str] = set()
        #: Edit tools that really ran: a denied or failed write changed nothing and must not
        #: trigger the health-gate or the "code changed but nothing was checked" nudge.
        self._changed_tools: set[str] = set()

    # ------------------------------------------------------------------

    async def run(self, task: str, options: RunOptions | None = None) -> RunResult:
        """Выполняет одну задачу пользователя. Не поднимает исключений, кроме CancelledError."""
        run_id = uuid.uuid4().hex[:12]
        # Помечаем этим прогоном все снимки файлов — чтобы можно было откатить
        # весь прогон целиком (аудит + «оставить одного»: вернулся и откатил всё).
        self.checkpoints.set_run(run_id)
        # Кладём на диск след прогона: если процесс умрёт посреди задачи, штатный
        # выход не случится и маркер останется — по нему предложим продолжить.
        self.run_state.begin(self.session.id, run_id, task, self.llm.model)
        started = time.perf_counter()
        usage_total: Counter[str] = Counter()
        repeats: Counter[str] = Counter()
        step = 0
        #: Имена инструментов, вызванных за этот запуск — для «ворот проверки».
        self._called_tools = set()
        self._changed_tools = set()
        nudged_verify = False
        gate_cycles = 0  # сколько раз health-gate возвращал агента чинить проверки
        # Свежий запуск не наследует фоновые уведомления/наблюдатели прошлого.
        self.tool_context.scratch.pop("notifications", None)
        self.tool_context.scratch.pop("steering", None)
        self.tool_context.scratch["_watch_tasks"] = []

        options = options or RunOptions()
        # Подозрения на инъекцию относятся к текущей задаче: сбрасываем, иначе
        # стороннее чтение в прошлой задаче гатило бы действия в этой.
        self.tool_context.scratch.pop("injection_flags", None)
        # Цены моделей для показа стоимости задачи. Читаются один раз за запуск.
        pricing = load_pricing(self.settings)
        # Реестр на этот запуск: выключенный веб-поиск = инструментов просто нет.
        # Запрет на уровне промпта модель может и обойти, отсутствие схемы — нет.
        self.registry = options.filter_registry(self._base_registry)
        self.tool_context.registry = self.registry

        # Память подставляется под конкретную задачу: релевантные факты о
        # пользователе и проекте — прямо в системный промпт.
        self.session.set_system_prompt(self._system_prompt(task))
        # Переключатели действуют на один запуск. Если их не убрать, прошлое
        # «не искать в интернете» будет тихо действовать и в следующей задаче.
        self.session.clear_run_notes()
        self.session.add_user(
            self._task_text(task, options),
            parts=options.attachments.parts(),
        )
        for note in options.notes(self.skills):
            self.session.add_run_note(note)

        await self._emit(RunStarted(run_id=run_id, task=task, model=self.llm.model))
        await self._emit_context()

        # Логи — после run.started: интерфейс заводит блок задачи по этому
        # событию, и всё, что пришло раньше, ему некуда положить.
        if options.describe():
            await self._log(tr("log.run_options", desc=options.describe()))
        for problem in options.attachments.errors:
            await self._log(tr("log.attach_failed", problem=problem), "warning")

        # max_steps <= 0 — без лимита шагов (по умолчанию: агент работает до
        # завершения задачи). Стоп-краны — стоп-кнопка, стопор застревания и
        # (если заданы) лимиты времени/токенов.
        max_steps = self.settings.max_steps
        unlimited_steps = max_steps <= 0
        try:
            while unlimited_steps or step < max_steps:
                step += 1
                self.run_state.update(self.session.id, step)
                await self._emit(StepStarted(step=step, max_steps=max_steps))

                # Фоновые уведомления (watch_background) доставляются на границе
                # шага: не прерывают текущую работу, но модель увидит их сейчас.
                await self._drain_notifications()
                # Живой steering: реплики пользователя, присланные во время прогона,
                # подхватываются здесь же — без перезапуска задачи.
                await self._drain_steering()
                # Скриншоты от vision-инструментов (audit_ui/view_image) вливаем в
                # диалог как обычное вложение — их видит ТА ЖЕ основная модель на
                # этом шаге, без отдельного запроса к другой модели.
                await self._drain_vision()

                await self._manage_context()
                await self._emit_context()

                model_started = time.perf_counter()
                turn = await self._ask_model(with_tools=True)
                # Время ответа модели пишем в журнал: без него непонятно,
                # тормозит агент или провайдер долго думает.
                await self._log(
                    tr("log.model_time", s=f"{time.perf_counter() - model_started:.1f}"),
                    "debug",
                )
                usage_total.update(turn.usage)
                self._note_context(turn)
                self.session.add_assistant_turn(turn)
                await self._report_usage(usage_total, pricing)
                await self._emit_context()

                if not turn.wants_tools:
                    edited = bool(self._changed_tools & EDIT_TOOL_NAMES)

                    # Health-gate: если правился код — перед завершением АВТОМАТИЧЕСКИ
                    # прогоняем тесты. Красно → возвращаем агента чинить (до лимита);
                    # если так и не позеленело — по желанию откатываем весь прогон и
                    # честно докладываем. Это делает «оставить одного» правдой.
                    if edited and self.settings.health_gate:
                        ran, ok, report = await self._run_health_checks()
                        if ran and not ok:
                            if gate_cycles < self.settings.health_gate_max_cycles:
                                gate_cycles += 1
                                await self._log(
                                    tr("log.gate_failed", n=gate_cycles, total=self.settings.health_gate_max_cycles),
                                    "warning",
                                )
                                self.session.add_note(
                                    "Автопроверка (health-gate) НЕ прошла. Ниже её вывод — найди "
                                    "причину, почини и доведи до зелёного, потом можно завершать. "
                                    "Не заявляй «готово», пока красно.\n\n" + report[:6000]
                                )
                                continue
                            # Попытки исчерпаны — оставлять красный код нельзя.
                            rolled: list[str] = []
                            if self.settings.health_gate_auto_rollback:
                                try:
                                    res = await asyncio.to_thread(self.checkpoints.restore_run, run_id)
                                    rolled = res.get("restored", [])
                                except LookupError:
                                    rolled = []
                                for rel in rolled:
                                    await self._emit(CheckpointRestored(
                                        path=rel, message=tr("log.gate_rollback_file", path=rel)))
                            await self._log(
                                tr("log.gate_rollback", n=len(rolled)) if rolled else tr("log.gate_kept"),
                                "warning",
                            )
                            note = (
                                "Автопроверки так и не прошли за отведённые попытки. "
                                + ("Все изменения этого прогона ОТКАЧЕНЫ к состоянию до него. "
                                   if rolled else "")
                                + "Честно объясни пользователю: что пытался сделать, почему не "
                                "вышло (кратко по выводу проверок ниже) и предложи варианты. "
                                "НЕ заявляй успех.\n\n" + report[:4000]
                            )
                            self.session.add_note(note)
                            final = await self._ask_model(with_tools=False)
                            usage_total.update(final.usage)
                            self._note_context(final)
                            self.session.add_assistant_turn(final)
                            return await self._finish(
                                run_id, final.content, step, started, usage_total, pricing)
                        if ran and ok:
                            await self._log(tr("log.gate_ok"), "info")
                            return await self._finish(
                                run_id, turn.content, step, started, usage_total, pricing)
                        # ran == False: автотестов нет — падаем в мягкий нудж ниже.

                    # Мягкие ворота: правил код, но проверить нечем автоматически —
                    # один раз попросим проверить/честно сказать (не зацикливаемся).
                    if (
                        self.settings.verification_gate
                        and not nudged_verify
                        and edited
                        and not (self._called_tools & VERIFY_TOOL_NAMES)
                    ):
                        nudged_verify = True
                        await self._log(
                            tr("log.verify_nudge"),
                            "warning",
                        )
                        self.session.add_note(
                            "Ты изменил код, но не запускал никаких проверок. Прежде чем "
                            "заявлять «готово», проверь результат: `run_tests`, при наличии — "
                            "`run_lint`/`type_check`, для UI — `screenshot_ui`/`audit_ui`, а также "
                            "пробеги свой дифф ревьюером через `review_changes`. Если проверять "
                            "объективно нечем (нет тестов и т.п.) — честно скажи об этом в ответе."
                        )
                        continue
                    return await self._finish(run_id, turn.content, step, started, usage_total, pricing)

                # Стоп-кран по времени: не начинаем новый круг инструментов, если
                # исчерпан бюджет времени (долгие/зависшие процессы не жгут токены).
                time_budget = self.settings.max_run_seconds
                if time_budget and (time.perf_counter() - started) >= time_budget:
                    return await self._wrap_up(
                        run_id,
                        f"Достигнут лимит времени {time_budget:.0f} с на задачу. "
                        "Запрашиваю итоговый ответ.",
                        "Исчерпан лимит времени на задачу. Инструменты больше недоступны. "
                        "Дай пользователю итоговый ответ: что сделано и что осталось.",
                        step, started, usage_total, pricing,
                    )

                # Потолок токенов на задачу: не даём агенту жечь бюджет бесконечно.
                budget = self.settings.max_run_tokens
                if budget and usage_total["total_tokens"] >= budget:
                    return await self._wrap_up(
                        run_id,
                        f"Достигнут лимит {budget} токенов на задачу. Запрашиваю итоговый ответ.",
                        "Исчерпан бюджет токенов на задачу. Инструменты больше недоступны. "
                        "Дай пользователю итоговый ответ: что сделано и что осталось.",
                        step, started, usage_total, pricing,
                    )

                await self._run_tools(turn.tool_calls, repeats)
                await self._emit_context()

            # Достигнут абсолютный потолок шагов — просим финальный ответ без инструментов.
            return await self._wrap_up(
                run_id,
                f"Достигнут потолок в {self.settings.max_steps} шагов. Запрашиваю итоговый ответ.",
                "Достигнут потолок шагов. Инструменты больше недоступны. "
                "Дай пользователю итоговый ответ: что сделано, что осталось и почему.",
                step, started, usage_total, pricing,
            )

        except asyncio.CancelledError:
            # Tell the UI and re-raise: swallowing CancelledError would mark the task done and
            # break stop/shutdown. Close the step first, so the history stays whole and valid.
            self._close_interrupted_step()
            self.session.add_note(
                "The user stopped this task. What was done so far is above; continue from there "
                "if the next message asks for it."
            )
            await asyncio.shield(self._emit(RunCancelled(run_id=run_id)))
            raise

        except (AgentError, LLMError) as exc:
            logger.exception("Задача завершилась ошибкой")
            await self._emit(RunFailed(run_id=run_id, message=str(exc)))
            return RunResult(ok=False, steps=step, error=str(exc))

        except Exception as exc:  # noqa: BLE001 - последний рубеж: UI не должен зависать
            logger.exception("Непредвиденная ошибка в цикле агента")
            message = f"Внутренняя ошибка агента: {type(exc).__name__}: {exc}"
            await self._emit(RunFailed(run_id=run_id, message=message))
            return RunResult(ok=False, steps=step, error=message)

        finally:
            # Снимаем след прогона: любой штатный выход (успех/ошибка/отмена)
            # доходит сюда. Оставшийся маркер = процесс умер посреди задачи.
            self.run_state.clear(self.session.id)
            # Гасим фоновые наблюдатели (watch_background), не переживших задачу.
            for task in self.tool_context.scratch.get("_watch_tasks", []):
                if not task.done():
                    task.cancel()
            self.tool_context.scratch["_watch_tasks"] = []

    async def _drain_notifications(self) -> None:
        """Переносит готовые фоновые уведомления в историю — модель увидит их сейчас."""
        pending = self.tool_context.scratch.get("notifications")
        if not pending:
            return
        # Забираем и очищаем атомарно (один поток event loop, гонок нет).
        self.tool_context.scratch["notifications"] = []
        for text in pending:
            self.session.add_note(f"[Фоновое уведомление] {text}")
            await self._log(tr("log.background", text=text), "info")

    async def _drain_steering(self) -> None:
        """Вносит реплики пользователя, присланные во время прогона, как ход диалога.

        Точка — граница шага: предыдущий ход (assistant + результаты инструментов)
        уже полностью в истории, поэтому добавить user-сообщение здесь безопасно и
        модель ответит на него следующим шагом, не теряя уже сделанного."""
        pending = self.tool_context.scratch.get("steering")
        if not pending:
            return
        self.tool_context.scratch["steering"] = []
        for text in pending:
            text = str(text).strip()
            if not text:
                continue
            self.session.add_user(f"[Уточнение по ходу] {text}")
            await self._log(tr("log.steering", text=text), "info")

    async def _drain_vision(self) -> None:
        """Показывает основной модели скриншоты от vision-инструментов — как обычное
        вложение пользователя (тот же мультимодальный формат), в том же диалоге."""
        pending = self.tool_context.scratch.get("_vision_pending")
        if not pending:
            return
        self.tool_context.scratch["_vision_pending"] = []
        parts: list[dict[str, Any]] = []
        texts: list[str] = []
        for item in pending:
            parts.extend(item.get("parts") or [])
            if item.get("text"):
                texts.append(str(item["text"]))
        if parts:
            # Marked, so the history can drop it once a newer screenshot supersedes it.
            text = "\n\n".join(texts) or "Screenshot to check."
            self.session.add_user(f"{TOOL_MEDIA_MARK} {text}", parts=parts)

    # ------------------------------------------------------------------

    async def _ask_model(self, *, with_tools: bool) -> AssistantTurn:
        self._partial_text = ""

        async def on_text(chunk: str) -> None:
            self._partial_text += chunk  # kept if the user stops the answer midway
            await self._emit(TextDelta(text=chunk))

        async def on_reasoning(chunk: str) -> None:
            await self._emit(ReasoningDelta(text=chunk))

        async def on_tool_progress(name: str, chars: int) -> None:
            await self._emit(ToolPending(name=name, chars=chars))

        async def on_retry(attempt: int, total: int, delay: float, reason: str) -> None:
            # Живая индикация переподключения: без неё повторы выглядят зависанием.
            await self._emit(
                Reconnecting(attempt=attempt, max_attempts=total, delay_s=round(delay, 1), reason=reason)
            )

        tools = None
        if with_tools:
            # Deferred loading: core tools + whatever this chat has found or used.
            active = active_tool_names(
                self.registry, self.settings.tool_search, self.session.messages, self.tool_context.scratch
            )
            tools = self.registry.schemas(active)
        turn = await self.llm.complete(
            self.session.snapshot(),
            tools=tools,
            on_text=on_text,
            on_reasoning=on_reasoning,
            on_tool_progress=on_tool_progress,
            on_retry=on_retry,
        )
        self._partial_text = ""  # the answer is complete: it goes into the history as a whole
        return turn

    async def _run_tools(self, calls: list[ToolCall], repeats: Counter[str]) -> None:
        semaphore = asyncio.Semaphore(max(1, self.settings.max_parallel_tools))

        self._finished_calls = {}

        async def execute(call: ToolCall) -> tuple[ToolCall, ToolResult]:
            async with semaphore:
                result = await self._execute_call(call, self.tool_context, repeats)
                self._finished_calls[call.id] = result  # kept if the user stops the others
                return call, result

        results = await asyncio.gather(*(execute(call) for call in calls))

        # Порядок ответов должен совпадать с порядком tool_calls.
        for call, result in results:
            self.session.add_tool_result(call.id, call.name, result.content)

    async def _execute_call(
        self, call: ToolCall, ctx: ToolContext, repeats: Counter[str]
    ) -> ToolResult:
        args = call.parsed_arguments()
        # Учёт вызванных инструментов — для «ворот проверки» перед завершением.
        self._called_tools.add(call.name)
        await self._emit(ToolStarted(call_id=call.id, name=call.name, args=args))
        started = time.perf_counter()

        tool = self.registry.get(call.name)
        if tool is None:
            hints = self.registry.suggest(call.name)
            hint = f" Возможно, ты имел в виду: {', '.join(hints)}." if hints else ""
            result = ToolResult.fail(
                f"Инструмента '{call.name}' не существует.{hint} "
                f"Доступные инструменты: {', '.join(self.registry.names())}."
            )
        else:
            signature = _call_signature(call)
            repeats[signature] += 1
            if repeats[signature] >= self.settings.repeat_call_block_limit:
                result = ToolResult.fail(
                    f"Вызов '{call.name}' с теми же аргументами повторяется "
                    f"{repeats[signature]} раз и заблокирован как зацикливание. Смени подход "
                    "или сообщи пользователю, что застрял и почему."
                )
            else:
                result = await tool.invoke(call.arguments or args, ctx)
                if repeats[signature] >= REPEAT_WARNING_LIMIT:
                    result.content += (
                        f"\n\nПРЕДУПРЕЖДЕНИЕ: это {repeats[signature]}-й вызов "
                        f"'{call.name}' с теми же аргументами. Если результат не приближает "
                        "к цели, измени подход."
                    )

        elapsed = int((time.perf_counter() - started) * 1000)
        await self._emit(
            ToolFinished(
                call_id=call.id,
                name=call.name,
                ok=result.ok,
                output=result.content,
                duration_ms=elapsed,
            )
        )
        logger.info("Инструмент %s -> ok=%s за %d мс", call.name, result.ok, elapsed)
        if result.ok:
            self._changed_tools.add(call.name)
        return result

    async def _manage_context(self) -> None:
        """Держит историю в рамках бюджета. Если можно — сворачивает старое в
        резюме, а не выбрасывает: так агент не забывает договорённости."""
        budget = self.settings.context_token_budget
        # Stale page snapshots and screenshots go first, whatever the budget: they describe
        # pages that are gone, and a browsing task would otherwise resend them on every step.
        if self.settings.tool_result_clearing:
            dropped, _ = self.session.supersede_page_states()
            if dropped:
                await self._log(tr("log.superseded", n=dropped), "debug")
        # Then, past half the budget, old tool outputs become short notes (observation
        # masking). A 1M window must not mean 500K of old outputs resent on every request:
        # the ceiling keeps it to what a model actually uses well. One batch that frees a
        # lot, so the cache breaks rarely.
        threshold = min(budget // 2, CLEARING_CEILING_TOKENS)
        if self.settings.tool_result_clearing and self.session.token_estimate() > threshold:
            cleared, freed = self.session.clear_old_tool_results(
                keep_recent=self.settings.tool_result_keep_recent, min_free_chars=min(budget // 5, 60_000)
            )
            if cleared:
                await self._log(tr("log.cleared", n=cleared, chars=freed), "debug")

        count = self.session.overflow_count(budget)
        if count <= 0:
            return

        if self.settings.context_compaction:
            old = self.session.peek_prefix(count)
            summary = await self._summarize_history(old)
            if summary:
                self.session.replace_prefix(
                    count, f"[Ранее в диалоге (свёрнуто {count} сообщений): {summary}]"
                )
                await self._log(tr("log.compacted", n=count), "debug")
                return

        # Резюме не вышло (или выключено) — грубый запасной вариант.
        dropped = self.session.trim(budget)
        if dropped:
            await self._log(tr("log.trimmed", n=dropped), "warning")

    async def _summarize_history(self, messages: list[dict[str, Any]]) -> str:
        """Сжимает старую часть диалога в короткое резюме одним вызовом модели."""
        transcript = _format_for_summary(messages)
        if not transcript.strip():
            return ""
        try:
            turn = await self.llm.complete(
                [
                    {
                        "role": "system",
                        "content": "Ты сжимаешь начало диалога в краткую памятку для продолжения работы.",
                    },
                    {
                        "role": "user",
                        "content": (
                            "Сожми это начало диалога агента с пользователем в короткое резюме "
                            "(5–8 предложений): что просил пользователь, что сделал агент, какие "
                            "факты, договорённости и решения важны для продолжения. Пиши по делу, "
                            "без воды.\n\n" + transcript
                        ),
                    },
                ],
                max_tokens=600,
            )
        except (AgentError, LLMError) as exc:
            logger.debug("Не удалось сжать контекст: %s", exc)
            return ""
        return (turn.content or "").strip()

    def _close_interrupted_step(self) -> None:
        """What the user saw before pressing stop stays in the history.

        A half-streamed answer is kept (marked as interrupted) instead of vanishing, and every
        tool call of the last step gets its answer: the real result for calls that finished,
        a note for the ones that were cut off. A call without an answer makes providers reject
        the whole history (HTTP 400) and leaves the model unaware of what already happened.
        """
        partial = getattr(self, "_partial_text", "").strip()
        self._partial_text = ""
        answered = {m.get("tool_call_id") for m in self.session.messages if m.get("role") == "tool"}
        last = next((m for m in reversed(self.session.messages) if m.get("role") == "assistant"), None)
        finished = getattr(self, "_finished_calls", {})
        for call in (last or {}).get("tool_calls") or []:
            call_id = call.get("id")
            if call_id in answered:
                continue
            name = call.get("function", {}).get("name", "")
            result = finished.get(call_id)
            content = result.content if result is not None else (
                "[Stopped by the user before this finished: its result is unknown. Check the "
                "state before relying on it.]")
            self.session.add_tool_result(call_id, name, content)
        self._finished_calls = {}
        if partial:
            self.session.add_assistant_turn(AssistantTurn(content=f"{partial}\n\n[Interrupted by the user here.]"))

    def _note_context(self, turn: AssistantTurn) -> None:
        """Remember how big the context really was on this request, as the provider counted
        it: the ring shows that instead of an estimate from characters. The mark is our own
        estimate of the same moment (the answer included), the base for live updates."""
        seen = int(turn.usage.get("context_tokens") or turn.usage.get("prompt_tokens") or 0)
        if seen:
            self.session.context_tokens = seen + int(turn.usage.get("completion_tokens") or 0)
            self.session.context_mark = self.session.token_estimate() + estimate_tokens([turn.to_message()])

    async def _emit_context(self) -> None:
        """The ring follows the run live: after the user's message, every model answer, tool
        results and any masking, not only when the run ends."""
        tokens, exact = self.session.context_now()
        await self._emit(ContextUsage(tokens=tokens, exact=exact))

    async def _report_usage(self, usage: Counter[str], pricing: dict) -> None:
        """Живой счётчик расхода: обновляется после каждого ответа модели."""
        cost = estimate_cost(dict(usage), self.llm.model, pricing)
        await self._emit(
            UsageUpdated(
                tokens=cost.total_tokens,
                usd=round(cost.usd, 6),
                priced=cost.priced,
                budget=self.settings.max_run_tokens,
            )
        )

    async def _run_health_checks(self) -> tuple[bool, bool, str]:
        """Автоматически прогоняет тесты проекта перед завершением.

        Возвращает (ran, ok, отчёт): ran=False — автотестов не нашлось (не наша
        забота решать «зелено/красно»); ok — прошли ли. Любой сбой самого запуска
        безопасно трактуется как «не прогнали» (ran=False), чтобы не ронять задачу.
        """
        try:
            from core.quality import build_report, detect_test_commands
            from core.utils.proc import run_process

            base = self.settings.workspace
            commands = await asyncio.to_thread(detect_test_commands, base)
            if not commands:
                return (False, True, "")
            command = commands[0]
            await self._log(tr("log.gate_running", cmd=command.display), "info")
            result = await run_process(
                command.argv, cwd=base, timeout=self.settings.health_gate_timeout
            )
            report = build_report(
                command.tool, result.stdout, result.stderr, result.returncode, result.timed_out
            )
            return (True, report.ok, f"Команда: {command.display}\n\n{report.render()}")
        except Exception as exc:  # noqa: BLE001 — health-gate не должен ронять прогон
            await self._log(tr("log.gate_error", error=exc), "warning")
            return (False, True, "")

    async def _wrap_up(
        self,
        run_id: str,
        log_text: str,
        note: str,
        step: int,
        started: float,
        usage_total: Counter[str],
        pricing: dict,
    ) -> RunResult:
        """Единый «стоп-кран»: логирует причину остановки, просит у модели итог
        БЕЗ инструментов и корректно завершает задачу. Используется всеми
        лимитами (токены/время/шаги), чтобы поведение было одинаковым."""
        await self._log(log_text, "warning")
        self.session.add_note(note)
        final = await self._ask_model(with_tools=False)
        usage_total.update(final.usage)
        self._note_context(final)
        self.session.add_assistant_turn(final)
        return await self._finish(run_id, final.content, step, started, usage_total, pricing)

    async def _finish(
        self,
        run_id: str,
        text: str,
        steps: int,
        started: float,
        usage: Counter[str],
        pricing: dict | None = None,
    ) -> RunResult:
        final_text = (text or "").strip()
        if not final_text:
            final_text = "Задача завершена, но модель не вернула текстовый ответ."
            self.session.add_assistant_turn(AssistantTurn(content=final_text))

        cost = estimate_cost(dict(usage), self.llm.model, pricing or {})
        duration = int((time.perf_counter() - started) * 1000)
        await self._emit(
            RunFinished(
                run_id=run_id,
                text=final_text,
                steps=steps,
                duration_ms=duration,
                usage=dict(usage),
                cost_usd=round(cost.usd, 6),
            )
        )
        logger.info(
            "Задача завершена за %d мс, шагов: %d, токенов: %d, ~$%.4f",
            duration, steps, cost.total_tokens, cost.usd,
        )
        return RunResult(
            text=final_text, ok=True, steps=steps, usage=dict(usage), duration_ms=duration
        )

    # ------------------------------------------------------------------

    def _task_text(self, task: str, options: RunOptions) -> str:
        """Текст запроса вместе с содержимым текстовых вложений."""
        context = options.attachments.context()
        return f"{task}\n\n{context}" if context else task

    def _system_prompt(self, task: str = "") -> str:
        # Память = глобальная (о пользователе, кросс-чат) + пер-папочная (memory.md
        # этой рабочей папки: о проекте/чате и извлечённые уроки).
        from core.folder_memory import FolderMemory
        from core.reminders import ReminderStore

        parts = [self.memory.prompt_section(task), FolderMemory(self.settings.workspace).prompt_section()]
        # Сработавшие/активные напоминания этого чата — чтобы модель знала контекст
        # срабатывания и что она уже запланировала. После показа помечаем доставленными.
        reminders = ReminderStore(self.settings.data_dir)
        reminder_section = reminders.prompt_section(self.session.id)
        if reminder_section:
            parts.append(reminder_section)
            reminders.mark_delivered(self.session.id)
        memory = "\n\n".join(p for p in parts if p.strip())
        return build_system_prompt(
            settings=self.settings,
            registry=self.registry,
            skills=self.skills,
            extra=self._project_rules(),
            memory=memory,
        )

    def _project_rules(self) -> str:
        """Правила проекта из AGENTS.md рядом с workspace (если файл есть)."""
        for name in PROJECT_RULES_FILES:
            path = self.settings.workspace / name
            if path.exists() and path.is_file():
                try:
                    return path.read_text(encoding="utf-8", errors="replace")[:8000]
                except OSError:  # pragma: no cover
                    continue
        return ""

    async def _approve(self, request: Any) -> bool:
        """Прокси к текущему approver: подменять его можно после создания runner'а."""
        return await self.approver(request)

    async def _emit(self, event: Any) -> None:
        try:
            await self.emitter(event)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - UI не должен ломать выполнение задачи
            logger.debug("Не удалось отправить событие %s", type(event).__name__, exc_info=True)

    async def _log(self, text: str, level: str = "info") -> None:
        logger.log({"debug": 10, "info": 20, "warning": 30, "error": 40}[level], text)
        await self._emit(LogEvent(text=text, level=level))  # type: ignore[arg-type]


#: Насколько подрезаем каждое сообщение при формировании выжимки для резюме —
#: длинные результаты инструментов не должны раздуть сам запрос на сжатие.
_SUMMARY_MSG_LIMIT = 1500


def _format_for_summary(messages: list[dict[str, Any]]) -> str:
    """Превращает старые сообщения в компактный транскрипт для сжатия."""
    role_names = {"user": "Пользователь", "assistant": "Агент", "tool": "Инструмент", "system": "Система"}
    lines: list[str] = []
    for message in messages:
        role = role_names.get(message.get("role", ""), message.get("role", ""))
        content = message.get("content")
        if isinstance(content, list):
            # Мультимодальное сообщение: берём только текстовые части.
            content = " ".join(
                str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") == "text"
            )
        text = str(content or "").strip()

        calls = message.get("tool_calls") or []
        if calls:
            names = ", ".join(c.get("function", {}).get("name", "?") for c in calls)
            text = (text + f" [вызвал инструменты: {names}]").strip()

        if text:
            lines.append(f"{role}: {text[:_SUMMARY_MSG_LIMIT]}")
    return "\n".join(lines)
