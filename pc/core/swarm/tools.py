"""Инструменты общего чата — единственная связь агента с коллегами.

Каждому участнику выдаётся своя пара инструментов с «вшитым» именем автора,
поэтому подделать отправителя нельзя: агент физически может писать в чат только
от своего лица. Сообщения других агентов он получает либо этими инструментами,
либо автоматически в начале своего хода (см. оркестратор).

Категория `read`: запись в общий чат не трогает диск, сеть или систему, поэтому
подтверждения не требует — иначе эксперимент утонул бы в вопросах.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.events import SwarmMessage
from core.swarm.chat import GroupChat
from core.tools.base import Tool, ToolContext, ToolResult


class ChatSendArgs(BaseModel):
    message: str = Field(
        description=(
            "Сообщение коллегам в общий чат. Пиши как программист в командном чате: "
            "договаривайся, задавай вопросы, сообщай о готовности своей части, проси "
            "проверить или доделать то, что вне твоей зоны ответственности."
        )
    )


class ChatSendTool(Tool):
    name = "chat_send"
    description = (
        "Отправить сообщение в ОБЩИЙ ЧАТ команды. Это твой ЕДИНСТВЕННЫЙ способ связаться "
        "с коллегами — они не видят твои файлы и действия, только то, что ты написал сюда. "
        "Через чат согласуй интерфейсы, попроси сделать то, что вне твоей зоны, и сообщи, "
        "когда твоя часть готова."
    )
    Args = ChatSendArgs
    category = "read"
    dangerous = False
    timeout = None

    def __init__(self, chat: GroupChat, author: str, role: str = "") -> None:
        self._chat = chat
        self._author = author
        self._role = role

    async def run(self, args: ChatSendArgs, ctx: ToolContext) -> ToolResult:
        message = self._chat.post(self._author, args.message)
        # Публичный слой эксперимента: только это видят коллеги и интерфейс.
        await ctx.emitter(
            SwarmMessage(seq=message.seq, author=self._author, text=message.text, role=self._role)
        )
        return ToolResult(
            content=f"Отправлено в общий чат как #{message.seq}. Коллеги увидят это в свой ход."
        )


class ChatReadArgs(BaseModel):
    pass


class ChatReadTool(Tool):
    name = "chat_read"
    description = (
        "Прочитать ВЕСЬ общий чат команды с начала. Полезно, чтобы вспомнить, о чём "
        "договорились, и не переспрашивать то, что уже обсудили."
    )
    Args = ChatReadArgs
    category = "read"
    dangerous = False
    timeout = None

    def __init__(self, chat: GroupChat, author: str, role: str = "") -> None:
        self._chat = chat
        self._author = author
        self._role = role

    async def run(self, args: ChatReadArgs, ctx: ToolContext) -> ToolResult:
        return ToolResult(content=self._chat.transcript(author=self._author))


def chat_tools(chat: GroupChat, author: str, role: str = "") -> list[Tool]:
    """Пара инструментов чата для одного участника."""
    return [ChatSendTool(chat, author, role), ChatReadTool(chat, author, role)]
