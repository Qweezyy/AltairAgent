"""Нативный клиент Anthropic Messages API (прямые ключи api.anthropic.com).

Ядро говорит на «OpenAI-диалекте» (роли system/user/assistant/tool, tool_calls в
assistant, отдельное tool-сообщение с результатом). Здесь этот формат
конвертируется в формат Anthropic (system отдельным полем; tool_use/tool_result
блоками) и обратно — наружу отдаётся тот же AssistantTurn, что и у OpenAI-клиента,
поэтому цикл агента не меняется.

Стриминг — SSE через httpx (без SDK: httpx уже есть, лишней зависимости нет).
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any

import httpx

from core.errors import ConfigError, LLMError
from core.llm.base import (
    CACHE_BREAKPOINT,
    AssistantTurn,
    LLMClient,
    RetryCallback,
    StreamCallback,
    ToolCall,
    ToolProgressCallback,
)
from core.logging_setup import get_logger
from core.settings import Settings, get_settings

logger = get_logger("llm.anthropic")

_API_VERSION = "2023-06-01"
_DEFAULT_MAX_TOKENS = 8192


def is_anthropic_endpoint(base_url: str) -> bool:
    """Похоже ли на нативный Anthropic-эндпоинт (а не OpenAI-совместимый прокси)."""
    return "api.anthropic.com" in (base_url or "").lower()


def _tools_to_anthropic(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """OpenAI-схема инструментов -> Anthropic (input_schema вместо parameters)."""
    out: list[dict[str, Any]] = []
    for t in tools or []:
        fn = t.get("function") or {}
        if not fn.get("name"):
            continue
        out.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return out


def _system_blocks(text: str) -> Any:
    """system как строка или блоки с cache_control на стабильном префиксе."""
    if CACHE_BREAKPOINT in text:
        stable, volatile = text.split(CACHE_BREAKPOINT, 1)
        stable, volatile = stable.rstrip(), volatile.strip()
    else:
        stable, volatile = text, ""
    blocks: list[dict[str, Any]] = [
        {"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}}
    ]
    if volatile:
        blocks.append({"type": "text", "text": volatile})
    return blocks


def _text_of(content: Any) -> str:
    """Достаёт текст из OpenAI-сообщения (строка или мультимодальные части)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(p.get("text", "")) for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return "" if content is None else str(content)


def to_anthropic_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """OpenAI-сообщения -> (system, messages) в формате Anthropic.

    Результаты инструментов (роль tool) идут одним user-сообщением с блоками
    tool_result сразу после assistant с tool_use — как требует Anthropic.
    Подряд идущие tool-сообщения объединяются в один user-ход.
    """
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []

    def flush_pending(pending: list[dict[str, Any]]) -> None:
        if pending:
            out.append({"role": "user", "content": list(pending)})
            pending.clear()

    pending_tool: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            flush_pending(pending_tool)
            system_parts.append(_text_of(m.get("content")))
            continue
        if role == "tool":
            pending_tool.append({
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id") or m.get("id") or "",
                "content": _text_of(m.get("content")),
            })
            continue
        flush_pending(pending_tool)
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            text = _text_of(m.get("content"))
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                blocks.append({
                    "type": "tool_use", "id": tc.get("id") or "",
                    "name": fn.get("name") or "", "input": args if isinstance(args, dict) else {},
                })
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
        else:  # user
            out.append({"role": "user", "content": _text_of(m.get("content"))})
    flush_pending(pending_tool)

    return "\n\n".join(p for p in system_parts if p.strip()), out


class AnthropicClient(LLMClient):
    def __init__(
        self,
        settings: Settings | None = None,
        model: str | None = None,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.model = model or self.settings.default_model
        self.base_url = (base_url or self.settings.llm_base_url or "https://api.anthropic.com/v1").rstrip("/")
        self._api_key = api_key or self.settings.llm_api_key_effective
        if not self._api_key:
            raise ConfigError("Не задан ключ Anthropic (x-api-key). Добавьте ключ в настройках.")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.settings.llm_timeout, connect=20.0, read=self.settings.llm_timeout, write=30.0, pool=30.0),
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": _API_VERSION,
                "content-type": "application/json",
            },
        )

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
        system, conv = to_anthropic_messages(messages)
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or self.settings.llm_max_tokens or _DEFAULT_MAX_TOKENS,
            "messages": conv,
            "stream": True,
        }
        if system:
            body["system"] = _system_blocks(system)
        if self.settings.llm_temperature is not None:
            body["temperature"] = self.settings.llm_temperature
        anth_tools = _tools_to_anthropic(tools)
        if anth_tools:
            body["tools"] = anth_tools

        attempts = max(5, self.settings.llm_max_retries)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await self._stream_once(body, on_text, on_reasoning, on_tool_progress)
            except (httpx.RequestError, _RetryStatus) as exc:
                last_error = exc
                if attempt == attempts:
                    break
                delay = min(2 ** attempt, 10) + random.uniform(0.2, 0.8)
                logger.warning("Anthropic: сбой (%s), повтор %d/%d через %.1f с", exc, attempt, attempts, delay)
                if on_retry:
                    try:
                        await on_retry(attempt + 1, attempts, delay, str(exc)[:160])
                    except Exception:  # noqa: BLE001
                        pass
                await asyncio.sleep(delay)
            except LLMError:
                raise
        raise LLMError(
            f"Не удалось связаться с Anthropic-моделью «{self.model}» после {attempts} попыток. "
            f"Последняя ошибка: {str(last_error)[:300]}."
        ) from last_error

    async def _stream_once(
        self,
        body: dict[str, Any],
        on_text: StreamCallback | None,
        on_reasoning: StreamCallback | None,
        on_tool_progress: ToolProgressCallback | None,
    ) -> AssistantTurn:
        turn = AssistantTurn(model=self.model)
        blocks: dict[int, dict[str, Any]] = {}
        reported: dict[int, int] = {}
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        async with self._client.stream("POST", f"{self.base_url}/messages", json=body) as resp:
            if resp.status_code >= 400:
                raw = (await resp.aread()).decode("utf-8", "replace")
                if resp.status_code in (408, 429, 500, 502, 503, 504, 529):
                    raise _RetryStatus(f"HTTP {resp.status_code}: {raw[:200]}")
                if resp.status_code in (401, 403):
                    raise LLMError(f"Anthropic отклонил ключ ({resp.status_code}). Проверьте x-api-key в настройках.")
                raise LLMError(f"Anthropic API {resp.status_code}: {raw[:300]}")

            event = ""
            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                try:
                    data = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                et = data.get("type") or event

                if et == "message_start":
                    u = (data.get("message") or {}).get("usage") or {}
                    usage["prompt_tokens"] = u.get("input_tokens", 0) or 0
                    # input_tokens leaves out the cached prefix; the context is all of it.
                    usage["context_tokens"] = (usage["prompt_tokens"] + (u.get("cache_read_input_tokens") or 0)
                                               + (u.get("cache_creation_input_tokens") or 0))
                elif et == "content_block_start":
                    idx = data.get("index", 0)
                    cb = data.get("content_block") or {}
                    blocks[idx] = cb
                    if cb.get("type") == "tool_use":
                        blocks[idx] = {"type": "tool_use", "id": cb.get("id", ""), "name": cb.get("name", ""), "json": ""}
                        if on_tool_progress and cb.get("name"):
                            await on_tool_progress(cb["name"], 0)
                elif et == "content_block_delta":
                    idx = data.get("index", 0)
                    d = data.get("delta") or {}
                    dt = d.get("type")
                    if dt == "text_delta":
                        txt = d.get("text") or ""
                        turn.content += txt
                        if txt and on_text:
                            await on_text(txt)
                    elif dt == "thinking_delta":
                        r = d.get("thinking") or ""
                        turn.reasoning += r
                        if r and on_reasoning:
                            await on_reasoning(r)
                    elif dt == "input_json_delta":
                        b = blocks.get(idx)
                        if b is not None and b.get("type") == "tool_use":
                            b["json"] += d.get("partial_json") or ""
                            if on_tool_progress and b.get("name"):
                                size = len(b["json"])
                                if size - reported.get(idx, -1) >= 400 or reported.get(idx, -1) < 0:
                                    reported[idx] = size
                                    await on_tool_progress(b["name"], size)
                elif et == "message_delta":
                    d = data.get("delta") or {}
                    if d.get("stop_reason"):
                        turn.finish_reason = d["stop_reason"]
                    u = data.get("usage") or {}
                    if u.get("output_tokens"):
                        usage["completion_tokens"] = u["output_tokens"]
                elif et == "error":
                    err = data.get("error") or {}
                    raise LLMError(f"Anthropic: {err.get('type', 'error')}: {err.get('message', '')}")

        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        turn.usage = usage
        turn.tool_calls = [
            ToolCall(id=b.get("id") or f"call_{i}", name=b.get("name", ""), arguments=b.get("json") or "{}")
            for i, b in sorted(blocks.items())
            if b.get("type") == "tool_use" and b.get("name")
        ]
        return turn

    async def aclose(self) -> None:
        await self._client.aclose()


class _RetryStatus(Exception):
    """Внутренний маркер повторяемого HTTP-статуса."""
