"""Иерархия исключений агента.

Правило: наружу (в UI) летят только AgentError и его наследники.
Всё остальное — баг, который надо чинить, а не глотать.
"""

from __future__ import annotations


class AgentError(Exception):
    """Базовая ошибка агента. Сообщение показывается пользователю."""


class ConfigError(AgentError):
    """Некорректная или неполная конфигурация."""


class LLMError(AgentError):
    """Ошибка обращения к модели (сеть, аутентификация, лимиты)."""


class ToolError(AgentError):
    """Ошибка выполнения инструмента, которую можно вернуть модели как текст."""


class ToolNotFound(ToolError):
    """Модель попросила несуществующий инструмент."""


class ToolInputError(ToolError):
    """Аргументы инструмента не прошли валидацию схемы."""


class ToolTimeout(ToolError):
    """Инструмент превысил лимит времени."""


class PermissionDenied(ToolError):
    """Действие запрещено политикой безопасности или отклонено пользователем."""


class PathNotAllowed(PermissionDenied):
    """Путь вне разрешённых корней."""


class MCPError(AgentError):
    """Ошибка взаимодействия с MCP-сервером."""
