"""Видео-пайплайн через ffmpeg: нарезка, сжатие, конвертация, вертикаль, аудио, кадр.

ffmpeg вызывается как внешний бинарник (он есть почти на любой системе, а тянуть
его в сборку — сотни мегабайт). Если ffmpeg не найден — инструмент честно об этом
сообщает. Все пути — внутри рабочей папки (проверяет вызывающий инструмент).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

#: Операции и их человекочитаемое описание (для схемы инструмента).
VIDEO_OPS = ("info", "trim", "compress", "convert", "vertical", "extract_audio", "frame")

#: Лимит времени на операцию: длинное видео жать долго.
_TIMEOUT = 600.0


class FFmpegUnavailable(RuntimeError):
    """ffmpeg/ffprobe не установлены."""


def _bin(name: str) -> str:
    exe = shutil.which(name)
    if exe is None:
        raise FFmpegUnavailable(
            f"{name} не найден. Установите ffmpeg (в него входит и ffprobe) и добавьте в PATH."
        )
    return exe


def _run(argv: list[str], timeout: float = _TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, check=False,
    )


def video_info(path: Path) -> dict:
    """Метаданные видео через ffprobe: длительность, разрешение, кодеки, размер."""
    proc = _run([
        _bin("ffprobe"), "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ], timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ffprobe не смог прочитать файл.")
    data = json.loads(proc.stdout or "{}")
    fmt = data.get("format", {})
    video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
    return {
        "duration_sec": round(float(fmt.get("duration", 0)), 1),
        "size_bytes": int(fmt.get("size", 0)),
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "video_codec": video_stream.get("codec_name"),
        "audio_codec": audio_stream.get("codec_name"),
        "fps": _fps(video_stream.get("r_frame_rate")),
    }


def _fps(rate: str | None) -> float | None:
    if not rate or "/" not in rate:
        return None
    try:
        num, den = rate.split("/")
        return round(int(num) / int(den), 2) if int(den) else None
    except (ValueError, ZeroDivisionError):
        return None


def _default_output(inp: Path, op: str, out_format: str = "") -> Path:
    """Имя выходного файла, если пользователь его не задал."""
    ext = {
        "extract_audio": ".mp3",
        "frame": ".png",
    }.get(op, f".{out_format}" if out_format else inp.suffix or ".mp4")
    suffix = {"vertical": "_9x16", "compress": "_small", "trim": "_cut", "convert": ""}.get(op, f"_{op}")
    return inp.with_name(f"{inp.stem}{suffix}{ext}")


def run_video_op(
    op: str,
    inp: Path,
    out: Path | None,
    *,
    start: str = "",
    end: str = "",
    out_format: str = "",
    time: str = "",
) -> tuple[Path, str]:
    """Выполняет операцию над видео. Возвращает (путь результата, краткое описание).

    Операции:
      trim          — вырезать отрезок [start, end];
      compress      — уменьшить размер файла (CRF 28, x264);
      convert       — сменить формат (out_format: mp4/webm/mkv/gif);
      vertical      — кадрировать под 9:16 (1080×1920) для Reels/Shorts;
      extract_audio — вытащить дорожку в mp3;
      frame         — снять кадр-превью в момент time (по умолчанию с начала).
    """
    ffmpeg = _bin("ffmpeg")
    output = out or _default_output(inp, op, out_format)
    output.parent.mkdir(parents=True, exist_ok=True)
    base = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]

    if op == "trim":
        if not start and not end:
            raise ValueError("Для trim укажите start и/или end (например 00:00:05).")
        argv = [*base]
        if start:
            argv += ["-ss", start]
        argv += ["-i", str(inp)]
        if end:
            argv += ["-to", end]
        argv += ["-c", "copy", str(output)]
        desc = f"вырезан отрезок {start or 'начало'}–{end or 'конец'}"
    elif op == "compress":
        argv = [*base, "-i", str(inp), "-vcodec", "libx264", "-crf", "28",
                "-preset", "fast", "-acodec", "aac", "-b:a", "128k", str(output)]
        desc = "сжато (CRF 28, x264/aac)"
    elif op == "convert":
        if output.suffix.lstrip(".") == "gif":
            argv = [*base, "-i", str(inp), "-vf", "fps=12,scale=480:-1:flags=lanczos", str(output)]
        else:
            argv = [*base, "-i", str(inp), str(output)]
        desc = f"конвертировано в {output.suffix.lstrip('.')}"
    elif op == "vertical":
        # Обрезаем центр под 9:16 и масштабируем в 1080×1920.
        vf = "crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',scale=1080:1920"
        argv = [*base, "-i", str(inp), "-vf", vf, "-c:a", "copy", str(output)]
        desc = "кадрировано под вертикаль 9:16 (1080×1920)"
    elif op == "extract_audio":
        argv = [*base, "-i", str(inp), "-vn", "-acodec", "libmp3lame", "-q:a", "2", str(output)]
        desc = "аудиодорожка извлечена в mp3"
    elif op == "frame":
        argv = [*base]
        if time:
            argv += ["-ss", time]
        argv += ["-i", str(inp), "-frames:v", "1", str(output)]
        desc = f"снят кадр на {time or '00:00:00'}"
    else:
        raise ValueError(f"Неизвестная операция «{op}». Доступны: {', '.join(VIDEO_OPS)}.")

    proc = _run(argv)
    if proc.returncode != 0 or not output.exists():
        raise RuntimeError(proc.stderr.strip()[-400:] or "ffmpeg завершился с ошибкой.")
    return output, desc
