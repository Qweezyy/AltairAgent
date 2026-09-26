"""Жёсткие инварианты безопасности — то, что НИКОГДА не должно сломаться к релизу.

Если дев-чат случайно ослабит песочницу путей или вычистит паттерн из чёрного
списка команд — эти тесты упадут. В этом и смысл независимой приёмки.
"""

from __future__ import annotations

import re

import pytest


def test_path_sandbox_blocks_escape(tmp_path):
    """resolve_path не должен выпускать за пределы рабочей папки."""
    from core.security.paths import PathNotAllowed, resolve_path
    from core.settings import get_settings

    settings = get_settings().for_workspace(tmp_path)

    # Валидный относительный путь внутри — разрешён и лежит под workspace.
    ok = resolve_path("notes.txt", settings=settings)
    assert str(ok).startswith(str(tmp_path.resolve()))

    # Выход наружу — запрещён.
    for escape in ("../../etc/passwd", "..\\..\\Windows\\system32", "/etc/shadow"):
        with pytest.raises(PathNotAllowed):
            resolve_path(escape, settings=settings)


def test_dangerous_commands_are_blocked():
    """Чёрный список команд обязан покрывать разрушительные операции."""
    from core.security.risk import BLOCK_PATTERNS

    compiled = [re.compile(p, re.IGNORECASE) for p, _ in BLOCK_PATTERNS]
    must_block = [
        "rm -rf /",
        "sudo rm -rf /home/user",
        "format c:",
        "shutdown /s /t 0",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda",
        ":(){ :|:& };:",
        "diskpart",
    ]
    for cmd in must_block:
        assert any(rx.search(cmd) for rx in compiled), f"опасная команда НЕ блокируется: {cmd!r}"
