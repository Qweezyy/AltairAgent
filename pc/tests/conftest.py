"""Общие фикстуры тестов.

ВАЖНО: тесты никогда не должны трогать реальный workspace пользователя —
`settings` подменяется на временную директорию.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.i18n import set_ui_language  # noqa: E402
from core.settings import Settings  # noqa: E402
from core.tools.base import ToolContext  # noqa: E402


@pytest.fixture(autouse=True)
def _english_ui():
    # The UI language is process-wide; a test that switches it must not leak into others.
    set_ui_language("en")
    yield
    set_ui_language("en")


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    # app_path тоже во временной папке: иначе тесты писали бы навыки и чаты
    # в реальную папку приложения.
    app_dir = tmp_path / "_app"
    workspace = tmp_path / "project"
    app_dir.mkdir()
    workspace.mkdir()
    return Settings(
        openrouter_api_key="test-key",
        app_path=app_dir,
        workspace_path=workspace,
        approval_mode="auto",
        max_steps=5,
        tool_output_limit=5000,
        _env_file=None,  # не читаем .env пользователя
    )


@pytest.fixture()
def ctx(settings: Settings) -> ToolContext:
    return ToolContext(settings=settings)
