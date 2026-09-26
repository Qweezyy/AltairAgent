"""Автоматическая проверка кода: тесты, линтеры, типы."""

from core.quality.detect import (
    Command,
    describe_project,
    detect_lint_commands,
    detect_test_commands,
)
from core.quality.report import CheckReport, build_report

__all__ = [
    "CheckReport",
    "Command",
    "build_report",
    "describe_project",
    "detect_lint_commands",
    "detect_test_commands",
]
