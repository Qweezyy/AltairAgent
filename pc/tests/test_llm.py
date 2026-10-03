from __future__ import annotations

import httpx
import openai

from core.llm.base import AssistantTurn
from core.llm.openai_client import OpenAICompatClient
from core.settings import Settings


def test_llm_client_prefers_universal_key():
    settings = Settings(
        llm_api_key="gateyourway-key",
        openrouter_api_key="legacy-key",
        llm_base_url="https://api.gateyourway.com/v1",
    )
    client = OpenAICompatClient(settings=settings)

    assert client._client.api_key == "gateyourway-key"


async def test_on_retry_callback_reports_reconnection(monkeypatch):
    """При повторах должен вызываться on_retry — иначе UI выглядит зависшим."""
    settings = Settings(openrouter_api_key="test-key", llm_max_retries=3)
    client = OpenAICompatClient(settings=settings, model="test-model")

    # Не ждём реальный backoff в тесте.
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("core.llm.openai_client.asyncio.sleep", no_sleep)

    attempts = 0

    async def mock_stream_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise openai.APIConnectionError(request=httpx.Request("POST", "http://test"))
        return AssistantTurn(content="готово")

    monkeypatch.setattr(client, "_stream_once", mock_stream_once)

    notices: list[tuple[int, int]] = []

    async def on_retry(attempt, total, delay, reason):
        notices.append((attempt, total))

    result = await client.complete([{"role": "user", "content": "x"}], on_retry=on_retry)
    assert result.content == "готово"
    # Два провала → два уведомления, номера указывают на СЛЕДУЮЩУЮ попытку.
    assert notices == [(2, 5), (3, 5)]


async def test_llm_retries_on_upstream_transfer_error(monkeypatch):
    settings = Settings(openrouter_api_key="test-key", llm_max_retries=3)
    client = OpenAICompatClient(settings=settings, model="test-model")

    call_count = 0

    req = httpx.Request("POST", "http://test")
    resp = httpx.Response(status_code=400, request=req)

    async def mock_stream_once(
        messages, tools, on_text, on_reasoning, on_tool_progress, mark_streamed, max_tokens=None
    ):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise openai.BadRequestError(
                "Upstream error from DigitalOcean: Response payload is not completed: TransferEncodingError: 400",
                response=resp,
                body=None,
            )
        return AssistantTurn(content="Успешный ответ после повтора")

    monkeypatch.setattr(client, "_stream_once", mock_stream_once)

    result = await client.complete([{"role": "user", "content": "Привет"}])
    assert result.content == "Успешный ответ после повтора"
    assert call_count == 2


# ------------------------------------------------ предохранитель от зацикливания


class _Delta:
    def __init__(self, content: str) -> None:
        self.content = content
        self.tool_calls = None
        self.reasoning = None


class _Choice:
    def __init__(self, content: str) -> None:
        self.delta = _Delta(content)
        self.finish_reason = None


class _Chunk:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]
        self.usage = None


class _EndlessStream:
    """Бесконечный поток одинаковых кусков — как зациклившаяся модель."""

    def __init__(self) -> None:
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.closed:
            raise StopAsyncIteration
        return _Chunk("повторяю одно и то же ")

    async def close(self):
        self.closed = True


async def test_endless_generation_is_cut_off(monkeypatch):
    """A model that generates without end: the stream is cut at the limit and closed, tried once
    more with little reasoning, and then the call fails honestly — a cut stream is never handed
    back as if it were the answer (the agent took it for a finished task)."""
    import pytest

    from core.errors import LLMError
    from core.llm import reliability

    reliability.reset_state()
    settings = Settings(openrouter_api_key="test-key", llm_stream_char_limit=500,
                        llm_base_url="https://openrouter.ai/api/v1")
    client = OpenAICompatClient(settings=settings, model="test-model")
    client.reasoning = "medium"
    streams: list[_EndlessStream] = []
    sent: list[dict] = []

    async def fake_create(**params):
        sent.append(params)
        streams.append(_EndlessStream())
        return streams[-1]

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    monkeypatch.setattr("core.llm.openai_client.asyncio.sleep", no_wait)

    with pytest.raises(LLMError, match="without end"):
        await client.complete([{"role": "user", "content": "зациклись"}])

    assert len(streams) == 2, "one retry after a loop, not five"
    assert all(s.closed for s in streams), "a runaway provider stream was not closed"
    assert sent[0]["extra_body"] == {"reasoning": {"effort": "medium"}}
    assert sent[1]["extra_body"] == {"reasoning": {"effort": "low"}}
    assert client.reasoning == "medium", "the lowered level must not stick to the client"


async def test_max_tokens_reaches_the_request(monkeypatch):
    """Явный max_tokens подзадачи должен доходить до провайдера."""
    settings = Settings(openrouter_api_key="test-key")
    client = OpenAICompatClient(settings=settings, model="test-model")

    captured: dict = {}

    class _OneAnswer(_EndlessStream):
        sent = False

        async def __anext__(self):
            if self.sent:
                raise StopAsyncIteration
            self.sent = True
            return _Chunk("ok")

    async def fake_create(**params):
        captured.update(params)
        return _OneAnswer()

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)

    await client.complete([{"role": "user", "content": "коротко"}], max_tokens=1500)
    assert captured.get("max_tokens") == 1500
