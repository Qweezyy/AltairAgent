"""Тестовые двойники: LLM со сценарием ответов и собиратель событий."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from core.events import Event
from core.llm.base import AssistantTurn, LLMClient, StreamCallback, ToolCall


class ScriptedLLM(LLMClient):
    """Отдаёт заранее заданные ответы по очереди.

    Пример:
        ScriptedLLM([
            AssistantTurn(tool_calls=[tool_call("read_file", path="a.txt")]),
            AssistantTurn(content="готово"),
        ])
    """

    def __init__(self, turns: list[AssistantTurn], model: str = "test/model") -> None:
        self.turns = list(turns)
        self.model = model
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        on_text: StreamCallback | None = None,
        on_reasoning: StreamCallback | None = None,
        on_tool_progress=None,
        on_retry=None,
        max_tokens: int | None = None,
    ) -> AssistantTurn:
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        turn = self.turns.pop(0) if self.turns else AssistantTurn(content="конец сценария")
        if turn.content and on_text:
            await on_text(turn.content)
        if turn.reasoning and on_reasoning:
            await on_reasoning(turn.reasoning)
        if turn.tool_calls and on_tool_progress:
            for call in turn.tool_calls:
                await on_tool_progress(call.name, len(call.arguments))
        return turn


class HangingLLM(LLMClient):
    """Модель, которая «думает» бесконечно.

    Нужна, чтобы проверять остановку задачи: с мгновенным ответом гонка не
    воспроизводится — задача успевает закончиться раньше нажатия «Стоп».
    """

    def __init__(self, model: str = "test/hanging") -> None:
        self.model = model
        self.started = asyncio.Event()

    async def complete(self, messages, **kwargs):  # type: ignore[override]
        self.started.set()
        await asyncio.sleep(3600)
        raise AssertionError("сюда попасть нельзя")


def tool_call(name: str, call_id: str | None = None, **arguments: Any) -> ToolCall:
    return ToolCall(
        id=call_id or f"call_{name}",
        name=name,
        arguments=json.dumps(arguments, ensure_ascii=False),
    )


class EventCollector:
    """Emitter, который просто складывает события в список."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    async def __call__(self, event: Event) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [event.type for event in self.events]

    def of(self, event_type: str) -> list[Event]:
        return [event for event in self.events if event.type == event_type]
