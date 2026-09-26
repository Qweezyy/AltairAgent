"""Абстракция над LLM-провайдером.

Ядро агента общается только с этим интерфейсом. Чтобы подключить другого
провайдера (Anthropic, локальная модель, mock в тестах) — реализуйте
`LLMClient.complete()` и передайте объект в AgentRunner. Менять цикл агента
при этом не нужно.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

#: Разделитель системного промпта на «стабильный префикс» и «изменчивый хвост».
#: build_system_prompt вставляет его между неизменной частью (правила, инструменты,
#: навыки) и меняющейся (дата, память, напоминания). Клиент режет по нему и ставит
#: точку кэширования (cache_control) на стабильный префикс. В запрос провайдеру сам
#: маркер не попадает. Символы-разделители группы (U+241E) почти не встречаются в тексте.
CACHE_BREAKPOINT = "␞␞::CACHE_BREAKPOINT::␞␞"

#: Колбэк стриминга: получает очередной кусок текста.
StreamCallback = Callable[[str], Awaitable[None]]

#: Прогресс надиктовки вызова инструмента: (имя, сколько символов аргументов).
ToolProgressCallback = Callable[[str, int], Awaitable[None]]

#: Уведомление о повторной попытке: (номер попытки, всего попыток, пауза в сек, причина).
#: Нужно, чтобы интерфейс во время переподключения не выглядел зависшим.
RetryCallback = Callable[[int, int, float, str], Awaitable[None]]


@dataclass(slots=True)
class ToolCall:
    """Запрос модели на вызов инструмента."""

    id: str
    name: str
    arguments: str = ""

    def parsed_arguments(self) -> dict[str, Any]:
        """Аргументы как dict; при кривом JSON — пустой словарь (валидация будет в Tool)."""
        try:
            data = json.loads(self.arguments or "{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def to_message_part(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments or "{}"},
        }


@dataclass(slots=True)
class AssistantTurn:
    """Результат одного обращения к модели."""

    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)

    def to_message(self) -> dict[str, Any]:
        """Сообщение assistant для истории диалога.

        Reasoning намеренно НЕ включается: провайдеры по-разному его валидируют,
        а для логики он не нужен.
        """
        message: dict[str, Any] = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            message["tool_calls"] = [tc.to_message_part() for tc in self.tool_calls]
        return message


class LLMClient(ABC):
    """Минимальный контракт провайдера."""

    model: str

    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        on_text: StreamCallback | None = None,
        on_reasoning: StreamCallback | None = None,
        on_tool_progress: ToolProgressCallback | None = None,
        on_retry: RetryCallback | None = None,
        max_tokens: int | None = None,
    ) -> AssistantTurn:
        """Один запрос к модели. Должен либо вернуть AssistantTurn, либо кинуть LLMError."""

    async def aclose(self) -> None:
        """Освобождение ресурсов (по умолчанию ничего не делает)."""
        return None
