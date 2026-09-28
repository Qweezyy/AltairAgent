"""The PC's reminder scheduler.

Every second it fires what is due in reminders.json (times, waits, ended background jobs;
conditions on the PC's signals every CONDITION_INTERVAL) and hands the fired ones to their
chats through the ChatHub: a busy chat gets them at its next step, an idle one — shown or
not — is woken with a new turn. What fired while the app was closed is due at the first pass
after the start, and what fired but never reached its chat (the app closed first) is handed
out again.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from core.logging_setup import get_logger
from core.reminders import ReminderStore

logger = get_logger("server.reminders")

#: Times, waits and background jobs are checked this often: a 30-second wait must not
#: become a 50-second one.
DEFAULT_INTERVAL = 1.0
#: Conditions need a network probe and sensors, so they are checked less often (like the
#: conditions on the phone).
CONDITION_INTERVAL = 20.0
#: Old delivered records are pruned this often.
PRUNE_INTERVAL = 3600.0


async def _check_network(timeout: float = 2.0) -> str:
    """online/offline: whether a TCP connection to a public DNS server opens."""
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
    """battery (%) and charging (true/false) through psutil, when it is installed."""
    try:
        import psutil  # optional: many PCs have no battery or no psutil
    except ImportError:
        return {}
    try:
        battery = psutil.sensors_battery()
    except Exception:  # noqa: BLE001 - the sensor may raise on a desktop
        return {}
    if battery is None:
        return {}
    return {"battery": battery.percent, "charging": "true" if battery.power_plugged else "false"}


def _phone_online(app: Any) -> str:
    conns = getattr(app.state, "connections", set())
    return "true" if any(getattr(c, "phone_connected", False) for c in conns) else "false"


async def current_signals(app: Any) -> dict[str, Any]:
    """The PC's signals for conditional reminders."""
    signals: dict[str, Any] = {
        "network": await _check_network(),
        "phone_online": _phone_online(app),
    }
    signals.update(_battery_signals())
    return signals


def job_state(name: str, ref: str = "") -> tuple[bool, str]:
    """Whether a background job has ended, and how (with the last lines of its output, so
    the woken chat need not read it first). `ref` is the pid the job was watched under."""
    from core.devserver import get_manager
    from core.devserver.manager import DevServerError

    try:
        job = get_manager().get(name)
    except DevServerError:
        job = None
    if job is None or (ref and str(getattr(job.proc, "pid", "")) != ref):
        # Jobs do not outlive the app: after a restart the job is gone (or the name is
        # another job's now).
        return True, "is gone: the app was closed or restarted and the job stopped with it"
    if job.is_running():
        return False, ""
    code = job.exit_code()
    verdict = "finished successfully" if code == 0 else f"failed with exit code {code}"
    tail = "\n".join(job.tail(6))
    return True, f"{verdict}; last lines of its output:\n{tail}" if tail else verdict


async def tick(app: Any, now: float | None = None, with_conditions: bool = True) -> list:
    """One pass: fire what is due and hand everything fired but not delivered to its chat."""
    settings = app.state.settings
    store = ReminderStore(settings.data_dir)
    now = now or time.time()
    active = await asyncio.to_thread(store.active)
    signals = None
    if with_conditions and any(r.kind == "condition" for r in active):
        signals = await current_signals(app)
    fired = await asyncio.to_thread(store.fire_due, now, signals, job_state) if active else []
    for reminder in fired:
        logger.info("reminder fired: %s", reminder.describe())
    pending = await asyncio.to_thread(store.undelivered)
    hub = getattr(app.state, "chats", None)
    if pending and hub is not None:
        await hub.dispatch(pending)
    return fired


async def reminder_scheduler(app: Any, interval: float = DEFAULT_INTERVAL) -> None:
    """The endless pass loop, started in the app's lifespan."""
    logger.info("reminder scheduler started")
    last_conditions = 0.0
    last_prune = 0.0
    while True:
        now = time.time()
        with_conditions = now - last_conditions >= CONDITION_INTERVAL
        if with_conditions:
            last_conditions = now
        try:
            await tick(app, now, with_conditions)
            if now - last_prune >= PRUNE_INTERVAL:
                last_prune = now
                await asyncio.to_thread(ReminderStore(app.state.settings.data_dir).prune, now)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one bad pass must not stop the scheduler
            logger.warning("reminder scheduler pass failed", exc_info=True)
        await asyncio.sleep(interval)
