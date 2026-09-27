"""Logging setup: once, from one place, always UTF-8.

Never call logging.basicConfig() in other modules: only `setup_logging()` from the entry
point.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys

from core.settings import get_settings

_CONFIGURED = False

FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


class _SharedRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """A rotating log that keeps writing when it cannot rotate.

    On Windows the rename of a full log fails while another process has it open (a second
    copy of the app, the dev server, a `--check` run). The stock handler then drops that
    record and every one after it, since it retries the rename on each line: the log went
    silent for the rest of the session. Here a failed rotation just continues the same file.
    """

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except OSError:
            if self.stream is None or self.stream.closed:
                self.stream = self._open()
            self._rollover_failed_at = self.stream.tell()

    def shouldRollover(self, record: logging.LogRecord) -> int:
        # After a failed rotation, try again only once the file has grown by another 1 MiB.
        failed = getattr(self, "_rollover_failed_at", None)
        if failed is not None and self.stream is not None and self.stream.tell() < failed + 1024 * 1024:
            return 0
        return super().shouldRollover(record)


def setup_logging(force: bool = False) -> logging.Logger:
    """Sets up the root logger: a rotating file plus the console."""
    global _CONFIGURED
    root = logging.getLogger()
    if _CONFIGURED and not force:
        return root

    settings = get_settings()
    level = getattr(logging, settings.log_level, logging.INFO)

    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(FORMAT)

    file_handler = _SharedRotatingFileHandler(
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
    # The Windows console is not UTF-8 by default: emoji and Cyrillic would fail.
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - depends on the terminal; the file log is unaffected
            logging.getLogger(__name__).debug("console stays in its own encoding", exc_info=True)
    console = logging.StreamHandler(stream)
    console.setFormatter(formatter)
    console.setLevel(level)
    root.addHandler(console)

    root.setLevel(logging.DEBUG)

    # Libraries are too chatty on DEBUG.
    for noisy in ("httpx", "httpcore", "openai", "urllib3", "asyncio", "multipart"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))

    _CONFIGURED = True
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
