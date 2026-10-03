"""Клиент к любому OpenAI-совместимому API (OpenRouter, Ollama, LM Studio, vLLM).

Отвечает за: стриминг, сборку tool_calls из чанков, повторы при временных
сбоях и понятные сообщения об ошибках.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx
import openai
from openai import AsyncOpenAI

from core.errors import ConfigError, LLMError
from core.i18n import tr
from core.llm.base import (
    CACHE_BREAKPOINT,
    AssistantTurn,
    LLMClient,
    RetryCallback,
    StreamCallback,
    ToolCall,
    ToolProgressCallback,
)
from core.llm import reliability
from core.llm.reliability import BadTurn
from core.logging_setup import get_logger
from core.settings import Settings, get_settings

logger = get_logger("llm")

#: Ошибки, которые имеет смысл повторить (включая обрывы соединений upstream-провайдеров).
RETRYABLE = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
    openai.APIError,
    httpx.RequestError,
    httpx.RemoteProtocolError,
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
)


class OpenAICompatClient(LLMClient):
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
        # Переопределения base_url/api_key важнее настроек: так один прогон может
        # использовать модель у ДРУГОГО провайдера (маршрутизация по сложности).
        self.base_url = base_url or self.settings.llm_base_url
        api_key = api_key or self.settings.llm_api_key_effective
        if not api_key:
            raise ConfigError(
                "Не задан LLM_API_KEY. Добавьте ключ в .env "
                "(для локальных моделей подойдёт любая непустая строка)."
            )
        headers = {}
        if "openrouter" in self.base_url:
            headers = {"HTTP-Referer": "http://localhost", "X-Title": "Local AI Agent"}

        # Настраиваем httpx Timeout с раздельными таймаутами на чтение и подключение
        custom_timeout = httpx.Timeout(
            timeout=self.settings.llm_timeout,
            connect=20.0,
            read=self.settings.llm_timeout,
            write=30.0,
            pool=30.0,
        )

        self._client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=api_key,
            timeout=custom_timeout,
            max_retries=0,  # повторы делаем сами, чтобы корректно логировать и восстанавливать стрим
            default_headers=headers,
        )
        #: Кэширование промпта (cache_control) поддерживают модели Anthropic (в т.ч.
        #: через OpenRouter). Для остальных провайдеров этот маркер бессмыслен/ломает
        #: запрос, поэтому включаем только для claude-моделей.
        self._caching = _supports_cache(self.model)
        #: False once the provider refused our reasoning field (then it is not sent again).
        self._reasoning_supported = True
        #: Requests in flight to this provider are shared by every client that talks to it.
        self._gate = reliability.gate(self.base_url, self.settings.llm_max_concurrent)

    # ------------------------------------------------------------------

    def _prepare_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Готовит системный промпт к отправке: режет по маркеру кэширования.

        Для Anthropic — ставит cache_control на стабильный префикс (кэш-хит на
        шагах прогона и между близкими запросами экономит до ~90% входных токенов).
        Для прочих провайдеров — просто убирает маркер, склеивая префикс и хвост.
        Трогает только первое сообщение (системный промпт); заметки-`system` в
        середине истории проходят как есть.
        """
        if not messages:
            return messages
        first = messages[0]
        if first.get("role") != "system" or not isinstance(first.get("content"), str):
            return messages
        content: str = first["content"]
        if CACHE_BREAKPOINT in content:
            stable, volatile = content.split(CACHE_BREAKPOINT, 1)
            stable, volatile = stable.rstrip(), volatile.strip()
        else:
            stable, volatile = content, ""

        if self._caching:
            blocks: list[dict[str, Any]] = [
                {"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}}
            ]
            if volatile:
                blocks.append({"type": "text", "text": volatile})
            new_first = {**first, "content": blocks}
        else:
            joined = stable + (f"\n\n{volatile}" if volatile else "")
            new_first = {**first, "content": joined}
        return [new_first, *messages[1:]]

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
        attempts = self.max_attempts or max(5, self.settings.llm_max_retries)
        last_error: Exception | None = None
        error_details = []
        cuts = 0

        async def _retry(attempt: int, exc: Exception, reason: str, base: float) -> None:
            """Единая точка повтора: логирует, уведомляет UI и ждёт с backoff.

            Все ветки повторов проходят через неё, чтобы пользователь ГАРАНТИРОВАННО
            видел «идёт переподключение», а не тишину до финальной ошибки.
            """
            delay = min(2 ** attempt, 10) + random.uniform(base, base + 0.6)
            logger.warning(
                "Связь с моделью нарушена (%s), попытка %d/%d, повтор через %.1f с",
                reason, attempt, attempts, delay,
            )
            if on_retry:
                # attempt — номер только что провалившейся попытки; следующая — attempt+1.
                try:
                    await on_retry(attempt + 1, attempts, delay, _short(exc, 160))
                except Exception:  # noqa: BLE001 - уведомление не должно ломать повтор
                    logger.debug("Колбэк on_retry упал", exc_info=True)
            await asyncio.sleep(delay)

        saved_reasoning = self.reasoning
        try:
            for attempt in range(1, attempts + 1):
                streamed = [False]
                try:
                    return await self._stream_once(
                        messages,
                        tools,
                        on_text,
                        on_reasoning,
                        on_tool_progress,
                        mark_streamed=lambda: streamed.__setitem__(0, True),  # noqa: B023
                        max_tokens=max_tokens,
                    )
                except openai.BadRequestError as exc:
                    # Временные ошибки стриминга от upstream провайдеров (DigitalOcean/Cloudflare 400 TransferEncoding)
                    err_str = str(exc).lower()
                    if "transfer" in err_str or "upstream" in err_str or "payload is not completed" in err_str:
                        last_error = exc
                        error_details.append(f"Попытка {attempt}: BadRequest (upstream) — {exc}")
                        if attempt < attempts:
                            await _retry(attempt, exc, f"upstream 400: {exc}", 0.5)
                            continue
                        break
                    raise LLMError(
                        f"Провайдер отклонил запрос (400): {_short(exc)}. "
                        "Обычно это несовместимая модель (нет поддержки tools) или слишком длинный контекст."
                    ) from exc
                except openai.AuthenticationError as exc:
                    raise LLMError(
                        f"API отклонил ключ (401): {_short(exc)}. Проверьте LLM_API_KEY в настройках."
                    ) from exc
                except openai.PermissionDeniedError as exc:
                    # A key that is valid but not allowed for this model (per-model keys on gateways):
                    # retrying cannot help and only stalls the caller.
                    raise LLMError(tr("llm.forbidden", model=self.model, detail=_short(exc))) from exc
                except openai.NotFoundError as exc:
                    raise LLMError(
                        f"Модель '{self.model}' недоступна на {self.base_url} (404). "
                        f"Проверьте название модели в настройках или у провайдера. Детали: {_short(exc)}"
                    ) from exc
                except BadTurn as exc:
                    # The provider "answered" without running the model (a gateway error text, an
                    # empty stream, a stream cut at the loop guard): as good as a dropped connection.
                    last_error = exc
                    error_details.append(f"Attempt {attempt}: not a real answer — {exc}")
                    if str(exc) == "cut":
                        # A model that loops once may loop again: one more try with little
                        # reasoning, then an honest error instead of more 160K-char streams.
                        cuts += 1
                        if cuts >= 2:
                            raise LLMError(tr("llm.loop", model=self.model)) from exc
                        saved_reasoning, self.reasoning = self.reasoning, "low"
                        restore_reasoning = saved_reasoning
                    if attempt == attempts:
                        break
                    await _retry(attempt, exc, f"not a real answer: {exc}", 0.5)
                    continue
                except openai.APIStatusError as exc:
                    # Статусы 502, 503, 504, 524, 429 тоже пробуем повторить
                    if exc.status_code in (408, 429, 500, 502, 503, 504, 520, 521, 522, 524):
                        last_error = exc
                        error_details.append(f"Попытка {attempt}: HTTP {exc.status_code} — {exc}")
                        if attempt == attempts:
                            break
                        await _retry(attempt, exc, f"HTTP {exc.status_code}", 0.5)
                        continue
                    raise LLMError(f"Ошибка API {exc.status_code}: {_short(exc)}") from exc
                except RETRYABLE as exc:
                    last_error = exc
                    error_details.append(f"Попытка {attempt}: {type(exc).__name__} — {exc}")
                    if attempt == attempts:
                        break
                    await _retry(attempt, exc, f"{type(exc).__name__}: {exc}", 0.2)

            detailed_message = (
                f"Не удалось связаться с моделью «{self.model}» после {attempts} попыток переподключения. "
                f"Последняя ошибка: {_short(last_error)}.\n\n"
                "Что можно сделать: проверьте интернет и доступность провайдера, "
                "смените модель или endpoint в настройках, затем нажмите «Повторить».\n\n"
                "История попыток:\n" + "\n".join(error_details)
            )
            raise LLMError(detailed_message) from last_error
        finally:
            # A retry after a loop runs with little reasoning; the next request gets the level back.
            self.reasoning = saved_reasoning

    async def _stream_once(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        on_text: StreamCallback | None,
        on_reasoning: StreamCallback | None,
        on_tool_progress: ToolProgressCallback | None,
        mark_streamed,
        max_tokens: int | None = None,
    ) -> AssistantTurn:
        params: dict[str, Any] = {
            "model": self.model,
            "messages": self._prepare_messages(messages),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self.settings.llm_temperature is not None:
            params["temperature"] = self.settings.llm_temperature
        # Явный аргумент важнее глобальной настройки: он ограничивает конкретную
        # подзадачу (например, конспект источника в исследовании).
        effective_max = max_tokens or self.settings.llm_max_tokens
        if effective_max:
            params["max_tokens"] = effective_max
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"
        extra = (reliability.reasoning_extra(self.reasoning, str(self._client.base_url), self.model,
                                             self.settings.llm_reasoning_dialect)
                 if self._reasoning_supported else {})
        if extra:
            params["extra_body"] = extra

        gate = self._gate
        await gate.acquire()
        ok = False
        try:
            turn = await self._stream_body(params, messages, tools, on_text, on_reasoning, on_tool_progress,
                                           mark_streamed)
            ok = True
            return turn
        except openai.APIStatusError as exc:
            # Counted while this request is still in flight, so the new limit is right.
            if exc.status_code == 429 and reliability.is_concurrency_refusal(str(exc)):
                gate.too_many()
            raise
        finally:
            gate.release(ok)

    async def _open_stream(self, params: dict[str, Any]) -> Any:
        try:
            return await self._client.chat.completions.create(**params)
        except openai.BadRequestError as exc:
            text = str(exc)
            # Не все провайдеры знают stream_options — пробуем без него
            if "stream_options" in text:
                params.pop("stream_options", None)
                return await self._client.chat.completions.create(**params)
            # A provider that does not take our reasoning field: send without it from now on.
            if "extra_body" in params and any(k in text.lower() for k in ("reasoning", "thinking", "unrecognized", "unknown", "extra")):
                logger.info("%s does not take the reasoning level (%s): sending without it", self.model, _short(exc, 120))
                self._reasoning_supported = False
                params.pop("extra_body", None)
                return await self._client.chat.completions.create(**params)
            raise

    async def _stream_body(self, params, messages, tools, on_text, on_reasoning, on_tool_progress,
                           mark_streamed) -> AssistantTurn:
        started = time.perf_counter()
        first_byte = self.settings.llm_first_byte_timeout or None
        try:
            stream = await asyncio.wait_for(self._open_stream(params), first_byte)
            chunks = stream.__aiter__()
            first = await asyncio.wait_for(chunks.__anext__(), first_byte)
        except asyncio.TimeoutError as exc:
            raise BadTurn(f"no first byte in {first_byte:.0f} s") from exc
        except StopAsyncIteration:
            first = None
            chunks = None

        turn = AssistantTurn(model=self.model, first_byte_s=time.perf_counter() - started)
        calls: dict[int, ToolCall] = {}
        reported: dict[int, int] = {}  # сколько символов уже показали интерфейсу
        limit = self.settings.llm_stream_char_limit
        cut = False
        # The first characters are held back until it is clear they are not a gateway's error
        # text (that would otherwise show in the chat and stay there after the retry).
        held = ""
        hold = on_text is not None

        async def flush() -> None:
            nonlocal held, hold
            hold = False
            if held and on_text:
                mark_streamed()
                await on_text(held)
            held = ""

        async def all_chunks():
            if first is not None:
                yield first
            if chunks is not None:
                async for item in chunks:
                    yield item

        async for chunk in all_chunks():
            # Предохранитель от зацикливания. Reasoning-модель может генерировать
            # один и тот же текст бесконечно; read-timeout при этом молчит, ведь
            # поток идёт. Обрываем, как только вывод превысил разумный предел —
            # иначе задача (особенно deep_research) висит навсегда.
            if limit and len(turn.content) + len(turn.reasoning) > limit:
                logger.warning(
                    "Ответ модели превысил %d символов — вероятно зацикливание, обрываю поток.",
                    limit,
                )
                turn.finish_reason = "length"
                cut = True
                await _close_stream(stream)
                break

            usage = getattr(chunk, "usage", None)
            if usage:
                details = getattr(usage, "prompt_tokens_details", None)
                cached = getattr(details, "cached_tokens", None) if details is not None else None
                if cached is None and isinstance(details, dict):
                    cached = details.get("cached_tokens")
                turn.usage = {
                    "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                    "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
                    "total_tokens": getattr(usage, "total_tokens", 0) or 0,
                    # Prompt tokens served from the provider's cache (billed at a fraction).
                    "cached_tokens": cached or getattr(usage, "prompt_cache_hit_tokens", 0) or 0,
                }
            if not getattr(chunk, "choices", None):
                continue

            choice = chunk.choices[0]
            delta = choice.delta
            if choice.finish_reason:
                turn.finish_reason = choice.finish_reason

            if delta is None:
                continue

            text = delta.content or ""
            if text:
                turn.content += text
                if hold:
                    held += text
                    if len(turn.content) >= 160:
                        await flush()
                else:
                    mark_streamed()
                    if on_text:
                        await on_text(text)

            reasoning = _extract_reasoning(delta)
            if reasoning:
                mark_streamed()
                turn.reasoning += reasoning
                if on_reasoning:
                    await on_reasoning(reasoning)

            if delta.tool_calls and hold:
                await flush()
            for part in delta.tool_calls or []:
                index = part.index or 0
                call = calls.setdefault(index, ToolCall(id="", name=""))
                if part.id:
                    call.id = part.id
                if part.function and part.function.name:
                    call.name = part.function.name
                if part.function and part.function.arguments:
                    call.arguments += part.function.arguments

                # Сообщаем о прогрессе: сразу как узнали имя и дальше редко,
                # чтобы не заваливать интерфейс событиями на каждый токен.
                if on_tool_progress and call.name:
                    shown = reported.get(index, -1)
                    size = len(call.arguments)
                    if shown < 0 or size - shown >= 400:
                        reported[index] = size
                        mark_streamed()
                        await on_tool_progress(call.name, size)

        turn.tool_calls = [
            ToolCall(id=call.id or f"call_{idx}", name=call.name, arguments=call.arguments)
            for idx, call in sorted(calls.items())
            if call.name
        ]
        verdict = reliability.judge_turn(turn, messages, tools, cut=cut)
        if verdict:
            logger.warning("%s: not a real answer (%s), %d chars: %r", self.model, verdict,
                           len(turn.content), turn.content[:120])
            raise BadTurn(verdict)
        await flush()
        return turn

    async def aclose(self) -> None:
        await self._client.close()


async def _close_stream(stream: Any) -> None:
    """Закрывает поток провайдера, чтобы прервать генерацию на его стороне."""
    close = getattr(stream, "close", None)
    if close is None:
        return
    try:
        result = close()
        if asyncio.iscoroutine(result):
            await result
    except Exception:  # noqa: BLE001 - на обрыве потока падать не из-за чего
        logger.debug("Не удалось закрыть поток модели", exc_info=True)


def _extract_reasoning(delta: Any) -> str:
    """Достаёт reasoning-текст: разные провайдеры кладут его в разные поля."""
    for attr in ("reasoning", "reasoning_content"):
        value = getattr(delta, attr, None)
        if isinstance(value, str) and value:
            return value
    extra = getattr(delta, "model_extra", None) or {}
    for key in ("reasoning", "reasoning_content"):
        value = extra.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _supports_cache(model: str) -> bool:
    """Поддерживает ли модель кэширование промпта через cache_control (Anthropic)."""
    m = (model or "").lower()
    return "claude" in m or m.startswith("anthropic/")


def _short(exc: Exception | None, limit: int = 300) -> str:
    if exc is None:
        return "неизвестная ошибка"
    text = str(exc).replace("\n", " ")
    return text[:limit]


def build_llm_client(
    model: str | None = None,
    settings: Settings | None = None,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
) -> LLMClient:
    """Фабрика клиента. Единственное место, где ядро знает про конкретного провайдера.

    Класс выбирается по эндпоинту: нативный Anthropic (api.anthropic.com) —
    AnthropicClient, всё остальное — OpenAI-совместимый. base_url/api_key можно
    переопределить на один вызов (модель у другого провайдера при маршрутизации).
    """
    settings = settings or get_settings()
    # The model's own key / its provider (Settings -> Models), for every caller: runs, routing
    # tiers and their fallbacks, chat titles, subagents. The list's current key beats a copy a
    # routing tier stored when it was configured (it goes stale when the key is changed).
    from core.providers import credentials_for

    creds = credentials_for(model or settings.default_model, base_url, settings)
    api_key = creds.get("api_key") or api_key or None
    base_url = base_url or creds.get("base_url") or None
    effective_base = base_url or settings.llm_base_url
    from core.llm.anthropic_client import AnthropicClient, is_anthropic_endpoint

    if is_anthropic_endpoint(effective_base):
        return AnthropicClient(settings=settings, model=model, base_url=base_url, api_key=api_key)
    return OpenAICompatClient(settings=settings, model=model, base_url=base_url, api_key=api_key)
