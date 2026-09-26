"""Смоук-проверки собранного агента: реестр инструментов и версия.

Проверяют РЕАЛЬНОЕ состояние кода (импорт, сборка реестра), а не то, что
удобно тестам приложения. Импорт core обеспечивает conftest (pc/ в sys.path).
"""

from __future__ import annotations

import re

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def test_registry_builds_and_is_sane():
    from core.tools import build_default_registry

    registry = build_default_registry()
    tools = registry.all()

    assert len(tools) >= 30, f"подозрительно мало инструментов: {len(tools)}"

    names = [t.name for t in tools]
    dups = {n for n in names if names.count(n) > 1}
    assert not dups, f"дублирующиеся имена инструментов: {sorted(dups)}"

    for t in tools:
        assert NAME_RE.match(t.name), f"недопустимое имя инструмента: {t.name!r}"
        desc = (t.description or "").strip()
        assert len(desc) >= 10, f"пустое/слишком короткое описание у {t.name!r}"


def test_version_parses():
    from core.version import __version__, version_tuple

    parts = version_tuple(__version__)
    assert isinstance(parts, tuple) and len(parts) >= 2
    assert all(isinstance(p, int) for p in parts), f"версия не числовая: {__version__!r}"
