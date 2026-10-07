"""Запасные модели в маршрутизации: FallbackLLM и tier_candidates."""

from __future__ import annotations

import pytest

from core.agent.router import tier_candidates
from core.llm.base import AssistantTurn, LLMClient
from core.llm.fallback import FallbackLLM


class _Stub(LLMClient):
    def __init__(self, model: str, *, boom: bool = False) -> None:
        self.model = model
        self._boom = boom
        self.calls = 0
        self.closed = False

    async def complete(self, messages, **kwargs):  # type: ignore[override]
        self.calls += 1
        if self._boom:
            raise RuntimeError(f"{self.model} не отвечает")
        return AssistantTurn(content=f"ok:{self.model}")

    async def aclose(self) -> None:
        self.closed = True


def test_tier_candidates_primary_then_fallbacks():
    tier = {
        "model": "big", "base_url": "u", "api_key": "k",
        "fallbacks": [{"model": "big2", "base_url": "u2"}, {"bad": 1}, {"model": "big3"}],
    }
    cands = tier_candidates(tier)
    assert [c["model"] for c in cands] == ["big", "big2", "big3"]
    assert cands[0]["api_key"] == "k"


def test_tier_candidates_empty_without_model():
    assert tier_candidates({}) == []
    assert tier_candidates({"fallbacks": [{"model": "x"}]}) == [{"model": "x", "base_url": "", "api_key": ""}]


async def test_fallback_uses_first_working():
    built = []

    def build(**kw):
        c = _Stub(kw["model"], boom=(kw["model"] == "primary"))
        built.append(c)
        return c

    llm = FallbackLLM([{"model": "primary"}, {"model": "backup"}], build)
    turn = await llm.complete([{"role": "user", "content": "hi"}])
    assert turn.content == "ok:backup"
    assert llm.model == "backup"
    await llm.aclose()
    assert all(c.closed for c in built)


async def test_fallback_all_fail_raises_last():
    def build(**kw):
        return _Stub(kw["model"], boom=True)

    llm = FallbackLLM([{"model": "a"}, {"model": "b"}], build)
    with pytest.raises(RuntimeError):
        await llm.complete([{"role": "user", "content": "hi"}])


async def test_fallback_primary_ok_skips_backup():
    built = {}

    def build(**kw):
        built[kw["model"]] = _Stub(kw["model"])
        return built[kw["model"]]

    llm = FallbackLLM([{"model": "primary"}, {"model": "backup"}], build)
    turn = await llm.complete([{"role": "user", "content": "hi"}])
    assert turn.content == "ok:primary"
    assert "backup" not in built  # запасную даже не создаём, если основная ответила


async def test_a_providers_bad_period_is_one_interval_in_the_journal(monkeypatch, settings):
    """A reviewer's point: the breaker opening and closing are events (model, provider, why, for
    how long), so a bad period reads as one interval, not a scatter of slow attempts."""
    import core.settings as settings_module
    from core.journal import get_journal
    from core.llm import reliability

    reliability.reset_state()
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)
    down = {"primary": True}

    class Flaky(_Stub):
        async def complete(self, messages, **kwargs):  # type: ignore[override]
            self._boom = self.model == "primary" and down["primary"]
            return await super().complete(messages, **kwargs)

    def build(**kw):
        return Flaky(kw["model"])

    llm = FallbackLLM([{"model": "primary", "base_url": "https://a"}, {"model": "backup", "base_url": "https://b"}],
                      build)
    assert (await llm.complete([{"role": "user", "content": "1"}])).content == "ok:backup"
    assert (await llm.complete([{"role": "user", "content": "2"}])).content == "ok:backup"   # held back: no 2nd open
    journal = get_journal(settings.data_dir / "journal")
    opened = [r for r in journal.read(limit=20) if r["kind"].startswith("llm.breaker")]
    assert [r["kind"] for r in opened] == ["llm.breaker_open"]
    assert opened[0]["data"]["model"] == "primary" and "не отвечает" in opened[0]["data"]["reason"]

    # Its time runs out: the next request closes it, once, with how long it lasted.
    key = llm._key(0)
    reliability.health(key).sick_until = 0
    reliability.health(key).opened_at -= 600
    down["primary"] = False
    assert (await llm.complete([{"role": "user", "content": "3"}])).content == "ok:primary"
    await llm.complete([{"role": "user", "content": "4"}])
    kinds = [r["kind"] for r in journal.read(limit=20) if r["kind"].startswith("llm.breaker")]
    assert kinds == ["llm.breaker_closed", "llm.breaker_open"]
    closed = journal.read(limit=20, kinds=["llm.breaker_closed"])[0]["data"]
    assert closed["model"] == "primary" and closed["after_minutes"] >= 10
    reliability.reset_state()
