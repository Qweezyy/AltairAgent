"""Определение, чем в проекте запускаются тесты и линтеры.

Агент не должен угадывать команду и тратить шаги на «а вдруг сработает».
Здесь один раз описано, как выглядит проект каждого типа, и что для него
запускать.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from core.utils.proc import python_executable


@dataclass(slots=True)
class Command:
    """Готовая к запуску проверка."""

    kind: str  # tests | lint | typecheck
    tool: str  # pytest, npm, ruff, eslint, ...
    argv: list[str]
    description: str

    @property
    def display(self) -> str:
        return " ".join(self.argv)


def _has_any(base: Path, *names: str) -> bool:
    return any((base / name).exists() for name in names)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}


def _pyproject_text(base: Path) -> str:
    path = base / "pyproject.toml"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _has_python_sources(base: Path) -> bool:
    try:
        return any(base.glob("*.py")) or any(base.glob("*/*.py"))
    except OSError:  # pragma: no cover
        return False


def _has_test_files(base: Path) -> bool:
    """Есть ли где-нибудь неглубоко файлы вида test_*.py / *_test.py."""
    patterns = ("test_*.py", "*_test.py", "tests/test_*.py", "*/test_*.py")
    try:
        return any(next(base.glob(pattern), None) for pattern in patterns)
    except OSError:  # pragma: no cover
        return False


def detect_test_commands(base: Path) -> list[Command]:
    """Чем запускать тесты в этом проекте (в порядке предпочтения)."""
    commands: list[Command] = []
    pyproject = _pyproject_text(base)

    # Проект считаем питоновским не только по pyproject.toml: очень часто это
    # просто папка со скриптами и tests/ — раньше на таких определение падало.
    python_project = (
        bool(pyproject)
        or _has_any(base, "setup.py", "requirements.txt", "setup.cfg")
        or _has_python_sources(base)
    )
    has_tests_dir = _has_any(base, "tests", "test")
    has_test_files = _has_test_files(base)
    pytest_configured = "[tool.pytest" in pyproject or _has_any(base, "pytest.ini", "tox.ini", "conftest.py")

    if pytest_configured or (python_project and (has_tests_dir or has_test_files)):
        commands.append(
            Command(
                kind="tests",
                tool="pytest",
                argv=[python_executable(base), "-m", "pytest", "-q", "--no-header", "-x", "--tb=short"],
                description="pytest (первая упавшая проверка останавливает прогон)",
            )
        )
    elif python_project and has_tests_dir:
        commands.append(
            Command(
                kind="tests",
                tool="unittest",
                argv=[python_executable(base), "-m", "unittest", "discover", "-v"],
                description="unittest discover",
            )
        )

    package_json = base / "package.json"
    if package_json.exists():
        scripts = _read_json(package_json).get("scripts", {})
        if "test" in scripts:
            npm = shutil.which("npm") or "npm"
            commands.append(
                Command(kind="tests", tool="npm", argv=[npm, "test"], description="npm test")
            )

    if (base / "go.mod").exists():
        commands.append(
            Command(kind="tests", tool="go", argv=["go", "test", "./..."], description="go test")
        )

    if (base / "Cargo.toml").exists():
        commands.append(
            Command(kind="tests", tool="cargo", argv=["cargo", "test"], description="cargo test")
        )

    return commands


def detect_lint_commands(base: Path) -> list[Command]:
    """Чем проверять стиль и типы."""
    commands: list[Command] = []
    pyproject = _pyproject_text(base)

    python_project = bool(pyproject) or _has_any(base, "setup.py", "requirements.txt")
    if python_project or _has_any(base, "ruff.toml", ".ruff.toml"):
        commands.append(
            Command(
                kind="lint",
                tool="ruff",
                argv=[python_executable(base), "-m", "ruff", "check", "."],
                description="ruff check",
            )
        )
    if "[tool.mypy]" in pyproject or _has_any(base, "mypy.ini", ".mypy.ini"):
        commands.append(
            Command(
                kind="typecheck",
                tool="mypy",
                argv=[python_executable(base), "-m", "mypy", "."],
                description="mypy",
            )
        )

    if _has_any(
        base,
        "eslint.config.js",
        "eslint.config.mjs",
        ".eslintrc",
        ".eslintrc.json",
        ".eslintrc.js",
        ".eslintrc.cjs",
    ):
        npx = shutil.which("npx") or "npx"
        commands.append(
            Command(kind="lint", tool="eslint", argv=[npx, "eslint", "."], description="eslint")
        )

    if (base / "tsconfig.json").exists():
        npx = shutil.which("npx") or "npx"
        commands.append(
            Command(
                kind="typecheck",
                tool="tsc",
                argv=[npx, "tsc", "--noEmit"],
                description="tsc --noEmit",
            )
        )

    if (base / "go.mod").exists():
        commands.append(
            Command(kind="lint", tool="go", argv=["go", "vet", "./..."], description="go vet")
        )

    return commands


def describe_project(base: Path) -> str:
    """Короткое описание того, что нашли — попадает в подсказку модели."""
    tests = detect_test_commands(base)
    lints = detect_lint_commands(base)
    parts = []
    if tests:
        parts.append("тесты: " + ", ".join(c.description for c in tests))
    if lints:
        parts.append("проверки: " + ", ".join(c.description for c in lints))
    return "; ".join(parts) or "инструменты проверки не обнаружены"
