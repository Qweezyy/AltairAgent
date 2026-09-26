"""Реестр инструментов.

Один реестр = один набор инструментов, доступный модели. Реестр можно
клонировать и дополнять (например, добавить MCP-инструменты только для
конкретной сессии), не трогая глобальный.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import Any, TypeVar

from core.logging_setup import get_logger
from core.tools.base import Tool

logger = get_logger("tools.registry")

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

T = TypeVar("T", bound=Tool)


class ToolRegistry:
    """Коллекция инструментов с проверкой имён и защитой от коллизий."""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.add(tool)

    # --- регистрация -------------------------------------------------

    def add(self, tool: Tool, *, override: bool = False) -> Tool:
        if not _NAME_RE.match(tool.name):
            raise ValueError(
                f"Недопустимое имя инструмента '{tool.name}': разрешены латиница, "
                "цифры, '_' и '-', до 64 символов."
            )
        if tool.name in self._tools and not override:
            raise ValueError(
                f"Инструмент '{tool.name}' уже зарегистрирован "
                f"({type(self._tools[tool.name]).__name__}). Выберите другое имя."
            )
        self._tools[tool.name] = tool
        return tool

    def extend(self, tools: Iterable[Tool], *, override: bool = False) -> None:
        for tool in tools:
            self.add(tool, override=override)

    def register(self, cls: type[T]) -> type[T]:
        """Декоратор над классом инструмента: `@registry.register`."""
        self.add(cls())
        return cls

    def remove(self, name: str) -> None:
        self._tools.pop(name, None)

    def clone(self) -> ToolRegistry:
        """Копия реестра (сами инструменты не дублируются — они stateless)."""
        new = ToolRegistry()
        new._tools = dict(self._tools)
        return new

    # --- доступ ------------------------------------------------------

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[Tool]:
        return [self._tools[name] for name in sorted(self._tools)]

    def schemas(self, names: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """Tool schemas for a model request; `names` limits them (deferred loading)."""
        if names is None:
            return [tool.schema() for tool in self.all()]
        wanted = set(names)
        return [tool.schema() for tool in self.all() if tool.name in wanted]

    def suggest(self, name: str, limit: int = 3) -> list[str]:
        """Похожие имена — чтобы вернуть модели полезную подсказку."""
        import difflib

        return difflib.get_close_matches(name, self.names(), n=limit, cutoff=0.5)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __iter__(self) -> Iterator[Tool]:
        return iter(self.all())

    def __len__(self) -> int:
        return len(self._tools)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ToolRegistry {len(self)} tools>"


#: Глобальный реестр встроенных инструментов.
#: Наполняется в core/tools/__init__.py через build_default_registry().
registry = ToolRegistry()
