"""Инструмент обработки видео через ffmpeg."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from core.events import ArtifactCreated
from core.i18n import tr
from core.media import VIDEO_OPS, FFmpegUnavailable, run_video_op, video_info
from core.security.paths import resolve_path, safe_relpath
from core.tools.base import Tool, ToolContext, ToolResult


def _human_size(n: int) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.0f} ТБ"


class VideoArgs(BaseModel):
    operation: str = Field(
        description=(
            "info (сведения), trim (вырезать отрезок), compress (сжать), convert (сменить формат), "
            "vertical (кадрировать под 9:16), extract_audio (вытащить звук), frame (снять кадр)"
        )
    )
    path: str = Field(description="Путь к видеофайлу в рабочей папке")
    output: str = Field(default="", description="Куда сохранить результат (пусто — имя подберётся само)")
    start: str = Field(default="", description="Начало отрезка для trim, например 00:00:05")
    end: str = Field(default="", description="Конец отрезка для trim, например 00:00:20")
    format: str = Field(default="", description="Целевой формат для convert: mp4, webm, mkv, gif")
    time: str = Field(default="", description="Момент для frame, например 00:00:03")


class VideoTool(Tool):
    name = "video"
    description = (
        "Обрабатывает видео через ffmpeg: info — длительность/разрешение/кодеки; trim — вырезать "
        "отрезок; compress — уменьшить размер; convert — сменить формат (в т.ч. gif); vertical — "
        "кадрировать под 9:16 для Reels/Shorts; extract_audio — вытащить звук в mp3; frame — снять "
        "кадр-превью. Результат сохраняется в рабочую папку и появляется в артефактах."
    )
    Args = VideoArgs
    category = "edit"  # создаёт новые файлы в рабочей папке
    dangerous = True
    timeout = None  # ffmpeg держит свой лимит внутри

    def approval_reason(self, args: VideoArgs) -> str:  # type: ignore[override]
        return tr("appr.video", op=args.operation, path=args.path)

    def auto_verdict(self, args: VideoArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        # info ничего не создаёт — безопасно; остальное пишет файл.
        return "allow" if args.operation.strip() == "info" else "ask"

    async def run(self, args: VideoArgs, ctx: ToolContext) -> ToolResult:
        op = args.operation.strip()
        if op not in VIDEO_OPS:
            return ToolResult.fail(f"operation должен быть одним из: {', '.join(VIDEO_OPS)}.")
        inp = resolve_path(args.path, settings=ctx.settings, must_exist=True, must_be_file=True)

        if op == "info":
            try:
                info = await asyncio.to_thread(video_info, inp)
            except FFmpegUnavailable as exc:
                return ToolResult.fail(str(exc))
            except Exception as exc:  # noqa: BLE001
                return ToolResult.fail(f"Не удалось прочитать видео: {str(exc)[:300]}")
            res = "\n".join([
                f"Файл: {inp.name}",
                f"Длительность: {info['duration_sec']} с",
                f"Разрешение: {info['width']}×{info['height']}" if info["width"] else "Разрешение: —",
                f"Кадров/с: {info['fps']}" if info["fps"] else "",
                f"Видео-кодек: {info['video_codec']}, аудио: {info['audio_codec'] or 'нет'}",
                f"Размер: {_human_size(info['size_bytes'])}",
            ])
            return ToolResult(content="\n".join(ln for ln in res.splitlines() if ln))

        out = (
            resolve_path(args.output, settings=ctx.settings)
            if args.output.strip()
            else None
        )
        try:
            output, desc = await asyncio.to_thread(
                run_video_op, op, inp, out,
                start=args.start.strip(), end=args.end.strip(),
                out_format=args.format.strip(), time=args.time.strip(),
            )
        except FFmpegUnavailable as exc:
            return ToolResult.fail(str(exc))
        except (ValueError, RuntimeError) as exc:
            return ToolResult.fail(f"ffmpeg: {str(exc)[:400]}")

        rel = safe_relpath(output, ctx.settings).replace("\\", "/")
        size = output.stat().st_size
        kind = "image" if output.suffix.lower() in (".png", ".jpg") else "file"
        await ctx.emitter(
            ArtifactCreated(path=rel, name=output.name, kind=kind, size_bytes=size)
        )
        return ToolResult(content=f"Готово: {desc}. Результат: {rel} ({_human_size(size)}).")
