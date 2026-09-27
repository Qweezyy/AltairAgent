"""Инструменты паритета с телефоном: память (курирование), контекст, канвас."""

from __future__ import annotations

import pytest

from core.agent.session import Session
from core.events import ContextUsage, ShowFile, ShowHtml
from core.memory import MemoryStore
from core.security.approval import ApprovalRequest
from core.tools.base import ToolContext
from core.tools.builtin.canvas_tools import (
    AttachFileTool,
    ShowGraphicTool,
    ShowInteractiveTool,
)
from core.tools.builtin.context_tools import (
    ContextCompressTool,
    ContextDropTool,
    ContextInfoTool,
)
from core.tools.builtin.memory_tools import (
    MemoryRemoveTool,
    MemoryReplaceTool,
    MemoryViewTool,
    SuggestMemoryTool,
)


class Capture:
    """Собиратель эмитнутых событий."""

    def __init__(self) -> None:
        self.events: list = []

    async def __call__(self, event) -> None:
        self.events.append(event)


# ------------------------------------------------------------------ память


@pytest.mark.asyncio
async def test_memory_view_remove_replace_global(ctx, settings):
    store = MemoryStore(settings.data_dir)
    store.remember("Пользователь любит Kotlin", category="user")
    store.remember("Часовой пояс МСК", category="user")

    view = await MemoryViewTool().run(MemoryViewTool.Args(scope="global"), ctx)
    assert "1." in view and "Kotlin" in view and "МСК" in view

    # Замена по номеру.
    await MemoryReplaceTool().run(
        MemoryReplaceTool.Args(scope="global", index=1, new_text="Пользователь любит Rust"), ctx
    )
    texts = {f.text for f in MemoryStore(settings.data_dir).all()}
    assert "Пользователь любит Rust" in texts and "Пользователь любит Kotlin" not in texts

    # Удаление по подстроке.
    await MemoryRemoveTool().run(MemoryRemoveTool.Args(scope="global", contains="МСК"), ctx)
    texts = {f.text for f in MemoryStore(settings.data_dir).all()}
    assert not any("МСК" in t for t in texts)


@pytest.mark.asyncio
async def test_memory_folder_scope(ctx, settings):
    from core.folder_memory import FolderMemory

    fm = FolderMemory(settings.workspace)
    fm.append("Проект собирается через gradle", "project")
    fm.append("Тесты запускать pytest -q", "project")

    view = await MemoryViewTool().run(MemoryViewTool.Args(scope="folder"), ctx)
    assert "gradle" in view and "pytest" in view

    await MemoryRemoveTool().run(MemoryRemoveTool.Args(scope="folder", index=1), ctx)
    remaining = FolderMemory(settings.workspace).read()
    assert "gradle" not in remaining and "pytest" in remaining

    await MemoryReplaceTool().run(
        MemoryReplaceTool.Args(scope="folder", find="pytest -q", replace="pytest -x"), ctx
    )
    assert "pytest -x" in FolderMemory(settings.workspace).read()


@pytest.mark.asyncio
async def test_suggest_memory_respects_user_choice(settings):
    async def deny(_req: ApprovalRequest) -> bool:
        return False

    async def allow(_req: ApprovalRequest) -> bool:
        return True

    ctx_deny = ToolContext(settings=settings, approver=deny)
    out = await SuggestMemoryTool().run(SuggestMemoryTool.Args(text="Любит чай"), ctx_deny)
    assert "не сохранять" in out
    assert not any("чай" in f.text for f in MemoryStore(settings.data_dir).all())

    ctx_allow = ToolContext(settings=settings, approver=allow)
    await SuggestMemoryTool().run(SuggestMemoryTool.Args(text="Любит чай"), ctx_allow)
    assert any("чай" in f.text for f in MemoryStore(settings.data_dir).all())


# ---------------------------------------------------------------- контекст


def _session_with_history() -> Session:
    s = Session()
    s.set_system_prompt("Ты ассистент.")
    s.messages.append({"role": "user", "content": "привет " * 50})
    s.messages.append({"role": "assistant", "content": "здравствуйте " * 50})
    s.messages.append(
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]}
    )
    s.messages.append({"role": "tool", "tool_call_id": "c1", "name": "read_file", "content": "данные " * 100})
    s.messages.append({"role": "user", "content": "последнее сообщение"})
    return s


@pytest.mark.asyncio
async def test_context_info_breaks_down_by_role(settings):
    ctx = ToolContext(settings=settings, session=_session_with_history())
    out = await ContextInfoTool().run(ContextInfoTool.Args(), ctx)
    assert "занято" in out and "системный промпт" in out
    assert "результаты инструментов" in out


@pytest.mark.asyncio
async def test_context_compress_folds_prefix(settings):
    session = _session_with_history()
    cap = Capture()
    ctx = ToolContext(settings=settings, session=session, emitter=cap)
    before = len(session.messages)
    out = await ContextCompressTool().run(
        ContextCompressTool.Args(summary="Обсудили приветствие и чтение файла.", keep_last=1), ctx
    )
    assert "Сжато" in out
    assert len(session.view()) < before             # the model gets less...
    assert len(session.messages) == before + 1      # ...the chat keeps everything, plus the summary
    assert any("Сжатый контекст" in str(m.get("content")) for m in session.view())
    assert session.view()[-1]["content"] == "последнее сообщение"
    assert any(isinstance(e, ContextUsage) for e in cap.events)


@pytest.mark.asyncio
async def test_context_drop_tools_removes_orphans(settings):
    session = _session_with_history()
    ctx = ToolContext(settings=settings, session=session)
    await ContextDropTool().run(ContextDropTool.Args(what="tools"), ctx)
    view = session.view()
    assert all(m.get("role") != "tool" for m in view)
    # No assistant keeps tool calls whose results are gone (the provider would answer 400).
    assert all(not m.get("tool_calls") for m in view)
    assert any(m.get("role") == "tool" for m in session.messages)  # the chat keeps them


@pytest.mark.asyncio
async def test_context_drop_images_strips_parts(settings):
    session = Session()
    session.set_system_prompt("Ты ассистент.")
    session.messages.append(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "что на фото?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        }
    )
    ctx = ToolContext(settings=settings, session=session)
    await ContextDropTool().run(ContextDropTool.Args(what="images"), ctx)
    assert session.view()[-1]["content"] == "что на фото?"  # the model gets the text only
    assert isinstance(session.messages[-1]["content"], list)  # the chat keeps the photo


# ------------------------------------------------------------------ канвас


@pytest.mark.asyncio
async def test_show_graphic_emits_wrapped_svg(settings):
    cap = Capture()
    ctx = ToolContext(settings=settings, emitter=cap)
    out = await ShowGraphicTool().run(
        ShowGraphicTool.Args(svg='<svg viewBox="0 0 10 10"><rect/></svg>', caption="схема"), ctx
    )
    assert "графику" in out
    ev = cap.events[-1]
    assert isinstance(ev, ShowHtml) and ev.kind == "graphic"
    assert "<svg" in ev.html and "<!doctype html>" in ev.html.lower()


@pytest.mark.asyncio
async def test_show_graphic_rejects_non_svg(settings):
    ctx = ToolContext(settings=settings, emitter=Capture())
    res = await ShowGraphicTool().run(ShowGraphicTool.Args(svg="просто текст"), ctx)
    assert res.ok is False


@pytest.mark.asyncio
async def test_show_interactive_emits_html(settings):
    cap = Capture()
    ctx = ToolContext(settings=settings, emitter=cap)
    await ShowInteractiveTool().run(
        ShowInteractiveTool.Args(html="<button>hi</button>", caption="кнопка"), ctx
    )
    ev = cap.events[-1]
    assert isinstance(ev, ShowHtml) and ev.kind == "interactive" and "button" in ev.html


@pytest.mark.asyncio
async def test_attach_file_emits_show_file(settings):
    target = settings.workspace / "out.csv"
    target.write_text("a,b\n1,2\n", encoding="utf-8")
    cap = Capture()
    ctx = ToolContext(settings=settings, emitter=cap)
    out = await AttachFileTool().run(AttachFileTool.Args(path="out.csv", caption="итог"), ctx)
    assert "out.csv" in out
    ev = cap.events[-1]
    assert isinstance(ev, ShowFile) and ev.kind == "data" and ev.name == "out.csv"
    assert ev.size_bytes > 0


@pytest.mark.asyncio
async def test_attach_file_missing_fails(settings):
    ctx = ToolContext(settings=settings, emitter=Capture())
    res = await AttachFileTool().run(AttachFileTool.Args(path="нет.txt"), ctx)
    assert res.ok is False
