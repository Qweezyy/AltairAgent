"""Видео-инструмент (ffmpeg). Живые операции — только если ffmpeg установлен."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from core.media.video import _default_output, _fps
from core.tools.base import ToolContext
from core.tools.builtin.media_tools import VideoTool

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
live = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg не установлен")


# --------------------------------------------------------------- чистые функции


def test_fps_parsing():
    assert _fps("30/1") == 30.0
    assert _fps("30000/1001") == 29.97
    assert _fps(None) is None
    assert _fps("bad") is None


def test_default_output_names(tmp_path):
    inp = tmp_path / "clip.mp4"
    assert _default_output(inp, "vertical").name == "clip_9x16.mp4"
    assert _default_output(inp, "extract_audio").name == "clip_extract_audio.mp3"
    assert _default_output(inp, "convert", "webm").name == "clip.webm"


def test_bad_operation(settings):
    tool = VideoTool()
    (settings.workspace / "x.mp4").write_bytes(b"not a video")
    result = _run(tool, settings, operation="nope", path="x.mp4")
    assert not result.ok
    assert "operation" in result.content


def test_info_auto_allowed(settings):
    tool = VideoTool()
    ctx = ToolContext(settings=settings)
    assert tool.auto_verdict(tool.Args(operation="info", path="x.mp4"), ctx) == "allow"
    assert tool.auto_verdict(tool.Args(operation="compress", path="x.mp4"), ctx) == "ask"


# --------------------------------------------------------------- живые операции


def _make_video(path) -> None:
    """Генерирует 3-секундный тестовый ролик 320×240 с тоном."""
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=15",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-shortest", str(path)],
        check=True,
    )


def _run(tool, settings, **kwargs):
    import asyncio

    return asyncio.run(tool.run(tool.Args(**kwargs), ToolContext(settings=settings)))


@live
def test_info(settings):
    v = settings.workspace / "t.mp4"
    _make_video(v)
    result = _run(VideoTool(), settings, operation="info", path="t.mp4")
    assert result.ok
    assert "320×240" in result.content
    assert "с" in result.content  # длительность


@live
def test_extract_audio(settings):
    v = settings.workspace / "t.mp4"
    _make_video(v)
    result = _run(VideoTool(), settings, operation="extract_audio", path="t.mp4")
    assert result.ok
    assert (settings.workspace / "t_extract_audio.mp3").exists()


@live
def test_frame(settings):
    v = settings.workspace / "t.mp4"
    _make_video(v)
    result = _run(VideoTool(), settings, operation="frame", path="t.mp4", time="00:00:01")
    assert result.ok
    frames = list(settings.workspace.glob("t_frame.png"))
    assert frames and frames[0].stat().st_size > 0


@live
def test_vertical(settings):
    v = settings.workspace / "t.mp4"
    _make_video(v)
    result = _run(VideoTool(), settings, operation="vertical", path="t.mp4")
    assert result.ok
    out = settings.workspace / "t_9x16.mp4"
    assert out.exists()
    # Проверяем, что получилось действительно 1080×1920.
    info = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert info == "1080,1920"


@live
def test_trim_requires_bounds(settings):
    v = settings.workspace / "t.mp4"
    _make_video(v)
    result = _run(VideoTool(), settings, operation="trim", path="t.mp4")
    assert not result.ok
    assert "start" in result.content
