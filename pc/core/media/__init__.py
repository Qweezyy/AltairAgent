"""Работа с медиа: видео через ffmpeg."""

from core.media.video import VIDEO_OPS, FFmpegUnavailable, run_video_op, video_info

__all__ = ["FFmpegUnavailable", "VIDEO_OPS", "run_video_op", "video_info"]
