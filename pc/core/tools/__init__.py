"""Публичный API подсистемы инструментов."""

from __future__ import annotations

from core.tools.base import EmptyArgs, Tool, ToolContext, ToolResult, truncate_output
from core.tools.registry import ToolRegistry, registry


def build_default_registry() -> ToolRegistry:
    """Создаёт реестр со всеми встроенными инструментами.

    Возвращает новый объект: сессии не должны делить изменяемое состояние.
    """
    from core.bodies_routing import routed_all
    from core.tools.builtin import builtin_tools

    # The machine tools can also run on a server (core/bodies_routing.py); without servers they
    # are exactly the tools they were.
    return ToolRegistry(routed_all(builtin_tools()))


__all__ = [
    "EmptyArgs",
    "Tool",
    "ToolContext",
    "ToolRegistry",
    "ToolResult",
    "build_default_registry",
    "registry",
    "truncate_output",
]
