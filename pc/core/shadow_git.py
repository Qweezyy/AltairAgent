"""«Теневой git» для отката эффектов shell-команд.

Правки файлов инструментами (write/edit/patch) уже откатываются по-файловым снимкам
(core/checkpoints.py). Но `execute_command` может изменить что угодно и заранее
неизвестно что. Решение (как у Cline, без Docker): держим ОТДЕЛЬНЫЙ git-репозиторий,
чей git-dir лежит в данных приложения, а work-tree = рабочая папка. До команды делаем
снимок, после — второй, сравниваем и узнаём ТОЧНО, какие файлы затронула команда.
Настоящий `.git` пользователя не трогается (у нас свой GIT_DIR).

Откат делаем не через сам теневой git, а регистрируя «прежнее состояние» затронутых
файлов в обычном CheckpointStore — тогда работает существующий UI отката, а радиус
воздействия ограничен ровно теми файлами, что изменила команда (чужие не трогаем).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from core.logging_setup import get_logger
from core.utils.proc import no_window_kwargs

logger = get_logger("shadow_git")

#: Что не тащим в теневой снимок: тяжёлое, генерируемое и чужой .git.
_EXCLUDES = [
    ".git/",
    ".agent/",
    "node_modules/",
    ".venv/",
    "venv/",
    "__pycache__/",
    "*.pyc",
    "dist/",
    "build/",
    ".next/",
    "target/",
    ".gradle/",
    ".idea/",
    "*.log",
]

#: Единая подпись коммитов теневого репозитория (реальный git пользователя не при чём).
_IDENTITY = [
    "-c", "user.name=agent",
    "-c", "user.email=agent@local",
    "-c", "commit.gpgsign=false",
]


class ShadowGit:
    """Снимки рабочей папки в отдельном git-репозитории (для отката команд)."""

    def __init__(self, session_id: str, workspace: Path, base_dir: Path) -> None:
        safe = "".join(c for c in session_id if c.isalnum() or c in ("-", "_")) or "local"
        self.dir = (base_dir / "shadow_git" / safe).resolve()
        self.workspace = Path(workspace).resolve()

    # ------------------------------------------------------------------

    def available(self) -> bool:
        """Есть ли git и существует ли рабочая папка."""
        if not self.workspace.is_dir():
            return False
        try:
            subprocess.run(
                ["git", "--version"], capture_output=True, timeout=10, check=True, **no_window_kwargs()
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return True

    def snapshot(self) -> str | None:
        """Фиксирует текущее состояние рабочей папки, возвращает sha коммита."""
        try:
            self._ensure_repo()
            self._run(["add", "-A"])
            self._run([*_IDENTITY, "commit", "--allow-empty", "-m", "snap", "--no-verify"])
            done = self._run(["rev-parse", "HEAD"])
            return done.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            logger.debug("Теневой снимок не удался", exc_info=True)
            return None

    def changed_since(self, sha: str) -> list[tuple[str, str]]:
        """Список (статус, путь) между `sha` и текущим HEAD: A/M/D."""
        try:
            done = self._run(["diff", "--name-status", "-z", sha, "HEAD"])
        except (OSError, subprocess.SubprocessError):
            return []
        return _parse_name_status(done.stdout)

    def show(self, sha: str, rel_path: str) -> bytes | None:
        """Содержимое файла на момент коммита `sha` (или None, если его там нет)."""
        try:
            done = subprocess.run(
                ["git", "show", f"{sha}:{rel_path}"],
                cwd=str(self.workspace),
                env=self._env(),
                capture_output=True,
                timeout=60,
                **no_window_kwargs(),
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout if done.returncode == 0 else None

    # ------------------------------------------------------------------

    def _ensure_repo(self) -> None:
        if not (self.dir / "HEAD").exists():
            self.dir.mkdir(parents=True, exist_ok=True)
            self._run(["init"])
        # info/exclude отсекает тяжёлое/чужой .git из снимков.
        exclude = self.dir / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text("\n".join(_EXCLUDES) + "\n", encoding="utf-8")

    def _env(self) -> dict[str, str]:
        return {**os.environ, "GIT_DIR": str(self.dir), "GIT_WORK_TREE": str(self.workspace)}

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=str(self.workspace),
            env=self._env(),
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
            **no_window_kwargs(),
        )


def _parse_name_status(raw: str) -> list[tuple[str, str]]:
    """Разбирает `git diff --name-status -z`: NUL-разделённые статус, путь, статус…"""
    tokens = [t for t in raw.split("\0") if t]
    pairs: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        status = tokens[i][:1]
        if status in ("A", "M", "D") and i + 1 < len(tokens):
            pairs.append((status, tokens[i + 1]))
            i += 2
        else:
            i += 1
    return pairs
