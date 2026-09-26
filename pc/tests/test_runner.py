"""Тесты цикла агента — самая ценная страховка проекта.

Если вы меняли core/agent/runner.py и эти тесты упали — почти наверняка
сломали агента, а не тесты.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from core.agent.runner import AgentRunner
from core.agent.session import Session
from core.errors import LLMError
from core.llm.base import AssistantTurn, LLMClient
from core.tools.base import Tool, ToolContext
from core.tools.registry import ToolRegistry
from tests.fakes import EventCollector, ScriptedLLM, tool_call


class SteerInjectTool(Tool):
    """Инструмент-двойник: имитирует steering — кладёт реплику в очередь на лету."""

    name = "steer_inject"
    description = "Внутренний: добавляет уточнение пользователя в очередь steering."

    async def run(self, args: BaseModel, ctx: ToolContext) -> str:
        ctx.scratch.setdefault("steering", []).append("СТОП, сделай Y вместо X")
        return "ok"


class PingArgs(BaseModel):
    value: str = "pong"


class PingTool(Tool):
    name = "ping"
    description = "Возвращает значение."
    Args = PingArgs

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, args: PingArgs, ctx: ToolContext) -> str:
        self.calls.append(args.value)
        return f"pong:{args.value}"


def make_runner(turns, settings, tools=None, emitter=None):
    registry = ToolRegistry(tools if tools is not None else [PingTool()])
    return AgentRunner(
        llm=ScriptedLLM(turns),
        registry=registry,
        session=Session(),
        settings=settings,
        emitter=emitter or EventCollector(),
    )


async def test_drain_steering_injects_user_message(settings):
    """Реплика из очереди steering становится user-сообщением истории."""
    runner = make_runner([AssistantTurn(content="ok")], settings)
    runner.tool_context.scratch["steering"] = ["поменяй подход", ""]
    await runner._drain_steering()
    users = [m for m in runner.session.messages if m["role"] == "user"]
    assert users and "поменяй подход" in users[-1]["content"]
    assert runner.tool_context.scratch["steering"] == []  # очередь опустела


async def test_steering_reaches_model_next_step(settings):
    """Уточнение, поданное во время прогона, доходит до модели следующим шагом."""
    settings.approval_mode = "bypass"
    llm = ScriptedLLM(
        [
            AssistantTurn(tool_calls=[tool_call("steer_inject")]),
            AssistantTurn(content="учёл уточнение"),
        ]
    )
    runner = AgentRunner(
        llm=llm,
        registry=ToolRegistry([SteerInjectTool()]),
        session=Session(),
        settings=settings,
        emitter=EventCollector(),
    )
    await runner.run("сделай X")
    # В последнем запросе к модели уточнение присутствует как ход пользователя.
    last_messages = llm.calls[-1]["messages"]
    assert any(
        m.get("role") == "user" and "СТОП, сделай Y" in str(m.get("content"))
        for m in last_messages
    )


class _NamedTool(Tool):
    """Инструмент-заглушка с заданным именем (для проверки «ворот»)."""

    name = "placeholder"
    description = "Заглушка для тестов."
    Args = PingArgs

    def __init__(self, name: str) -> None:
        self.name = name

    async def run(self, args: PingArgs, ctx: ToolContext) -> str:
        return f"{self.name}:ok"


async def test_verification_gate_nudges_when_code_edited_without_check(settings):
    turns = [
        AssistantTurn(tool_calls=[tool_call("write_file", value="x")]),
        AssistantTurn(content="Готово"),          # первый финал → «ворота» заворачивают
        AssistantTurn(content="Проверять нечем"),  # второй финал → завершаем
    ]
    runner = make_runner(turns, settings, tools=[_NamedTool("write_file")])
    result = await runner.run("сделай правку")
    assert result.ok
    assert result.text == "Проверять нечем"
    # 3 обращения к модели: правка, завёрнутый финал, финал.
    assert len(runner.llm.calls) == 3


async def test_verification_gate_satisfied_by_running_tests(settings):
    turns = [
        AssistantTurn(tool_calls=[tool_call("write_file", value="x")]),
        AssistantTurn(tool_calls=[tool_call("run_tests", value="x")]),
        AssistantTurn(content="Готово"),  # проверка была → финал без заворота
    ]
    runner = make_runner(turns, settings, tools=[_NamedTool("write_file"), _NamedTool("run_tests")])
    result = await runner.run("сделай и проверь")
    assert result.text == "Готово"
    assert len(runner.llm.calls) == 3


async def test_verification_gate_disabled(settings):
    s = settings.model_copy(update={"verification_gate": False})
    turns = [
        AssistantTurn(tool_calls=[tool_call("write_file", value="x")]),
        AssistantTurn(content="Готово"),  # сразу финал, без заворота
    ]
    runner = make_runner(turns, s, tools=[_NamedTool("write_file")])
    result = await runner.run("правка")
    assert result.text == "Готово"
    assert len(runner.llm.calls) == 2


async def test_drain_notifications_delivers_as_note(settings):
    runner = make_runner([AssistantTurn(content="ok")], settings)
    runner.tool_context.scratch["notifications"] = ["сборка готова"]
    await runner._drain_notifications()
    assert runner.tool_context.scratch["notifications"] == []
    snap = runner.session.snapshot()
    assert any("сборка готова" in str(m.get("content", "")) for m in snap)


async def test_plain_answer_without_tools(settings):
    events = EventCollector()
    runner = make_runner([AssistantTurn(content="Готово.")], settings, emitter=events)

    result = await runner.run("привет")

    assert result.ok and result.text == "Готово."
    assert result.steps == 1
    assert "run.started" in events.types()
    assert "run.finished" in events.types()


async def test_tool_call_then_answer(settings):
    tool = PingTool()
    events = EventCollector()
    runner = make_runner(
        [
            AssistantTurn(tool_calls=[tool_call("ping", value="раз")]),
            AssistantTurn(content="Инструмент отработал."),
        ],
        settings,
        tools=[tool],
        emitter=events,
    )

    result = await runner.run("вызови ping")

    assert result.ok
    assert tool.calls == ["раз"]
    assert events.of("tool.started") and events.of("tool.finished")

    # История валидна: assistant с tool_calls -> tool с тем же id
    roles = [m["role"] for m in runner.session.messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    assert runner.session.messages[3]["tool_call_id"] == runner.session.messages[2]["tool_calls"][0]["id"]


async def test_parallel_tool_calls_all_answered(settings):
    tool = PingTool()
    runner = make_runner(
        [
            AssistantTurn(
                tool_calls=[
                    tool_call("ping", call_id="a", value="1"),
                    tool_call("ping", call_id="b", value="2"),
                ]
            ),
            AssistantTurn(content="ок"),
        ],
        settings,
        tools=[tool],
    )

    await runner.run("дважды")

    tool_messages = [m for m in runner.session.messages if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["a", "b"]
    assert sorted(tool.calls) == ["1", "2"]


async def test_unknown_tool_does_not_break_run(settings):
    runner = make_runner(
        [
            AssistantTurn(tool_calls=[tool_call("no_such_tool")]),
            AssistantTurn(content="понял, инструмента нет"),
        ],
        settings,
    )

    result = await runner.run("вызови несуществующее")

    assert result.ok
    tool_message = next(m for m in runner.session.messages if m["role"] == "tool")
    assert "не существует" in tool_message["content"]


async def test_repeated_identical_calls_warn_but_are_executed(settings):
    tool = PingTool()
    turns = [AssistantTurn(tool_calls=[tool_call("ping", value="x")]) for _ in range(4)]
    turns.append(AssistantTurn(content="готово"))
    settings.max_steps = 8
    runner = make_runner(turns, settings, tools=[tool])

    result = await runner.run("повтори")

    contents = [m["content"] for m in runner.session.messages if m["role"] == "tool"]
    assert result.ok
    assert len(tool.calls) == 4
    assert "ПРЕДУПРЕЖДЕНИЕ: это 4-й вызов" in contents[-1]


async def test_repeated_calls_are_blocked_only_at_configured_limit(settings):
    tool = PingTool()
    settings.max_steps = 25
    settings.repeat_call_block_limit = 20
    turns = [AssistantTurn(tool_calls=[tool_call("ping", value="same")]) for _ in range(20)]
    turns.append(AssistantTurn(content="итог: застрял"))
    runner = make_runner(turns, settings, tools=[tool])

    result = await runner.run("крутись на месте")

    assert result.ok
    assert "застрял" in result.text
    assert len(tool.calls) == 19
    contents = [m["content"] for m in runner.session.messages if m["role"] == "tool"]
    assert "20 раз и заблокирован" in contents[-1]


async def test_productive_run_not_stalled(settings):
    """Пока действия РАЗНЫЕ (прогресс), задача продолжается, а не глохнет."""
    tool = PingTool()
    settings.max_steps = 50
    settings.stall_limit = 3
    # Пять разных вызовов подряд — больше stall_limit, но каждый новый.
    turns = [AssistantTurn(tool_calls=[tool_call("ping", value=str(i))]) for i in range(5)]
    turns.append(AssistantTurn(content="всё сделано"))
    runner = make_runner(turns, settings, tools=[tool])

    result = await runner.run("делай разное")

    assert result.text == "всё сделано"
    assert tool.calls == ["0", "1", "2", "3", "4"]  # все выполнены, не оборвались


async def test_step_limit_forces_final_answer(settings):
    settings.max_steps = 2
    turns = [AssistantTurn(tool_calls=[tool_call("ping")]) for _ in range(2)]
    turns.append(AssistantTurn(content="итог после лимита"))
    runner = make_runner(turns, settings)

    result = await runner.run("бесконечная задача")

    assert result.ok
    assert result.text == "итог после лимита"
    # последний запрос к модели идёт без инструментов
    assert runner.llm.calls[-1]["tools"] is None


async def test_llm_error_is_reported_not_raised(settings):
    class BrokenLLM(LLMClient):
        model = "broken"

        async def complete(self, messages, *, tools=None, on_text=None, on_reasoning=None, on_tool_progress=None, on_retry=None):
            raise LLMError("провайдер недоступен")

    events = EventCollector()
    runner = AgentRunner(
        llm=BrokenLLM(),
        registry=ToolRegistry([PingTool()]),
        settings=settings,
        emitter=events,
    )

    result = await runner.run("задача")

    assert not result.ok
    assert "провайдер недоступен" in (result.error or "")
    assert events.of("run.failed")


async def test_cancellation_emits_event(settings):
    class HangingLLM(LLMClient):
        model = "hang"

        async def complete(self, messages, *, tools=None, on_text=None, on_reasoning=None, on_tool_progress=None, on_retry=None):
            await asyncio.sleep(10)
            return AssistantTurn(content="никогда")

    events = EventCollector()
    runner = AgentRunner(
        llm=HangingLLM(),
        registry=ToolRegistry(),
        settings=settings,
        emitter=events,
    )

    task = asyncio.create_task(runner.run("долгая задача"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_history_survives_second_task(settings):
    runner = make_runner(
        [AssistantTurn(content="первый"), AssistantTurn(content="второй")],
        settings,
    )
    await runner.run("задача 1")
    await runner.run("задача 2")

    user_messages = [m for m in runner.session.messages if m["role"] == "user"]
    assert [m["content"] for m in user_messages] == ["задача 1", "задача 2"]
    assert runner.session.messages[0]["role"] == "system"


async def test_empty_answer_gets_placeholder(settings):
    runner = make_runner([AssistantTurn(content="   ")], settings)
    result = await runner.run("молчи")
    assert result.ok
    assert result.text


class RememberTool(Tool):
    """Проверяет, что ctx.scratch — общая память между шагами."""

    name = "remember"
    description = "Считает свои вызовы через ctx.scratch."

    async def run(self, args, ctx: ToolContext) -> str:
        ctx.scratch["count"] = ctx.scratch.get("count", 0) + 1
        return f"вызов №{ctx.scratch['count']}"


async def test_scratch_survives_between_steps(settings):
    runner = make_runner(
        [
            AssistantTurn(tool_calls=[tool_call("remember", call_id="a")]),
            AssistantTurn(tool_calls=[tool_call("remember", call_id="b")]),
            AssistantTurn(content="готово"),
        ],
        settings,
        tools=[RememberTool()],
    )

    await runner.run("посчитай")

    outputs = [m["content"] for m in runner.session.messages if m["role"] == "tool"]
    assert outputs == ["вызов №1", "вызов №2"]


class RiskyTool(Tool):
    name = "risky"
    description = "Изменяет файлы."
    category = "edit"
    dangerous = True

    async def run(self, args, ctx: ToolContext) -> str:
        return "сделано"


async def test_approver_can_be_replaced_after_construction(settings):
    settings.approval_mode = "manual"
    asked: list[str] = []

    async def approver(request):
        asked.append(request.name)
        return True

    runner = make_runner(
        [AssistantTurn(tool_calls=[tool_call("risky")]), AssistantTurn(content="ок")],
        settings,
        tools=[RiskyTool()],
    )
    runner.approver = approver  # подмена после создания должна работать

    await runner.run("вызови risky")

    assert asked == ["risky"]


async def test_run_result_reports_duration(settings):
    """CLI печатает duration_ms — поле обязано существовать и заполняться."""
    runner = make_runner([AssistantTurn(content="готово")], settings)
    result = await runner.run("задача")
    assert result.duration_ms >= 0
    assert isinstance(result.duration_ms, int)


async def test_tool_progress_is_reported_before_execution(settings):
    """Пока модель диктует аргументы, интерфейс обязан показывать, что она делает.

    Регрессия: при записи большого файла экран замирал на десятки секунд —
    событие о вызове приходило только после того, как модель дописала всё.
    """
    events = EventCollector()
    runner = make_runner(
        [
            AssistantTurn(tool_calls=[tool_call("ping", value="x" * 500)]),
            AssistantTurn(content="готово"),
        ],
        settings,
        emitter=events,
    )

    await runner.run("длинная генерация")

    pending = events.of("tool.pending")
    assert pending, "события о надиктовке вызова не было"
    assert pending[0].name == "ping"
    assert pending[0].chars > 400
    # Прогресс приходит раньше самого запуска инструмента
    assert events.types().index("tool.pending") < events.types().index("tool.started")


# ------------------------------------------------- компакция контекста


async def test_long_history_is_compacted_into_summary(settings):
    """Переполненный контекст сворачивается в резюме, а не выбрасывается молча."""
    settings.context_token_budget = 200
    settings.context_compaction = True

    session = Session()
    session.set_system_prompt("system")
    # Набиваем историю, чтобы точно превысить бюджет.
    for _ in range(12):
        session.add_user("вопрос " + "x" * 400)
        session.add_assistant_turn(AssistantTurn(content="ответ " + "y" * 400))

    # Первый ответ модели используется как резюме, второй — как финальный ответ.
    llm = ScriptedLLM(
        [
            AssistantTurn(content="РЕЗЮМЕ: обсуждали настройку проекта и договорились о стиле."),
            AssistantTurn(content="Готово."),
        ]
    )
    runner = AgentRunner(llm=llm, registry=ToolRegistry([]), session=session, settings=settings)

    result = await runner.run("новый вопрос")
    assert result.ok

    # В истории появилась свёрнутая заметка с резюме, старьё ушло.
    joined = " ".join(str(m.get("content")) for m in session.messages if m.get("role") == "system")
    assert "свёрнуто" in joined
    assert "РЕЗЮМЕ" in joined


async def test_compaction_falls_back_to_trim_on_summary_failure(settings):
    """Если резюме не удалось — грубый выброс, но история остаётся валидной."""
    from core.errors import LLMError

    settings.context_token_budget = 200
    settings.context_compaction = True

    session = Session()
    session.set_system_prompt("system")
    for _ in range(12):
        session.add_user("вопрос " + "x" * 400)
        session.add_assistant_turn(AssistantTurn(content="ответ " + "y" * 400))

    class FlakySummaryLLM(ScriptedLLM):
        async def complete(self, messages, **kwargs):  # type: ignore[override]
            # Запрос на сжатие (есть max_tokens) — падает; обычный — по сценарию.
            if kwargs.get("max_tokens"):
                raise LLMError("сжатие недоступно")
            return await super().complete(messages, **kwargs)

    llm = FlakySummaryLLM([AssistantTurn(content="Готово.")])
    runner = AgentRunner(llm=llm, registry=ToolRegistry([]), session=session, settings=settings)

    result = await runner.run("вопрос")
    assert result.ok
    joined = " ".join(str(m.get("content")) for m in session.messages if m.get("role") == "system")
    assert "удалено" in joined  # сработал запасной trim


# ------------------------------------------------- health-gate + авто-откат


async def test_health_gate_blocks_then_passes(settings):
    """Красные проверки возвращают агента чинить; позеленело — завершаем."""
    turns = [
        AssistantTurn(tool_calls=[tool_call("write_file", value="x")]),
        AssistantTurn(content="Готово"),    # gate: красно → возврат
        AssistantTurn(content="Готово-2"),  # gate: зелено → финал
    ]
    runner = make_runner(turns, settings, tools=[_NamedTool("write_file")])
    calls = {"n": 0}

    async def fake_health():
        calls["n"] += 1
        return (True, calls["n"] > 1, "тест упал" if calls["n"] == 1 else "ok")

    runner._run_health_checks = fake_health
    result = await runner.run("правка")
    assert result.ok and result.text == "Готово-2"
    assert calls["n"] == 2  # прогнали дважды: красно, потом зелено


async def test_health_gate_rolls_back_when_stuck(settings, monkeypatch):
    """Если так и красно — откат всего прогона и честный финал (не «успех»)."""
    s = settings.model_copy(update={"health_gate_max_cycles": 1})
    turns = [
        AssistantTurn(tool_calls=[tool_call("write_file", value="x")]),
        AssistantTurn(content="Готово"),   # cycle 1 → возврат
        AssistantTurn(content="Готово"),   # исчерпано → откат + финальный вопрос
        AssistantTurn(content="Честно: не смог, откатил"),  # ответ без инструментов
    ]
    runner = make_runner(turns, s, tools=[_NamedTool("write_file")])

    async def fake_health():
        return (True, False, "красный тест")

    runner._run_health_checks = fake_health
    rolled = {"run": None}

    def fake_restore(run_id):
        rolled["run"] = run_id
        return {"restored": ["x.py"], "messages": [], "skipped": []}

    monkeypatch.setattr(runner.checkpoints, "restore_run", fake_restore)
    result = await runner.run("правка")
    assert rolled["run"], "откат прогона должен быть вызван при исчерпании попыток"
    assert result.text == "Честно: не смог, откатил"


async def test_health_gate_off_finishes_immediately(settings):
    """При выключенном health_gate код-правка завершается без авто-прогона тестов."""
    s = settings.model_copy(update={"health_gate": False, "verification_gate": False})
    turns = [
        AssistantTurn(tool_calls=[tool_call("write_file", value="x")]),
        AssistantTurn(content="Готово"),
    ]
    runner = make_runner(turns, s, tools=[_NamedTool("write_file")])
    called = {"n": 0}

    async def fake_health():
        called["n"] += 1
        return (True, False, "не должно вызываться")

    runner._run_health_checks = fake_health
    result = await runner.run("правка")
    assert result.text == "Готово" and called["n"] == 0


# ------------------------------------------------- стоп-кран по времени


async def test_time_budget_wraps_up(settings, monkeypatch):
    """Исчерпан лимит времени — не начинаем новый круг, просим итог без инструментов."""
    import core.agent.runner as runner_module

    s = settings.model_copy(update={"max_run_seconds": 1.0})
    turns = [
        AssistantTurn(tool_calls=[tool_call("ping")]),
        AssistantTurn(content="Стоп по времени: вот итог"),
    ]
    runner = make_runner(turns, s, tools=[PingTool()])
    # Управляемые «часы»: каждый вызов увеличивает время, поэтому бюджет
    # заведомо исчерпан сразу после первого хода модели.
    clock = {"t": 0.0}

    def fake_pc() -> float:
        clock["t"] += 10.0
        return clock["t"]

    monkeypatch.setattr(runner_module.time, "perf_counter", fake_pc)
    result = await runner.run("долгая задача")
    assert result.text == "Стоп по времени: вот итог"
    assert len(runner.llm.calls) == 2  # первый ход + финал по стоп-крану
    # Инструмент не должен был выполниться — стоп-кран сработал до круга инструментов.
    assert runner.registry.get("ping").calls == []
    # Маркер прогона снят и после остановки по лимиту.
    assert runner.run_state.interrupted(runner.session.id) is None


# ------------------------------------------------- след прогона (resume)


async def test_run_state_marker_present_during_run_and_cleared(settings):
    """Во время работы на диске лежит маркер `running`; после штатного конца — снят."""
    from core.agent.run_state import RunStateStore

    class _ProbeTool(Tool):
        name = "marker_probe"
        description = "Читает маркер прогона во время работы."
        Args = PingArgs

        def __init__(self, data_dir):
            self.data_dir = data_dir
            self.seen = None
            self.sid = ""

        async def run(self, args: PingArgs, ctx: ToolContext) -> str:
            self.seen = RunStateStore(self.data_dir).read(self.sid)
            return "ok"

    probe = _ProbeTool(settings.data_dir)
    turns = [
        AssistantTurn(tool_calls=[tool_call("marker_probe")]),
        AssistantTurn(content="Готово"),
    ]
    runner = make_runner(turns, settings, tools=[probe])
    probe.sid = runner.session.id
    result = await runner.run("задача")
    assert result.text == "Готово"
    assert probe.seen and probe.seen["status"] == "running"  # маркер был во время работы
    assert runner.run_state.interrupted(runner.session.id) is None  # снят после конца
