"""Оркестратор группового чата: равноправные агенты по очереди делают ходы.

Модель эксперимента:
  * все участники равноправны и решают ОДНУ задачу;
  * каждый видит только общий чат — не действия и не файлы коллег;
  * у каждого своя зона и свой урезанный набор инструментов, поэтому в одиночку
    задачу закрыть нельзя;
  * ход за ходом (round-robin) участник читает новое из чата, работает в своей
    зоне и пишет коллегам. Раунды идут, пока в чате появляются новые сообщения;
    как только целый круг прошёл молча — команда либо закончила, либо застряла,
    и эксперимент останавливается.

Каждый ход — это один запуск `AgentRunner.run()` поверх ПЕРСИСТЕНТНОЙ сессии
участника: вся его личная история (рассуждения, вызовы инструментов) копится у
него и не видна другим. Наружу выходит только то, что он отправил в общий чат.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.agent.runner import AgentRunner
from core.agent.session import Session
from core.events import (
    Emitter,
    SwarmActivity,
    SwarmFinished,
    SwarmMessage,
    SwarmTurnStarted,
    ToolFinished,
    noop_emitter,
)
from core.llm.base import LLMClient
from core.logging_setup import get_logger
from core.security.approval import Approver, always_allow
from core.settings import Settings, get_settings
from core.swarm.chat import ChatMessage, GroupChat
from core.swarm.member import MemberSpec
from core.swarm.tools import chat_tools
from core.tools.registry import ToolRegistry

logger = get_logger("swarm")

#: Автор системных сообщений в чате (постановка задачи).
CLIENT_NAME = "Заказчик"

#: Потолок шагов на ОДИН ход участника: ход — это короткий рабочий заход, а не
#: целая задача. Иначе один агент за ход мог бы всё сделать сам.
_TURN_MAX_STEPS = 18


def _charter_text(spec: MemberSpec, others: list[MemberSpec]) -> str:
    """Дописка к системному промпту: правила эксперимента и зона участника."""
    colleagues = "\n".join(f"  • {o.name} — {o.role}" for o in others) or "  (ты работаешь один)"
    return (
        "=== РЕЖИМ: ГРУППОВОЙ ЧАТ КОМАНДЫ ===\n"
        f"Тебя зовут {spec.name}, твоя роль — {spec.role}. Ты работаешь в команде "
        "программистов над ОДНОЙ общей задачей.\n\n"
        "Твои коллеги (ты знаешь об их существовании, но НЕ видишь их действий и файлов):\n"
        f"{colleagues}\n\n"
        "Жёсткие правила эксперимента:\n"
        "  1. Единственный способ связи с коллегами — инструмент chat_send. Всё остальное "
        "(твои файлы, вызовы инструментов, размышления) коллегам не видно.\n"
        "  2. Работай строго в своей зоне ответственности. Того, что вне неё, у тебя нет "
        "и в инструментах — это сделают коллеги. Проси их об этом в чате.\n"
        "  3. В одиночку задачу не закрыть — это нормально. Согласуй интерфейсы и порядок "
        "работы с командой, не дублируй чужую зону.\n"
        "  4. Каждый ход заканчивай осмысленным сообщением в чат: что сделал, что нужно от "
        "коллег, чего ждёшь. Если сейчас делать нечего — коротко скажи это в чат.\n"
        "  5. Пиши по-деловому и кратко, как в рабочем мессенджере.\n\n"
        "Твоя зона ответственности:\n"
        f"{spec.charter}\n"
        "=== КОНЕЦ ПРАВИЛ РЕЖИМА ==="
    )


class SwarmAgent(AgentRunner):
    """Участник команды: обычный `AgentRunner` с уставом в системном промпте."""

    def __init__(
        self,
        spec: MemberSpec,
        others: list[MemberSpec],
        *,
        llm: LLMClient,
        registry: ToolRegistry,
        settings: Settings,
        emitter: Emitter,
        approver: Approver,
    ) -> None:
        super().__init__(
            llm=llm,
            registry=registry,
            session=Session(workspace=str(settings.workspace)),
            settings=settings,
            emitter=emitter,
            approver=approver,
        )
        self.spec = spec
        self._others = others
        #: Последнее прочитанное этим участником сообщение общего чата.
        self.seen_seq = 0

    def _system_prompt(self, task: str = "") -> str:  # type: ignore[override]
        base = super()._system_prompt(task)
        return f"{base}\n\n{_charter_text(self.spec, self._others)}"


#: Инструменты чата не показываем как «действие» — они и есть публичный слой.
_CHAT_TOOLS = frozenset({"chat_send", "chat_read"})


def _member_emitter(parent: Emitter, name: str) -> Emitter:
    """Эмиттер участника. Наружу идут только:
      * сообщения общего чата (SwarmMessage) — публичный слой, его видят все;
      * приватные действия участника (ToolFinished → SwarmActivity) — их видит
        только человек-наблюдатель, но НЕ агенты-коллеги.
    Личный поток текста и размышлений участника не публикуется вовсе.
    """

    async def emit(event: Any) -> None:
        try:
            if isinstance(event, SwarmMessage):
                await parent(event)
                return
            if isinstance(event, ToolFinished) and event.name not in _CHAT_TOOLS:
                detail = "" if event.ok else (event.output or "")[:200]
                await parent(SwarmActivity(member=name, tool=event.name, ok=event.ok, detail=detail))
                return
            # Остальное (text/reasoning/логи/шаги) — приватно, наружу не идёт.
        except Exception:  # noqa: BLE001 - проблемы форвардинга не должны ронять ход
            logger.debug("Сбой форвардинга события участника %s", name, exc_info=True)

    return emit


@dataclass(slots=True)
class SwarmResult:
    """Итог эксперимента."""

    messages: list[ChatMessage] = field(default_factory=list)
    rounds_run: int = 0
    stopped_reason: str = ""

    def transcript(self) -> str:
        if not self.messages:
            return "(команда не обменялась ни одним сообщением)"
        return "\n".join(m.render() for m in self.messages)


class Swarm:
    """Запускает групповой чат агентов над одной задачей."""

    def __init__(
        self,
        task: str,
        members: list[MemberSpec],
        *,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        llm_factory: Callable[[MemberSpec], LLMClient] | None = None,
        model: str | None = None,
        emitter: Emitter = noop_emitter,
        approver: Approver = always_allow,
        max_rounds: int = 6,
    ) -> None:
        if not task.strip():
            raise ValueError("Пустая задача для группового чата.")
        if len(members) < 2:
            raise ValueError("Групповому чату нужно минимум двое участников.")
        names = [m.name for m in members]
        if len(set(names)) != len(names):
            raise ValueError("Имена участников должны быть уникальными.")

        self.task = task.strip()
        self.members = members
        self.settings = settings or get_settings()
        self.emitter = emitter
        self.approver = approver
        #: Максимум ходов на ОДНОГО участника (его личный бюджет активаций).
        self.max_rounds = max(1, max_rounds)
        self.chat = GroupChat()
        #: Общий клиент (если задан) — иначе каждому участнику свой (для настоящей
        #: параллельности запросов к модели). llm_factory перекрывает оба.
        self._shared_llm = llm
        self._llm_factory = llm_factory
        self._model = model
        self._agents: list[SwarmAgent] = []
        #: Клиенты, которые создали МЫ и обязаны закрыть.
        self._owned_llms: list[LLMClient] = []
        self._stop_reason = "исчерпан лимит ходов"

    # ------------------------------------------------------------------

    def _build_registry(self, spec: MemberSpec) -> ToolRegistry:
        """Реестр участника: разрешённые ему инструменты + инструменты чата."""
        from core.tools.builtin import builtin_tools

        allowed = set(spec.tools)
        tools = [t for t in builtin_tools() if t.name in allowed]
        missing = allowed - {t.name for t in tools}
        if missing:
            logger.warning("Участник %s: неизвестные инструменты пропущены: %s", spec.name, missing)
        tools.extend(chat_tools(self.chat, spec.name, spec.role))
        return ToolRegistry(tools)

    def _llm_for(self, spec: MemberSpec) -> LLMClient:
        """LLM-клиент участника. Свой на каждого — чтобы запросы шли параллельно."""
        if self._llm_factory is not None:
            return self._llm_factory(spec)
        if self._shared_llm is not None:
            return self._shared_llm
        import core.llm as llm_mod

        client = llm_mod.build_llm_client(self._model, self.settings)
        self._owned_llms.append(client)
        return client

    def _build_agents(self) -> None:
        # Настройки на участника: ход короткий, «ворота проверки» выключены —
        # у участников намеренно нет части проверяющих инструментов.
        member_settings = self.settings.model_copy(
            update={"max_steps": min(self.settings.max_steps, _TURN_MAX_STEPS), "verification_gate": False}
        )

        for spec in self.members:
            others = [m for m in self.members if m.name != spec.name]
            agent = SwarmAgent(
                spec,
                others,
                llm=self._llm_for(spec),
                registry=self._build_registry(spec),
                settings=member_settings,
                emitter=_member_emitter(self.emitter, spec.name),
                approver=self.approver,
            )
            self._agents.append(agent)

    def _turn_task(self, new_messages: list[ChatMessage], *, first: bool) -> str:
        """Текст, который получает участник на свой ход."""
        parts: list[str] = []
        if first:
            parts.append(
                "Команда приступает к общей задаче. Все работают ОДНОВРЕМЕННО. Изучи задачу и "
                "общий чат, затем начни работу в своей зоне и напиши коллегам в chat_send."
            )
        else:
            parts.append("Пока ты работал, коллеги написали в общий чат. Твой следующий ход.")

        if new_messages:
            body = "\n".join(m.render() for m in new_messages)
            parts.append(f"Новое в общем чате:\n{body}")

        parts.append(
            "Сделай полезное действие в своей зоне (если есть что делать) и ОБЯЗАТЕЛЬНО заверши "
            "ход сообщением в общий чат через chat_send. Коллеги работают параллельно — не жди "
            "их простаивая, делай свою часть и координируйся сообщениями. Если сейчас тебе нужен "
            "результат коллеги — коротко попроси его в чат и заверши ход."
        )
        return "\n\n".join(parts)

    def _fresh_for(self, agent: SwarmAgent) -> list[ChatMessage]:
        """Новые для участника сообщения — от КОГО УГОДНО, кроме него самого."""
        return [m for m in self.chat.since(agent.seen_seq) if m.author != agent.spec.name]

    async def run(self) -> SwarmResult:
        """Запускает всех участников ОДНОВРЕМЕННО и ждёт схождения команды.

        Каждый участник — отдельная задача event loop: они думают и пишут в чат
        параллельно. Участник, которому нечего обрабатывать, засыпает до появления
        нового сообщения. Когда ВСЕ уснули одновременно — обсуждать больше нечего,
        и эксперимент завершается.
        """
        run_id = uuid.uuid4().hex[:8]
        self._build_agents()

        # Постановка задачи — общая точка отсчёта, её видят все участники.
        task_msg = self.chat.post(CLIENT_NAME, f"Задача команде: {self.task}")
        await self.emitter(
            SwarmMessage(seq=task_msg.seq, author=CLIENT_NAME, text=task_msg.text, role="заказчик")
        )

        # Координация схождения: условие + счётчик «спящих». Когда спящих столько
        # же, сколько участников, никто уже не проснётся сам — команда сошлась.
        cond = asyncio.Condition()
        state = {"idle": 0, "stopped": False}
        total = len(self._agents)

        try:
            await asyncio.gather(*(self._agent_loop(a, cond, state, total) for a in self._agents))
        finally:
            for client in self._owned_llms:
                await client.aclose()

        await self.emitter(
            SwarmFinished(rounds_run=0, reason=self._stop_reason, message_count=len(self.chat.all()))
        )
        logger.info("Групповой чат [%s] завершён: %s", run_id, self._stop_reason)
        return SwarmResult(messages=self.chat.all(), rounds_run=0, stopped_reason=self._stop_reason)

    async def _agent_loop(
        self, agent: SwarmAgent, cond: asyncio.Condition, state: dict[str, Any], total: int
    ) -> None:
        """Жизненный цикл одного участника: работать, пока в чате есть на что реагировать."""
        first = True
        turns = 0
        while True:
            # Решение «работать или ждать» принимаем под замком: иначе сообщение,
            # пришедшее между проверкой и засыпанием, потерялось бы (lost wakeup).
            async with cond:
                if state["stopped"]:
                    return
                fresh = self._fresh_for(agent)
                capped = turns >= self.max_rounds
                if first or (fresh and not capped):
                    agent.seen_seq = self.chat.last_seq()
                    first = False
                    take_turn = True
                else:
                    # Нечего делать (или исчерпан личный бюджет) — засыпаем.
                    state["idle"] += 1
                    if state["idle"] >= total:
                        # Все уснули одновременно — расходимся.
                        state["stopped"] = True
                        self._stop_reason = (
                            "исчерпан лимит ходов у всех"
                            if capped
                            else "все участники высказались — обсуждать нечего"
                        )
                        cond.notify_all()
                        state["idle"] -= 1
                        return
                    await self.emitter(SwarmActivity(member=agent.spec.name, tool="ждёт коллег", ok=True))
                    await cond.wait()
                    state["idle"] -= 1
                    take_turn = False

            if not take_turn:
                continue

            turns += 1
            await self.emitter(SwarmTurnStarted(member=agent.spec.name))
            try:
                await agent.run(self._turn_task(fresh, first=(turns == 1)))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - падение одного не рушит эксперимент
                logger.exception("Ход участника %s завершился ошибкой", agent.spec.name)
                await self.emitter(
                    SwarmActivity(member=agent.spec.name, tool="(ход)", ok=False, detail="ход прерван ошибкой")
                )

            # Появились новые сообщения — будим спящих коллег.
            async with cond:
                cond.notify_all()
