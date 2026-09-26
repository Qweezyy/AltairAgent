"""Тесты чтения ноутбуков (read_file .ipynb) и view_image."""

from __future__ import annotations

import base64
import json

from core.events import ShowImage
from core.tools.base import ToolContext
from core.tools.builtin.files import ReadFileTool
from core.tools.builtin.vision_tools import ShowImageTool, ViewImageTool

# 1×1 PNG, чтобы не тащить Pillow в тест.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


async def test_read_notebook_renders_cells(settings, ctx: ToolContext):
    nb = {
        "cells": [
            {"cell_type": "markdown", "source": ["# Заголовок\n", "текст"]},
            {
                "cell_type": "code",
                "source": ["print('hi')"],
                "outputs": [{"output_type": "stream", "text": ["hi\n"]}],
            },
            {
                "cell_type": "code",
                "source": ["1/0"],
                "outputs": [{"output_type": "error", "ename": "ZeroDivisionError", "evalue": "division by zero"}],
            },
        ]
    }
    (settings.workspace / "nb.ipynb").write_text(json.dumps(nb), encoding="utf-8")

    out = await ReadFileTool().run(ReadFileTool.Args(path="nb.ipynb"), ctx)
    assert "ячеек 3" in out
    assert "[1] markdown" in out
    assert "print('hi')" in out
    assert "out> hi" in out
    assert "ERR> ZeroDivisionError" in out


async def test_view_image_queues_for_main_model(settings, ctx: ToolContext):
    """view_image кладёт картинку в очередь для ОСНОВНОЙ модели (в диалог как
    вложение), а не делает отдельный запрос к другой модели."""
    (settings.workspace / "pic.png").write_bytes(_PNG)

    res = await ViewImageTool().run(ViewImageTool.Args(path="pic.png"), ctx)
    assert "pic.png" in res.content

    pending = ctx.scratch.get("_vision_pending")
    assert pending, "картинка должна попасть в _vision_pending для показа модели"
    part = pending[0]["parts"][0]
    assert part["type"] == "image_url"
    assert part["image_url"]["url"].startswith("data:image/png;base64,")


async def test_view_image_rejects_non_image(settings, ctx: ToolContext):
    (settings.workspace / "notes.txt").write_text("hello", encoding="utf-8")
    res = await ViewImageTool().run(ViewImageTool.Args(path="notes.txt"), ctx)
    assert not res.ok


async def test_show_image_emits_event(settings):
    (settings.workspace / "shot.png").write_bytes(_PNG)
    events: list = []

    async def emitter(ev):
        events.append(ev)

    ctx = ToolContext(settings=settings, emitter=emitter)
    res = await ShowImageTool().run(
        ShowImageTool.Args(path="shot.png", caption="Готовый экран"), ctx
    )
    assert res.ok
    shown = [e for e in events if isinstance(e, ShowImage)]
    assert len(shown) == 1
    assert shown[0].path == "shot.png"
    assert shown[0].caption == "Готовый экран"


async def test_show_image_rejects_non_image(settings, ctx: ToolContext):
    (settings.workspace / "readme.md").write_text("# hi", encoding="utf-8")
    res = await ShowImageTool().run(ShowImageTool.Args(path="readme.md"), ctx)
    assert not res.ok
