"""Групповой чат равноправных агентов (эксперимент).

Несколько ИИ-агентов решают ОДНУ задачу. Каждый — как отдельный программист:
он не видит действий коллег и знает только о их существовании. Единственный
канал связи между ними — общий чат (`GroupChat`). У каждого агента свой участок
задачи и свой урезанный набор инструментов, поэтому в одиночку задачу не закрыть:
им приходится договариваться через чат.

Точка входа — `Swarm` (см. `orchestrator.py`). Пример запуска — `python main.py
--swarm "задача"`.
"""

from __future__ import annotations

from core.swarm.chat import ChatMessage, GroupChat
from core.swarm.member import MemberSpec, build_default_team, load_team
from core.swarm.orchestrator import Swarm, SwarmResult

__all__ = [
    "ChatMessage",
    "GroupChat",
    "MemberSpec",
    "Swarm",
    "SwarmResult",
    "build_default_team",
    "load_team",
]
