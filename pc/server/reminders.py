"""Фоновый планировщик напоминаний ПК.

Раз в `interval` секунд опрашивает reminders.json, вычисляет текущие сигналы ПК
(сеть, батарея, подключён ли телефон по мосту), зажигает наступившие напоминания
и условия, помечает их сработавшими и шлёт уведомление в тот чат, которому они
принадлежат. Контекст срабатывания модель получит из системного промпта
(ReminderStore.prompt_section) при следующем ответе — это делает core/agent/runner.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from core.logging_setup import get_logger
from core.reminders import ReminderStore

logger = get_logger("server.reminders")

#: Как часто опрашивать. Неточный таймер (не будильник) — как условия на телефоне.
DEFAULT_INTERVAL = 20.0


async def _check_network(timeout: float = 2.0) -> str:
    """online/offline по факту установки TCP-соединения с публичным DNS."""
    try:
        fut = asyncio.open_connection("1.1.1.1", 53)
        _, writer = await asyncio.wait_for(fut, timeout)
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return "online"
    except (OSError, asyncio.TimeoutError):
        return "offline"


def _battery_signals() -> dict[str, Any]:
    """battery(%) и charging(true/false) через psutil, если он установлен."""
    try:
        import psutil  # опциональная зависимость — на многих ПК её нет
    except ImportError:
        return {}
    try:
        battery = psutil.sensors_battery()
    except Exception:  # noqa: BLE001 - датчик может бросить на десктопе
        return {}
    if battery is None:
        return {}
    return {"battery": battery.percent, "charging": "true" if battery.power_plugged else "false"}


def _phone_online(app: Any) -> str:
    conns = getattr(app.state, "connections", set())
    return "true" if any(getattr(c, "phone_connected", False) for c in conns) else "false"


async def current_signals(app: Any) -> dict[str, Any]:
    """Снимок сигналов ПК для проверки условных напоминаний."""
    signals: dict[str, Any] = {
        "network": await _check_network(),
        "phone_online": _phone_online(app),
    }
    signals.update(_battery_signals())
    return signals


async def tick(app: Any) -> list:
    """Один проход планировщика: зажечь наступившее и уведомить чаты."""
    settings = app.state.settings
    store = ReminderStore(settings.data_dir)
    if not store.active():
        return []
    signals = await current_signals(app)
    due = store.due(time.time(), signals)
    if not due:
        return []
    store.mark_fired({r.id for r in due})
    conns = getattr(app.state, "connections", set())
    for reminder in due:
        payload = {
            "type": "reminder.fired",
            "id": reminder.id,
            "kind": reminder.kind,
            "note": reminder.note,
            "text": reminder.describe(),
        }
        for conn in list(conns):
            session = getattr(conn, "session", None)
            if session is not None and getattr(session, "id", None) == reminder.session_id:
                await conn.send(payload)
        logger.info("Напоминание сработало: %s", reminder.describe())
    return due


async def reminder_scheduler(app: Any, interval: float = DEFAULT_INTERVAL) -> None:
    """Бесконечный цикл опроса — запускается в lifespan приложения."""
    logger.info("Планировщик напоминаний запущен (интервал %.0f с).", interval)
    while True:
        try:
            await tick(app)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - планировщик не должен падать целиком
            logger.debug("Сбой прохода планировщика напоминаний", exc_info=True)
        await asyncio.sleep(interval)
