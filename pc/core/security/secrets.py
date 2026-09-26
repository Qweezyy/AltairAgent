"""Поиск утечек секретов в коде: ключи, токены, пароли, приватные ключи.

Задача — поймать секрет ДО того, как он уедет в коммит или наружу. Найденное
всегда МАСКИРУЕТСЯ (показываем пару символов по краям): смысл в том, чтобы
предупредить, а не продублировать сам секрет в выводе.

Чистый Python на регулярках: ноль зависимостей, работает офлайн и на любом
языке (секреты выглядят одинаково в .py, .js, .env, .yaml).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from core.utils.fs import IGNORED_DIRS
from core.utils.text import looks_binary, read_text_file

#: Не сканируем огромные файлы — это почти всегда данные/сборка, не исходники.
_MAX_FILE_BYTES = 1_000_000

#: (имя типа, регэксп). Специфичные форматы — по префиксам известных сервисов.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("приватный ключ", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("ключ вида sk- (OpenAI/OpenRouter/Stripe)", re.compile(r"\bsk-[a-zA-Z0-9_-]{20,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{30,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Stripe live key", re.compile(r"\bsk_live_[0-9a-zA-Z]{20,}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
]

#: Присваивание секрета: password="...", api_key: "...", DATABASE_PASSWORD=... (в
#: .env — без кавычек). Ключевое слово может быть частью большего имени
#: (DB_PASSWORD, API_TOKEN), поэтому по краям допускаем буквы/цифры/подчёркивания.
_ASSIGNMENT = re.compile(
    r"""(?ix)
    \b[\w.-]*(pass(?:word|wd)?|secret|token|api[_-]?key|access[_-]?key|auth[_-]?token|credential)[\w.-]*
    \s*[:=]\s*
    (['"]?)(?P<val>[^\s'"]{6,})\2
    """
)

#: Значение похоже на код, а не на литерал-секрет (вызов функции, выражение).
_CODE_VALUE = re.compile(r"[()\[\]]|^[a-z_][a-z0-9_]*$|\.\w+$")

#: Значения-заглушки: не секреты, флагировать их — шум.
_PLACEHOLDER = re.compile(
    r"(?i)(example|your[_-]?|placeholder|changeme|xxx+|<[^>]+>|\$\{|os\.environ|getenv|dummy|test[_-]?key|redacted|\*\*\*)"
)


@dataclass
class SecretFinding:
    rel_path: str
    line: int
    kind: str
    masked: str


def _mask(value: str) -> str:
    """Оставляет по краям немного символов, середину скрывает. Никогда не целиком."""
    value = value.strip()
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-2:]} (скрыто {len(value)} симв.)"


def _looks_placeholder(value: str) -> bool:
    return (
        bool(_PLACEHOLDER.search(value))
        or bool(_CODE_VALUE.search(value))  # это код/переменная, не литерал
        or len(set(value)) <= 3
    )


def scan_text(text: str, rel_path: str = "") -> list[SecretFinding]:
    """Ищет секреты в тексте. Возвращает находки с замаскированными значениями."""
    findings: list[SecretFinding] = []
    seen: set[tuple[int, str]] = set()
    for lineno, line in enumerate(text.splitlines(), 1):
        for kind, pattern in _PATTERNS:
            match = pattern.search(line)
            if match and (lineno, kind) not in seen:
                seen.add((lineno, kind))
                findings.append(SecretFinding(rel_path, lineno, kind, _mask(match.group(0))))
        assign = _ASSIGNMENT.search(line)
        if assign:
            value = assign.group("val")
            if not _looks_placeholder(value) and (lineno, "присвоение") not in seen:
                seen.add((lineno, "присвоение"))
                findings.append(
                    SecretFinding(rel_path, lineno, f"секрет в коде ({assign.group(1)})", _mask(value))
                )
    return findings


def scan_path(base: Path, workspace: Path, *, limit: int = 500) -> list[SecretFinding]:
    """Рекурсивно сканирует файлы под `base`, пропуская мусор и бинарники."""
    import os

    findings: list[SecretFinding] = []
    targets: list[Path] = [base] if base.is_file() else []
    if base.is_dir():
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".git")]
            for name in files:
                targets.append(Path(root) / name)

    for path in targets:
        try:
            if not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES or looks_binary(path):
                continue
        except OSError:
            continue
        try:
            rel = str(path.resolve().relative_to(workspace.resolve())).replace("\\", "/")
        except ValueError:
            rel = path.name
        findings.extend(scan_text(read_text_file(path), rel))
        if len(findings) >= limit:
            break
    return findings[:limit]
