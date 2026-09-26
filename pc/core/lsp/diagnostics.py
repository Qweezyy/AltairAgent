"""Проверка типов и семантики через pyright.

Зачем отдельно от `run_lint` (ruff): линтер ловит стиль и очевидные ошибки, но НЕ
проверяет типы. pyright — полноценный статический анализатор Python: несовпадение
типов, неопределённые имена, недостижимый код, неверные аргументы. Это и есть
«ошибки компиляции до запуска тестов» из плана.

pyright вызывается ВНУТРИ процесса через свой Python-API (`pyright.run`), а не как
`python -m pyright`: так работает и из исходников, и из собранного exe, где своего
интерпретатора для `-m` нет. Node pyright управляет сам (качает при первом
запуске в кэш пользователя).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

from core.logging_setup import get_logger
from core.settings import Settings, get_settings

logger = get_logger("lsp.diagnostics")

#: Первый запуск pyright скачивает node — даём щедрый таймаут; дальше ~2–4 с.
_TIMEOUT = 240.0


class PyrightUnavailable(RuntimeError):
    """pyright не установлен или не смог запуститься (нет node/сети)."""


@dataclass
class Diagnostic:
    rel_path: str
    line: int  # 1-based
    col: int  # 1-based
    severity: str  # error | warning | information
    message: str
    rule: str = ""


@dataclass
class TypeCheckResult:
    diagnostics: list[Diagnostic] = field(default_factory=list)
    files_analyzed: int = 0
    error_count: int = 0
    warning_count: int = 0

    @property
    def ok(self) -> bool:
        return self.error_count == 0


def _run_pyright_sync(target: str, cwd: str) -> str:
    """Синхронный вызов pyright, возвращает stdout (JSON). Бросает при отказе."""
    try:
        import pyright
    except ImportError as exc:  # pragma: no cover - зависит от окружения
        raise PyrightUnavailable(
            "pyright не установлен. Поставьте: pip install pyright"
        ) from exc
    try:
        proc = pyright.run(
            "--outputjson",
            target,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 - таймаут, отказ node, отсутствие сети
        raise PyrightUnavailable(f"pyright не запустился: {exc}") from exc
    return proc.stdout or ""


def _parse(stdout: str, workspace: Path) -> TypeCheckResult:
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        # pyright напечатал не-JSON — обычно текст об ошибке инициализации.
        raise PyrightUnavailable(
            f"pyright вернул неожиданный вывод: {stdout[:200] or exc}"
        ) from exc

    result = TypeCheckResult()
    summary = data.get("summary", {})
    result.files_analyzed = int(summary.get("filesAnalyzed", 0))
    result.error_count = int(summary.get("errorCount", 0))
    result.warning_count = int(summary.get("warningCount", 0))

    for item in data.get("generalDiagnostics", []):
        rng = item.get("range", {}).get("start", {})
        raw_file = item.get("file", "?")
        try:
            rel = str(Path(raw_file).relative_to(workspace))
        except ValueError:
            rel = raw_file  # вне workspace (например, стаб библиотеки)
        result.diagnostics.append(
            Diagnostic(
                rel_path=rel.replace("\\", "/"),
                line=int(rng.get("line", 0)) + 1,
                col=int(rng.get("character", 0)) + 1,
                severity=str(item.get("severity", "error")),
                # Сообщения pyright бывают многострочными — берём суть первой строкой,
                # остальное схлопываем в скобки, чтобы вывод оставался плотным.
                message=_flatten(str(item.get("message", ""))),
                rule=str(item.get("rule", "")),
            )
        )
    return result


def _flatten(message: str) -> str:
    # pyright отбивает вложенные пояснения неразрывными пробелами (U+00A0) —
    # заменяем их на обычные, иначе в выводе остаётся мусор вида «В В».
    normalized = message.replace(" ", " ")
    lines = [ln.strip() for ln in normalized.splitlines() if ln.strip()]
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0]
    return f"{lines[0]} ({'; '.join(lines[1:])})"


async def type_check(
    target: Path, workspace: Path, *, settings: Settings | None = None
) -> TypeCheckResult:
    """Проверяет типы в файле или папке. Бросает PyrightUnavailable, если движка нет.

    pyright запускается с cwd=workspace, поэтому видит конфиг проекта и правильно
    резолвит импорты своих же модулей.
    """
    settings = settings or get_settings()
    stdout = await asyncio.to_thread(_run_pyright_sync, str(target), str(workspace))
    return _parse(stdout, workspace)
