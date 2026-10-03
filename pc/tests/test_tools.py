from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, Field

from core.events import ArtifactCreated, PlanUpdate
from core.security.approval import ApprovalRequest
from core.tools import build_default_registry
from core.tools.base import Tool, ToolContext, ToolResult, truncate_output
from core.tools.builtin.files import ReadFileTool, WriteFileTool
from core.tools.builtin.plan import UpdatePlanTool
from core.tools.builtin.search import GrepSearchTool
from core.tools.builtin.shell import check_command
from core.tools.builtin.web import WebSearchTool
from core.tools.registry import ToolRegistry


class EchoArgs(BaseModel):
    text: str = Field(description="что вернуть")
    times: int = Field(default=1, ge=1, le=5)


class EchoTool(Tool):
    name = "echo"
    description = "Возвращает текст."
    Args = EchoArgs

    async def run(self, args: EchoArgs, ctx: ToolContext) -> str:
        return args.text * args.times


class SlowTool(Tool):
    name = "slow"
    description = "Спит дольше таймаута."
    timeout = 0.05

    async def run(self, args, ctx) -> str:
        await asyncio.sleep(5)
        return "не должно случиться"


class BoomTool(Tool):
    name = "boom"
    description = "Всегда падает."

    async def run(self, args, ctx) -> str:
        raise RuntimeError("внутренняя поломка")


class DangerTool(Tool):
    name = "danger"
    description = "Опасное действие."
    dangerous = True

    async def run(self, args, ctx) -> str:
        return "выполнено"


# ------------------------------------------------------------------ схемы


def test_schema_has_no_refs_and_no_titles():
    schema = EchoTool().schema()
    assert schema["function"]["name"] == "echo"
    params = schema["function"]["parameters"]
    assert "$defs" not in params
    assert "title" not in params
    assert params["properties"]["text"]["description"] == "что вернуть"
    assert params["required"] == ["text"]


def test_builtin_registry_schemas_are_valid():
    registry = build_default_registry()
    assert len(registry) >= 12
    for schema in registry.schemas():
        params = schema["function"]["parameters"]
        assert params["type"] == "object"
        assert schema["function"]["description"]


def test_web_search_schema_contains_timelimit():
    schema = WebSearchTool().schema()
    params = schema["function"]["parameters"]
    assert "timelimit" in params["properties"]
    assert "query" in params["required"]


# --------------------------------------------------------------- валидация


async def test_invalid_arguments_return_readable_error(ctx):
    result = await EchoTool().invoke({"times": 99}, ctx)
    assert not result.ok
    assert "text" in result.content


async def test_arguments_accept_json_string(ctx):
    result = await EchoTool().invoke('{"text": "ab", "times": 2}', ctx)
    assert result.content == "abab"


async def test_broken_json_arguments(ctx):
    result = await EchoTool().invoke("{not json", ctx)
    assert not result.ok
    assert "JSON" in result.content


# ---------------------------------------------------------- отказоустойчивость


async def test_timeout_is_reported_not_raised(ctx):
    result = await SlowTool().invoke({}, ctx)
    assert not result.ok
    assert "лимит" in result.content


async def test_exception_inside_tool_is_captured(ctx):
    result = await BoomTool().invoke({}, ctx)
    assert not result.ok
    assert "внутренняя поломка" in result.content


def test_truncate_keeps_head_and_tail():
    text = "a" * 100 + "TAIL"
    out = truncate_output(text, 50)
    assert out.startswith("a")
    assert out.endswith("TAIL")
    assert "truncated" in out


# ------------------------------------------------------------ подтверждения


async def test_dangerous_tool_denied_by_user(ctx):
    ctx.settings.approval_mode = "manual"

    async def deny(request: ApprovalRequest) -> bool:
        assert request.name == "danger"
        return False

    ctx.approver = deny
    result = await DangerTool().invoke({}, ctx)
    assert not result.ok
    assert "отклонил" in result.content


async def test_dangerous_tool_allowed(ctx):
    ctx.settings.approval_mode = "manual"

    async def allow(request: ApprovalRequest) -> bool:
        return True

    ctx.approver = allow
    assert (await DangerTool().invoke({}, ctx)).content == "выполнено"


async def test_auto_mode_skips_approval(ctx):
    called = False

    async def approver(request: ApprovalRequest) -> bool:
        nonlocal called
        called = True
        return True

    ctx.settings.approval_mode = "bypass"
    ctx.approver = approver
    await DangerTool().invoke({}, ctx)
    assert called is False


# --------------------------------------------------------------- реестр


def test_registry_rejects_duplicates():
    registry = ToolRegistry([EchoTool()])
    with pytest.raises(ValueError):
        registry.add(EchoTool())
    registry.add(EchoTool(), override=True)
    assert len(registry) == 1


def test_registry_suggests_similar_name():
    registry = ToolRegistry([EchoTool()])
    assert "echo" in registry.suggest("ehco")


# --------------------------------------------------- планирование (план)


async def test_update_plan_emits_event(ctx):
    events = []

    async def emitter(e):
        events.append(e)

    ctx.emitter = emitter
    tool = UpdatePlanTool()
    result = await tool.invoke(
        {
            "steps": [
                {"title": "Сбор данных", "status": "completed"},
                {"title": "Анализ", "status": "in_progress"},
                {"title": "Отчёт", "status": "pending"},
            ]
        },
        ctx,
    )
    assert result.ok
    assert "1/3 шагов завершено" in result.content
    assert len(events) == 1
    assert isinstance(events[0], PlanUpdate)
    assert len(events[0].steps) == 3


async def test_update_plan_empty_fails(ctx):
    tool = UpdatePlanTool()
    result = await tool.invoke({"steps": []}, ctx)
    assert not result.ok
    assert "пустым" in result.content


# --------------------------------------------------- файловые инструменты и артефакты


async def test_write_then_read_roundtrip(ctx):
    events = []

    async def emitter(e):
        events.append(e)

    ctx.emitter = emitter
    write = await WriteFileTool().invoke({"path": "notes/hello.txt", "content": "привет\nмир"}, ctx)
    assert write.ok
    assert any(isinstance(e, ArtifactCreated) and e.name == "hello.txt" for e in events)

    read = await ReadFileTool().invoke({"path": "notes/hello.txt"}, ctx)
    assert "привет" in read.content
    assert "lines 1-2 of 2" in read.content


async def test_write_outside_workspace_blocked(ctx):
    result = await WriteFileTool().invoke({"path": "../evil.txt", "content": "x"}, ctx)
    assert not result.ok
    assert "запрещ" in result.content.lower()


async def test_grep_finds_text(ctx):
    (ctx.settings.workspace / "sample.py").write_text("def target():\n    pass\n", encoding="utf-8")
    result = await GrepSearchTool().invoke({"query": "def target"}, ctx)
    assert "sample.py:1" in result.content


# --------------------------------------------------- веб инструменты


async def test_web_search_mocked(ctx, monkeypatch):
    import core.tools.builtin.web as web_module

    async def mock_search(query, *, settings, max_results, timelimit):
        assert timelimit == "w"
        return [
            {
                "title": "Python 3.14",
                "url": "https://python.org",
                "snippet": "Latest release info",
                "engine": "duckduckgo",
            }
        ]

    monkeypatch.setattr(web_module, "search_web", mock_search)
    result = await WebSearchTool().invoke({"query": "Python news", "timelimit": "w"}, ctx)
    assert result.ok
    assert "Python 3.14" in result.content
    assert "[период: w]" in result.content


async def test_web_search_reports_no_results(ctx, monkeypatch):
    """Пустая выдача — ошибка с советом, а не молчаливый пустой ответ."""
    import core.tools.builtin.web as web_module

    async def empty(query, *, settings, max_results, timelimit):
        return []

    monkeypatch.setattr(web_module, "search_web", empty)
    result = await WebSearchTool().invoke({"query": "чепуха"}, ctx)
    assert not result.ok
    assert "не дал результатов" in result.content


async def test_search_prefers_tavily_when_key_is_set(ctx, monkeypatch):
    """С ключом Tavily идём в него, а не в бесплатный поисковик."""
    from core.research import search as search_module

    called: list[str] = []

    async def fake_tavily(client, query, api_key, limit, timelimit):
        called.append(api_key)
        return [{"title": "Tavily news", "url": "https://example.com", "snippet": "", "engine": "tavily"}]

    async def fail_ddg(*args, **kwargs):
        raise AssertionError("DuckDuckGo не должен вызываться, когда есть ключ Tavily")

    ctx.settings.tavily_api_key = "test-tavily-key"
    monkeypatch.setattr(search_module, "_tavily", fake_tavily)
    monkeypatch.setattr(search_module, "_duckduckgo", fail_ddg)

    results = await search_module.search_web("Python", settings=ctx.settings)
    assert called == ["test-tavily-key"]
    assert results[0]["title"] == "Tavily news"


def test_argument_named_title_survives_schema_cleanup():
    """«title» — и аннотация схемы, и законное имя аргумента.

    Однажды чистка аннотаций вырезала само поле: в required оно оставалось,
    в properties исчезало. Gemini на такую схему отвечает 400, остальные
    провайдеры молча теряли аргумент.
    """
    from core.tools.builtin.plan import UpdatePlanTool

    params = UpdatePlanTool().schema()["function"]["parameters"]
    step = params["properties"]["steps"]["items"]

    assert "title" in step["properties"]
    assert set(step.get("required", [])) <= set(step["properties"])


def test_no_tool_requires_a_field_it_does_not_declare():
    """Схема, где required ссылается в пустоту, ломает строгих провайдеров."""
    from core.tools import build_default_registry

    def check(node, path):
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                missing = set(node.get("required") or []) - set(properties)
                assert not missing, f"{path}: required без properties — {missing}"
            for key, value in node.items():
                check(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, item in enumerate(node):
                check(item, f"{path}[{index}]")

    for tool in build_default_registry().all():
        check(tool.schema()["function"]["parameters"], tool.name)


# ----------------------------------------------------------------- shell


@pytest.mark.parametrize(
    "command",
    ["format c:", "rm -rf /", "shutdown /s", "diskpart", "del /s /q C:\\"],
)
def test_catastrophic_commands_blocked(command):
    from core.errors import PermissionDenied

    with pytest.raises(PermissionDenied):
        check_command(command)


def test_normal_commands_allowed():
    for command in ["git status", "pip install requests", "python -m pytest", "ls -la"]:
        check_command(command)


async def test_tool_result_fail_helper():
    result = ToolResult.fail("что-то не так")
    assert not result.ok
    assert result.content.startswith("ОШИБКА")


# ------------------------------------------------- подтверждение в консоли


async def test_console_approver_accepts_yes(monkeypatch):
    from core.security import console as console_module

    monkeypatch.setattr(console_module.asyncio, "to_thread", lambda fn, prompt: _answer("да"))
    assert await console_module.console_approver(ApprovalRequest(name="write_file")) is True


async def test_console_approver_denies_on_eof(monkeypatch):
    from core.security import console as console_module

    async def raise_eof(fn, prompt):
        raise EOFError

    monkeypatch.setattr(console_module.asyncio, "to_thread", raise_eof)
    assert await console_module.console_approver(ApprovalRequest(name="write_file")) is False


async def _answer(text: str) -> str:
    return text
