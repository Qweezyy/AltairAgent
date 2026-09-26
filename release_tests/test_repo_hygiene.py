"""Гигиена репозитория перед публикацией — без импорта кода приложения.

Ловит то, что нельзя выпускать наружу: секреты в истории, утечку внутренних
планов, тестовые артефакты, отсутствие обязательных публичных файлов.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def test_required_public_files_exist():
    for name in ("README.md", "LICENSE", "VERSION", "CHANGELOG.md"):
        assert (REPO_ROOT / name).is_file(), f"нет обязательного публичного файла: {name}"


def test_version_is_semver():
    raw = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", raw), f"VERSION не SemVer: {raw!r}"


def test_no_env_file_tracked():
    bad = [f for f in _tracked_files() if Path(f).name == ".env"]
    assert not bad, f".env не должен быть в репозитории: {bad}"


def test_no_secret_files_tracked():
    exts = (".pem", ".key", ".keystore", ".jks", ".p12", ".pfx")
    bad = [f for f in _tracked_files() if f.lower().endswith(exts)]
    assert not bad, f"файлы-секреты в репозитории: {bad}"


def test_no_test_artifacts_tracked():
    bad = [f for f in _tracked_files() if f.lower().endswith((".apkg", ".log"))]
    assert not bad, f"тестовые артефакты в репозитории: {bad}"


def test_internal_docs_not_tracked():
    tracked = _tracked_files()
    markers = ("docs/PLAN_", "docs/RESEARCH_", "docs/ONBOARDING_", "docs/HANDOFF_", "MASTER_PLAN")
    leaked = [f for f in tracked if any(m in f for m in markers)]
    assert not leaked, f"внутренние доки просочились в репозиторий: {leaked}"


def test_no_secret_patterns_in_tracked_text():
    """Скан отслеживаемого текста на живые ключи. Паттерны собраны из кусков,
    чтобы сам этот файл не сработал; release_tests исключён из области поиска."""
    patterns = [
        "sk-" + "ant-" + r"[A-Za-z0-9_-]{20}",
        "AKIA" + r"[0-9A-Z]{16}",
        "ghp_" + r"[A-Za-z0-9]{36}",
        "-----BEGIN " + "[A-Z ]*PRIVATE KEY-----",
    ]
    # Тест-директории исключены: их фикстурам положено содержать фейковые ключи
    # (сканер секретов сам тестируется на плейсхолдерах вроде ...EXAMPLE).
    excludes = [
        ":(exclude)release_tests",
        ":(exclude,glob)**/tests/**",
        ":(exclude,glob)**/test/**",
        ":(exclude,glob)**/androidTest/**",
    ]
    hits: list[str] = []
    for pat in patterns:
        res = subprocess.run(
            ["git", "grep", "-I", "-nE", pat, "--", ".", *excludes],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        # returncode 0 = найдено, 1 = не найдено. Отсеиваем строки-плейсхолдеры.
        if res.returncode == 0 and res.stdout.strip():
            real = [ln for ln in res.stdout.splitlines() if "EXAMPLE" not in ln.upper()]
            if real:
                hits.extend(real)
    assert not hits, "похоже на секреты в отслеживаемых файлах:\n" + "\n".join(hits)
