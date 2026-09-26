"""WebSocket-транспорт для эксперимента «групповой чат агентов».

Отдельный эндпоинт (`/ws/swarm`), не связанный с обычным чатом: своя команда
участников, свои события (swarm.*), свой запуск/остановка. Ядро эксперимента —
в `core/swarm`; здесь только транспорт: приём конфигурации из интерфейса,
сериализация событий в сокет и управление запуском.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from core.errors import ConfigError
from core.events import Event
from core.logging_setup import get_logger
from core.security.approval import always_allow
from core.settings import get_settings
from core.swarm import MemberSpec, Swarm, build_default_team
from core.swarm.orchestrator import CLIENT_NAME
from core.tools.builtin import builtin_tools

logger = get_logger("server.swarm")

#: Разумные рамки эксперимента, чтобы из интерфейса нельзя было запустить
#: неуправляемую «толпу» агентов на сотни раундов.
_MAX_MEMBERS = 8
_MAX_ROUNDS = 20


def available_tools() -> list[dict[str, Any]]:
    """Список инструментов для редактора команды: имя, описание, категория, опасность."""
    tools = []
    for tool in builtin_tools():
        if tool.name.startswith("chat_"):  # инструменты чата добавляются всем автоматически
            continue
        tools.append(
            {
                "name": tool.name,
                "description": tool.description.strip(),
                "category": tool.category,
                "dangerous": tool.dangerous,
            }
        )
    return sorted(tools, key=lambda t: (t["category"], t["name"]))


def default_config() -> dict[str, Any]:
    """Стартовая конфигурация для интерфейса: команда по умолчанию, инструменты, модель."""
    settings = get_settings()
    team = [
        {"name": m.name, "role": m.role, "charter": m.charter, "tools": m.tools}
        for m in build_default_team()
    ]
    return {
        "team": team,
        "tools": available_tools(),
        "model": settings.default_model,
        "workspace": str(settings.workspace),
        "max_members": _MAX_MEMBERS,
        "max_rounds": _MAX_ROUNDS,
        "client_name": CLIENT_NAME,
    }


class SwarmConnection:
    """Одно подключение к эндпоинту группового чата."""

    def __init__(self, websocket: WebSocket) -> None:
        self.ws = websocket
        self.settings = get_settings()
        self.outbox: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=4000)
        self.run_task: asyncio.Task | None = None
        self._writer: asyncio.Task | None = None

    # ---------------------------------------------------------------- io

    async def serve(self) -> None:
        await self.ws.accept()
        self._writer = asyncio.create_task(self._writer_loop(), name="swarm-ws-writer")
        await self.send({"type": "swarm.ready", "config": default_config()})
        try:
            while True:
                message = await self.ws.receive_json()
                await self._handle(message)
        except WebSocketDisconnect:
            logger.info("Клиент группового чата отключился.")
        except (ValueError, TypeError) as exc:
            logger.warning("Некорректное сообщение группового чата: %s", exc)
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        if self.run_task and not self.run_task.done():
            self.run_task.cancel()
            await asyncio.gather(self.run_task, return_exceptions=True)
        await self.outbox.put(None)
        if self._writer:
            await asyncio.gather(self._writer, return_exceptions=True)

    async def _writer_loop(self) -> None:
        while True:
            payload = await self.outbox.get()
            if payload is None:
                return
            try:
                await self.ws.send_json(payload)
            except (WebSocketDisconnect, RuntimeError):
                return
            except Exception:  # noqa: BLE001
                logger.debug("Не удалось отправить событие swarm в сокет", exc_info=True)
                return

    async def send(self, payload: dict[str, Any]) -> None:
        try:
            self.outbox.put_nowait(payload)
        except asyncio.QueueFull:
            logger.warning("Очередь swarm переполнена — событие отброшено.")

    async def emit(self, event: Event) -> None:
        await self.send(event.model_dump(mode="json"))

    # ----------------------------------------------------------- команды

    async def _handle(self, message: dict[str, Any]) -> None:
        kind = str(message.get("type") or "")
        if kind == "run":
            await self._start(message)
        elif kind == "stop":
            await self._stop()
        elif kind == "ping":
            await self.send({"type": "pong"})
        else:
            await self.send({"type": "log", "level": "warning", "text": f"Неизвестная команда: {kind}"})

    def _parse_members(self, raw: Any) -> list[MemberSpec]:
        if not isinstance(raw, list) or not raw:
            raise ValueError("Не задана команда участников.")
        if len(raw) > _MAX_MEMBERS:
            raise ValueError(f"Слишком много участников (максимум {_MAX_MEMBERS}).")
        members: list[MemberSpec] = []
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ValueError(f"Участник #{i + 1}: ожидался объект.")
            name = str(item.get("name") or "").strip()
            charter = str(item.get("charter") or "").strip()
            if not name:
                raise ValueError(f"Участник #{i + 1}: пустое имя.")
            if not charter:
                raise ValueError(f"Участник «{name}»: не описана зона ответственности.")
            tools = [str(t) for t in item.get("tools") or [] if str(t).strip()]
            members.append(
                MemberSpec(
                    name=name,
                    role=str(item.get("role") or "участник").strip(),
                    charter=charter,
                    tools=tools,
                )
            )
        return members

    async def _start(self, message: dict[str, Any]) -> None:
        if self.run_task and not self.run_task.done():
            await self.send({"type": "log", "level": "warning", "text": "Эксперимент уже идёт."})
            return

        task = str(message.get("task") or "").strip()
        try:
            members = self._parse_members(message.get("members"))
        except ValueError as exc:
            await self.send({"type": "swarm.error", "message": str(exc)})
            return
        if not task:
            await self.send({"type": "swarm.error", "message": "Пустая задача."})
            return

        rounds = max(1, min(_MAX_ROUNDS, int(message.get("rounds") or 6)))
        model = str(message.get("model") or "").strip() or None
        workspace = str(message.get("workspace") or "").strip()

        settings = self.settings
        if workspace:
            try:
                settings = await asyncio.to_thread(self.settings.for_workspace, workspace)
            except ConfigError as exc:
                await self.send({"type": "swarm.error", "message": f"Рабочая папка недоступна: {exc}"})
                return
        # Участники действуют автономно (без карточек подтверждения на каждый шаг):
        # песочница путей и чёрный список команд продолжают защищать систему.
        settings = settings.model_copy(update={"approval_mode": "auto"})

        try:
            swarm = Swarm(
                task,
                members,
                settings=settings,
                model=model,
                emitter=self.emit,
                approver=always_allow,
                max_rounds=rounds,
            )
        except ValueError as exc:
            await self.send({"type": "swarm.error", "message": str(exc)})
            return

        await self.send(
            {
                "type": "swarm.started",
                "task": task,
                "rounds": rounds,
                "workspace": str(settings.workspace),
                "members": [{"name": m.name, "role": m.role} for m in members],
            }
        )
        self.run_task = asyncio.create_task(self._run(swarm), name="swarm-run")

    async def _run(self, swarm: Swarm) -> None:
        try:
            await swarm.run()
        except asyncio.CancelledError:
            await self.send({"type": "log", "level": "warning", "text": "Эксперимент остановлен."})
            raise
        except Exception as exc:  # noqa: BLE001 - сбой не должен ронять сокет
            logger.exception("Ошибка группового чата")
            await self.send({"type": "swarm.error", "message": f"Внутренняя ошибка: {exc}"})

    async def _stop(self) -> None:
        if self.run_task and not self.run_task.done():
            self.run_task.cancel()
            await self.send({"type": "log", "level": "info", "text": "Останавливаю эксперимент..."})
        else:
            await self.send({"type": "log", "level": "info", "text": "Нечего останавливать."})
