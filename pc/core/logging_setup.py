"""Настройка логирования: один раз, из одного места, всегда UTF-8.

Никогда не вызывайте logging.basicConfig() в других модулях — только
`setup_logging()` из точки входа.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys

from core.settings import get_settings

_CONFIGURED = False

FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging(force: bool = False) -> logging.Logger:
    """Инициализирует корневой логгер: файл с ротацией + консоль."""
    global _CONFIGURED
    root = logging.getLogger()
    if _CONFIGURED and not force:
        return root

    settings = get_settings()
    level = getattr(logging, settings.log_level, logging.INFO)

    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(FORMAT)

    file_handler = logging.handlers.RotatingFileHandler(
        settings.logs_dir / "agent.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
        errors="replace",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    root.addHandler(file_handler)

    stream = sys.stderr
    # Windows-консоль по умолчанию не UTF-8 — иначе падают эмодзи и кириллица.
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - зависит от терминала
            pass
    console = logging.StreamHandler(stream)
    console.setFormatter(formatter)
    console.setLevel(level)
    root.addHandler(console)

    root.setLevel(logging.DEBUG)

    # Библиотеки слишком болтливы на DEBUG.
    for noisy in ("httpx", "httpcore", "openai", "urllib3", "asyncio", "multipart"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))

    _CONFIGURED = True
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
