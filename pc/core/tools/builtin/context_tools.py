"""Инструменты самоконтроля контекстного окна: info / compress / drop.

Паритет с телефонным агентом. Модель может сама посмотреть заполнение окна,
свернуть старую часть диалога в резюме (которое пишет сама) или выбросить
тяжёлое (результаты инструментов, картинки). Правки идут по живой истории
сессии (`ctx.session`), поэтому эффект виден уже на следующем шаге прогона.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.agent.session import estimate_tokens
from core.events import ContextUsage
from core.tools.base import Tool, ToolContext, ToolResult


def _session(ctx: ToolContext) -> Any | None:
    return getattr(ctx, "session", None)


class ContextInfoTool(Tool):
    name = "context_info"
    description = (
        "Показывает состояние контекстного окна: сколько токенов занято из бюджета, "
        "процент, и разбивку (системный промпт / твои ответы / сообщения пользователя / "
        "результаты инструментов). Используй, если чувствуешь, что диалог разросся."
    )
    category = "read"
    timeout = 15.0

    async def run(self, args: BaseModel, ctx: ToolContext) -> str | ToolResult:
        session = _session(ctx)
        if session is None:
            return ToolResult.fail("контекст сессии недоступен")
        budget = ctx.settings.context_token_budget
        buckets = {"system": 0, "user": 0, "assistant": 0, "tool": 0}
        for message in session.messages:
            role = str(message.get("role") or "")
            key = role if role in buckets else "assistant"
            buckets[key] += estimate_tokens([message])
        used = sum(buckets.values())
        pct = used * 100 // budget if budget > 0 else 0
        free = max(budget - used, 0)

        def row(label: str, value: int) -> str:
            share = value * 100 // used if used > 0 else 0
            return f"  • {label}: {value} ({share}%)"

        return (
            f"Контекст: занято {used} из {budget} токенов ({pct}%). Свободно ~{free}.\n"
            "Разбивка:\n"
            + row("системный промпт", buckets["system"]) + "\n"
            + row("твои ответы", buckets["assistant"]) + "\n"
            + row("сообщения пользователя", buckets["user"]) + "\n"
            + row("результаты инструментов", buckets["tool"])
        )


class ContextCompressArgs(BaseModel):
    summary: str = Field(
        description="Краткое резюме сворачиваемой части (сохрани важные факты и решения) — пишешь его ТЫ"
    )
    keep_last: int = Field(
        default=4, ge=0, le=40, description="Сколько последних сообщений оставить как есть"
    )


class ContextCompressTool(Tool):
    name = "context_compress"
    description = (
        "Сжимает историю: заменяет всё, кроме последних keep_last сообщений, одним кратким "
        "резюме (его пишешь ТЫ в поле summary — сохрани важные факты и решения). Экономит "
        "контекст, сохраняя суть. Системный промпт и память не трогаются."
    )
    Args = ContextCompressArgs
    category = "edit"
    timeout = 15.0

    async def run(self, args: ContextCompressArgs, ctx: ToolContext) -> str | ToolResult:
        session = _session(ctx)
        if session is None:
            return ToolResult.fail("контекст сессии недоступен")
        summary = args.summary.strip()
        if not summary:
            return ToolResult.fail("нужно summary — краткое резюме сворачиваемой части")
        start = session._start_index()
        tail_len = len(session.messages) - start
        count = tail_len - args.keep_last
        if count <= 0:
            return "Сжимать нечего — сообщений слишком мало."
        removed = session.replace_prefix(
            count, f"[Сжатый контекст предыдущей части диалога]\n{summary}"
        )
        await ctx.emitter(ContextUsage(tokens=session.token_estimate()))
        return (
            f"Сжато: свёрнуто {removed} сообщений в резюме, оставлено последних {args.keep_last}. "
            f"Сейчас ~{session.token_estimate()} токенов."
        )


class ContextDropArgs(BaseModel):
    what: Literal["tools", "images"] = Field(
        description="tools — убрать все результаты инструментов; images — картинки из истории"
    )


class ContextDropTool(Tool):
    name = "context_drop"
    description = (
        "Убирает из контекста тяжёлое, не трогая видимый чат: what=tools (все результаты "
        "инструментов — они уже сыграли свою роль) | images (вложения-картинки из истории). "
        "Меняется только память модели."
    )
    Args = ContextDropArgs
    category = "edit"
    timeout = 15.0

    async def run(self, args: ContextDropArgs, ctx: ToolContext) -> str | ToolResult:
        session = _session(ctx)
        if session is None:
            return ToolResult.fail("контекст сессии недоступен")
        before = session.token_estimate()
        if args.what == "tools":
            session.messages = [_strip_tool_calls(m) for m in session.messages if m.get("role") != "tool"]
        else:  # images
            session.messages = [_strip_images(m) for m in session.messages]
        session.updated_at = time.time()
        after = session.token_estimate()
        await ctx.emitter(ContextUsage(tokens=after))
        return f"Убрано ({args.what}). Было ~{before}, стало ~{after} токенов."


def _strip_tool_calls(message: dict[str, Any]) -> dict[str, Any]:
    """Убирает tool_calls у assistant, иначе останутся «висячие» вызовы без ответов
    (провайдер вернёт 400, когда tool-сообщения удалены)."""
    if message.get("role") == "assistant" and message.get("tool_calls"):
        clone = dict(message)
        clone.pop("tool_calls", None)
        if not str(clone.get("content") or "").strip():
            clone["content"] = "[вызовы инструментов свёрнуты]"
        return clone
    return message


def _strip_images(message: dict[str, Any]) -> dict[str, Any]:
    """Убирает картиночные части из мультимодального user-сообщения, оставляя текст."""
    content = message.get("content")
    if not isinstance(content, list):
        return message
    text_parts = [p for p in content if isinstance(p, dict) and p.get("type") == "text"]
    if len(text_parts) == len(content):
        return message  # картинок не было
    clone = dict(message)
    if not text_parts:
        clone["content"] = "[изображение убрано из контекста]"
    elif len(text_parts) == 1:
        clone["content"] = text_parts[0].get("text", "")
    else:
        clone["content"] = text_parts
    return clone
