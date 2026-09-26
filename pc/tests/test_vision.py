"""Визуальная верификация вёрстки: захват скриншотов и Vision-аудит."""

from __future__ import annotations

import base64

import pytest

from core.tools.base import ToolContext
from core.tools.builtin.vision_tools import (
    AuditUITool,
    ScreenshotUITool,
    _is_local_target,
)
from core.vision import VIEWPORTS
from tests.fakes import EventCollector

#: 1×1 прозрачный PNG.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def test_viewports_known():
    assert set(VIEWPORTS) == {"desktop", "tablet", "mobile"}
    assert VIEWPORTS["mobile"] == (375, 667)


def test_is_local_target():
    assert _is_local_target("http://localhost:5173")
    assert _is_local_target("http://127.0.0.1:8000/page")
    assert _is_local_target("index.html")  # относительный путь = файл в workspace
    assert _is_local_target("file:///C:/x.html")
    assert not _is_local_target("https://example.com")


def test_screenshot_local_auto_allow():
    tool = ScreenshotUITool()
    ctx = ToolContext()
    assert tool.auto_verdict(tool.Args(target="http://localhost:3000"), ctx) == "allow"
    assert tool.auto_verdict(tool.Args(target="https://site.com"), ctx) == "ask"


@pytest.mark.asyncio
async def test_screenshot_saves_artifact(settings, monkeypatch):
    """screenshot_ui сохраняет PNG в .screenshots и эмитит артефакт-картинку."""
    import core.tools.builtin.vision_tools as vt

    async def fake_grab(args, ctx):
        return _PNG

    monkeypatch.setattr(vt, "_grab", fake_grab)
    events = EventCollector()
    ctx = ToolContext(settings=settings, emitter=events)
    tool = ScreenshotUITool()

    result = await tool.run(tool.Args(target="index.html"), ctx)
    assert result.ok
    assert ".screenshots/" in result.content
    # Файл создан.
    saved = list((settings.workspace / ".screenshots").glob("shot-*.png"))
    assert saved and saved[0].read_bytes() == _PNG
    # Событие артефакта с kind=image.
    art = [e for e in events.events if getattr(e, "type", "") == "artifact.created"]
    assert art and art[0].kind == "image"


@pytest.mark.asyncio
async def test_audit_queues_screenshot_for_main_model(settings, monkeypatch):
    """audit_ui прикладывает скриншот к диалогу для ОСНОВНОЙ модели (image_url),
    а не делает отдельный запрос к другой модели."""
    import core.tools.builtin.vision_tools as vt

    async def fake_grab(args, ctx):
        return _PNG

    monkeypatch.setattr(vt, "_grab", fake_grab)

    ctx = ToolContext(settings=settings, emitter=EventCollector())
    tool = AuditUITool()
    result = await tool.run(tool.Args(target="http://localhost:5173", focus="контраст"), ctx)

    assert ".screenshots/" in result.content
    # Картинка ушла в очередь показа основной модели, как image_url.
    pending = ctx.scratch.get("_vision_pending")
    assert pending, "скриншот должен попасть в _vision_pending"
    part = pending[0]["parts"][0]
    assert part["type"] == "image_url"
    assert part["image_url"]["url"].startswith("data:image/png;base64,")
    assert "контраст" in pending[0]["text"]  # focus дошёл до модели
