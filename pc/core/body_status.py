"""What a body is and how loaded it is right now: its card for the bodies registry.

The facts (system, processor, memory, GPU, Docker, browser) change rarely and are cached; the load
(CPU, memory, disk, running tasks) is read on every call — the PC asks every server for it every
15 s through the tunnel (core/servers/tunnel.py), and the window shows it.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from core.logging_setup import get_logger
from core.version import __version__

logger = get_logger("body_status")

_facts: dict[str, Any] | None = None


def _gpus() -> list[str]:
    """NVIDIA GPUs by name (nvidia-smi); other GPUs are not reported yet."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "--query-gpu=name", "--format=csv,noheader"], capture_output=True,
                             text=True, timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("nvidia-smi: %s", exc)
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _system() -> str:
    if platform.system() == "Linux":
        try:
            for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
        except OSError as exc:
            logger.debug("os-release: %s", exc)
    return f"{platform.system()} {platform.release()}".strip()


def facts() -> dict[str, Any]:
    """What this machine is: read once (nvidia-smi and the like are slow)."""
    global _facts
    if _facts is None:
        import psutil

        _facts = {
            "system": _system(),
            "os": platform.system().lower(),
            "arch": platform.machine().lower(),
            "cpus": os.cpu_count() or 0,
            "mem_mb": psutil.virtual_memory().total // (1024 * 1024),
            "gpus": _gpus(),
            "docker": bool(shutil.which("docker")),
            "version": __version__,
        }
    return _facts


def load(data_dir: Path, running_tasks: int = 0) -> dict[str, Any]:
    """The load right now. The CPU figure is since the previous call (the first one is 0)."""
    import psutil

    mem = psutil.virtual_memory()
    try:
        disk = shutil.disk_usage(data_dir if data_dir.exists() else data_dir.anchor or "/")
        disk_total, disk_free = disk.total // (1024 * 1024), disk.free // (1024 * 1024)
    except OSError as exc:
        logger.debug("disk usage: %s", exc)
        disk_total = disk_free = 0
    return {
        "cpu_pct": round(psutil.cpu_percent(interval=None), 1),
        "mem_used_mb": (mem.total - mem.available) // (1024 * 1024),
        "disk_total_mb": disk_total,
        "disk_free_mb": disk_free,
        "tasks": running_tasks,
        "uptime_s": int(time.time() - psutil.boot_time()),
        "at": time.time(),
    }


def status(identity_card: dict[str, Any], data_dir: Path, running_tasks: int = 0) -> dict[str, Any]:
    card = {k: v for k, v in identity_card.items() if k != "public_key"}
    return {**card, **facts(), "load": load(data_dir, running_tasks)}
