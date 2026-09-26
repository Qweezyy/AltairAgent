"""Поиск ошибок сборки и рантайма в потоке логов dev-сервера.

Цель — не разобрать ошибку до конца (это сделает модель), а быстро дать сигнал
«в логах есть проблема» и вытащить relevantные строки, чтобы агент не перечитывал
весь буфер. Паттерны намеренно широкие и покрывают частые стеки: Vite/Webpack,
TypeScript, ESLint, Python-трейсбеки, uvicorn/ASGI, Node и общие «error/failed».
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: (регэксп, ярлык источника). Порядок важен: специфичные — раньше общих.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bTraceback \(most recent call last\)"), "python"),
    (re.compile(r"^\s*File \".+\", line \d+", re.MULTILINE), "python"),
    (re.compile(r"\b\w*Error\b:"), "python/js"),
    (re.compile(r"\bTS\d{3,5}\b"), "typescript"),
    (re.compile(r"\berror TS\d+"), "typescript"),
    (re.compile(r"\[vite\].*error", re.IGNORECASE), "vite"),
    (re.compile(r"Failed to compile", re.IGNORECASE), "webpack"),
    (re.compile(r"Module not found", re.IGNORECASE), "bundler"),
    (re.compile(r"\bERR!"), "npm"),
    (re.compile(r"\bERROR in\b"), "webpack"),
    (re.compile(r"SyntaxError", re.IGNORECASE), "syntax"),
    (re.compile(r"Cannot find module", re.IGNORECASE), "node"),
    (re.compile(r"\bunhandled\b.*\b(exception|rejection)\b", re.IGNORECASE), "runtime"),
    (re.compile(r"\b(build|compilation) failed\b", re.IGNORECASE), "build"),
    (re.compile(r"panic:", re.IGNORECASE), "go/rust"),
]

#: Признаки того, что сервер, наоборот, успешно поднялся/пересобрался. Помогают
#: агенту понять, что после его правки всё стало хорошо.
_READY_PATTERNS = [
    re.compile(r"compiled successfully", re.IGNORECASE),
    re.compile(r"ready in \d+", re.IGNORECASE),
    re.compile(r"\bhmr update\b", re.IGNORECASE),
    re.compile(r"Local:\s+https?://", re.IGNORECASE),
    re.compile(r"Application startup complete", re.IGNORECASE),
    re.compile(r"watching for file changes", re.IGNORECASE),
    re.compile(r"webpack compiled", re.IGNORECASE),
]


@dataclass
class DetectedError:
    source: str
    line_index: int
    text: str


def scan_output(lines: list[str]) -> list[DetectedError]:
    """Возвращает строки, похожие на ошибки, с ярлыком источника."""
    found: list[DetectedError] = []
    for index, line in enumerate(lines):
        for pattern, source in _PATTERNS:
            if pattern.search(line):
                found.append(DetectedError(source=source, line_index=index, text=line.strip()))
                break
    return found


def looks_ready(lines: list[str]) -> bool:
    """Есть ли в строках признак успешного старта/пересборки."""
    return any(pattern.search(line) for line in lines for pattern in _READY_PATTERNS)
