"""Тесты субагентов (spawn_subagent) и флага allow_subagents."""

from __future__ import annotations

from core.llm.base import AssistantTurn
from core.tools.base import ToolContext
from core.tools.builtin.subagent_tools import SpawnSubagentTool
from tests.fakes import ScriptedLLM


async def test_subagent_refuses_when_disabled(ctx: ToolContext):
    # По умолчанию allow_subagents=False.
    res = await SpawnSubagentTool().run(
        SpawnSubagentTool.Args(task="сделай что-нибудь"), ctx
    )
    assert not res.ok
    assert "выключен" in res.content.lower()


async def test_subagent_runs_when_enabled(settings, monkeypatch):
    s = settings.model_copy(update={"allow_subagents": True})
    ctx = ToolContext(settings=s)

    def fake_build(*_args, **_kwargs):
        return ScriptedLLM([AssistantTurn(content="Разведка завершена: файлов 3.")])

    monkeypatch.setattr("core.llm.build_llm_client", fake_build)

    res = await SpawnSubagentTool().run(
        SpawnSubagentTool.Args(task="изучи проект", label="разведчик"), ctx
    )
    assert res.ok
    assert "Разведка завершена" in res.content
    assert "разведчик" in res.content


async def test_subagent_verdict_depends_on_flag(settings):
    tool = SpawnSubagentTool()
    args = SpawnSubagentTool.Args(task="x")
    off = ToolContext(settings=settings)  # allow_subagents=False
    on = ToolContext(settings=settings.model_copy(update={"allow_subagents": True}))
    assert tool.auto_verdict(args, off) == "ask"
    assert tool.auto_verdict(args, on) == "allow"


async def test_subagent_registry_excludes_itself(settings, monkeypatch):
    """Субагент не должен получать инструмент порождения субагентов (анти-рекурсия)."""
    s = settings.model_copy(update={"allow_subagents": True})
    ctx = ToolContext(settings=s)

    seen: dict = {}

    class _Recorder(ScriptedLLM):
        async def complete(self, messages, **kwargs):  # type: ignore[override]
            seen["tools"] = [t["function"]["name"] for t in (kwargs.get("tools") or [])]
            return await super().complete(messages, **kwargs)

    def fake_build(*_a, **_k):
        return _Recorder([AssistantTurn(content="готово")])

    monkeypatch.setattr("core.llm.build_llm_client", fake_build)

    await SpawnSubagentTool().run(SpawnSubagentTool.Args(task="t"), ctx)
    assert "spawn_subagent" not in seen.get("tools", [])
