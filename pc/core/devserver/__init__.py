"""Наблюдатель за dev-серверами: запуск, чтение логов, детекция ошибок."""

from core.devserver.detect import DetectedError, scan_output
from core.devserver.manager import DevServerManager, get_manager

__all__ = ["DetectedError", "DevServerManager", "get_manager", "scan_output"]
