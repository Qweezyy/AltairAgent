"""Тонкая обёртка над git.

Команды запускаются через `subprocess.run` в отдельном потоке, а не через
asyncio-сабпроцесс: на Windows asyncio-пайпы к дочернему процессу глохнут, когда
сервер работает вне главного потока (как в десктопном приложении). Git-команды
короткие, поток их не тормозит.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger("git")

#: Больше этого времени ни одна интерактивная git-команда идти не должна.
_TIMEOUT = 30.0


class GitUnavailable(RuntimeError):
    """git не установлен в системе."""


@dataclass
class GitResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def git_available() -> bool:
    return shutil.which("git") is not None


def _run_sync(args: list[str], cwd: str, timeout: float) -> GitResult:
    git = shutil.which("git")
    if git is None:
        raise GitUnavailable("git не установлен. Установите Git, чтобы работать с репозиторием.")
    try:
        proc = subprocess.run(
            [git, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return GitResult(-1, "", f"git не выполнился: {exc}")
    return GitResult(proc.returncode, proc.stdout or "", proc.stderr or "")


async def run_git(args: list[str], cwd: Path | str, *, timeout: float = _TIMEOUT) -> GitResult:
    """Запускает git-команду в рабочей папке. Не бросает, кроме GitUnavailable."""
    return await asyncio.to_thread(_run_sync, args, str(cwd), timeout)


async def is_repo(cwd: Path | str) -> bool:
    """Является ли папка git-репозиторием."""
    if not git_available():
        return False
    result = await run_git(["rev-parse", "--is-inside-work-tree"], cwd)
    return result.ok and result.stdout.strip() == "true"


async def current_branch(cwd: Path | str) -> str:
    result = await run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    return result.stdout.strip() if result.ok else ""
