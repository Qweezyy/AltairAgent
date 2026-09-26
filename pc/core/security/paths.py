"""Песочница файловой системы.

ЛЮБОЙ инструмент, который трогает диск, обязан прогнать путь через
`resolve_path()`. Это единственная защита от `../../Windows/System32`
и от того, что модель случайно снесёт что-то за пределами проекта.
"""

from __future__ import annotations

import os
from pathlib import Path

from core.errors import PathNotAllowed
from core.settings import Settings, get_settings


def _norm(p: Path) -> str:
    """Нормализованная строка пути для сравнения (Windows: регистронезависимо)."""
    return os.path.normcase(str(p))


def _is_within(child: Path, parent: Path) -> bool:
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def resolve_path(
    path: str | os.PathLike[str],
    *,
    settings: Settings | None = None,
    must_exist: bool = False,
    must_be_dir: bool = False,
    must_be_file: bool = False,
) -> Path:
    """Превращает пользовательский путь в безопасный абсолютный Path.

    Относительные пути считаются от WORKSPACE. Абсолютные проверяются на
    принадлежность разрешённым корням.

    Raises:
        PathNotAllowed: путь вне песочницы или не проходит проверки типа.
    """
    settings = settings or get_settings()
    raw = str(path).strip().strip('"').strip("'")

    candidate = Path(os.path.expandvars(raw)).expanduser()
    if not candidate.is_absolute():
        candidate = settings.workspace / candidate

    try:
        resolved = candidate.resolve()
    except OSError as exc:  # битые симлинки, слишком длинные пути и т.п.
        raise PathNotAllowed(f"Некорректный путь '{raw}': {exc}") from exc

    if not settings.allow_outside_workspace:
        roots = settings.allowed_roots
        if not any(_is_within(resolved, root) for root in roots):
            allowed = ", ".join(str(r) for r in roots)
            raise PathNotAllowed(
                f"Доступ к '{resolved}' запрещён. Разрешённые директории: {allowed}. "
                "Работайте внутри рабочей папки или попросите пользователя изменить "
                "EXTRA_ALLOWED_ROOTS в .env."
            )

    if must_exist and not resolved.exists():
        raise PathNotAllowed(f"Путь не существует: {resolved}")
    if must_be_dir and resolved.exists() and not resolved.is_dir():
        raise PathNotAllowed(f"Ожидалась директория, а это файл: {resolved}")
    if must_be_file and resolved.exists() and not resolved.is_file():
        raise PathNotAllowed(f"Ожидался файл, а это директория: {resolved}")

    return resolved


def safe_relpath(path: Path, settings: Settings | None = None) -> str:
    """Красивый относительный путь для вывода модели (не для доступа к ФС)."""
    settings = settings or get_settings()
    try:
        return str(path.relative_to(settings.workspace))
    except ValueError:
        return str(path)


def describe_roots(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if settings.allow_outside_workspace:
        return "the whole disk (ALLOW_OUTSIDE_WORKSPACE=true)"
    return ", ".join(str(r) for r in settings.allowed_roots)
