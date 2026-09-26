"""Подтверждение опасных действий пользователем.

Ядро не решает, как спрашивать — оно вызывает `Approver`. UI (WebSocket,
консоль, тесты) подставляет свою реализацию. По умолчанию — `always_allow`,
чтобы headless-сценарии не зависали.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from core.settings import Settings, get_settings


@dataclass(slots=True)
class ApprovalRequest:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = "Действие может изменить систему."
    #: read | edit | execute | network — от неё зависит режим разрешений.
    category: str = "edit"
    #: Тир риска (safe|low|moderate|high|critical) от независимого верификатора —
    #: интерфейс показывает его на карточке подтверждения.
    tier: str = "low"
    #: Почему верификатор поднял риск (конкретные причины для пользователя).
    reasons: list[str] = field(default_factory=list)


#: Возвращает True, если действие разрешено.
Approver = Callable[[ApprovalRequest], Awaitable[bool]]


async def always_allow(request: ApprovalRequest) -> bool:  # noqa: ARG001
    return True


async def always_deny(request: ApprovalRequest) -> bool:  # noqa: ARG001
    return False


def needs_approval(*, dangerous: bool, settings: Settings | None = None) -> bool:
    """Нужно ли спрашивать пользователя для инструмента с данным уровнем риска."""
    settings = settings or get_settings()
    mode = settings.approval_mode
    if mode == "all":
        return True
    if mode == "dangerous":
        return dangerous
    return False
