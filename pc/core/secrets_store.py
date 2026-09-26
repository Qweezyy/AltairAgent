"""Безопасное хранилище секретов проекта в `.env` рабочей папки.

Идея: пользователь вводит ключ/пароль ОДИН раз через интерфейс, значение
записывается в `.env` рабочей папки и больше нигде не всплывает — ни в чате, ни
в контексте модели. Агент обращается к секрету по ИМЕНИ (`os.environ['NAME']`),
но самого значения не видит. Так секрет доступен собранному приложению при
запуске, но не утекает в переписку с ИИ.

`.env` автоматически добавляется в `.gitignore`, чтобы секрет не уехал в git.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Имя секрета — как переменная окружения: буквы, цифры, подчёркивания.
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SecretError(ValueError):
    """Некорректное имя секрета или сбой записи."""


@dataclass
class SecretInfo:
    name: str
    masked: str  # значение показываем только замаскированным


def _env_path(workspace: Path) -> Path:
    return workspace / ".env"


def validate_name(name: str) -> str:
    name = name.strip()
    if not _NAME_RE.match(name):
        raise SecretError(
            "Имя секрета должно быть как переменная окружения: латиница, цифры, "
            "подчёркивание, не с цифры (например OPENAI_API_KEY)."
        )
    return name


def _parse_env(text: str) -> list[tuple[str, str, str]]:
    """Разбирает .env в список (имя, значение, исходная_строка).

    Строки-комментарии и пустые сохраняем как (None-эквивалент) через пустое имя,
    чтобы при перезаписи не терять форматирование файла.
    """
    rows: list[tuple[str, str, str]] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            rows.append(("", "", raw))
            continue
        key, _, value = stripped.partition("=")
        rows.append((key.strip(), _unquote(value.strip()), raw))
    return rows


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        inner = value[1:-1]
        if value[0] == '"':
            # Обратно к _quote: снимаем экранирование кавычек и слэшей.
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    return value


def _quote(value: str) -> str:
    # Кавычим, если есть пробелы, кавычки или спецсимволы — иначе значение
    # прочитается обрезанным.
    if value == "" or re.search(r"[\s\"'#=]", value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def mask(value: str) -> str:
    value = value.strip()
    if not value:
        return "(пусто)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}…{value[-2:]}"


def set_secret(workspace: Path, name: str, value: str) -> None:
    """Записывает/обновляет секрет в `.env`. Значение никуда не логируется."""
    name = validate_name(name)
    if value == "":
        raise SecretError("Пустое значение секрета.")

    env = _env_path(workspace)
    env.parent.mkdir(parents=True, exist_ok=True)
    text = env.read_text(encoding="utf-8") if env.exists() else ""
    rows = _parse_env(text)

    line = f"{name}={_quote(value)}"
    replaced = False
    out: list[str] = []
    for key, _val, raw in rows:
        if key == name:
            out.append(line)
            replaced = True
        else:
            out.append(raw)
    if not replaced:
        if out and out[-1].strip():
            out.append(line)
        else:
            out.append(line)
    env.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
    _ensure_gitignored(workspace)


def list_secrets(workspace: Path) -> list[SecretInfo]:
    """Имена секретов и замаскированные значения. Полные значения не возвращаются."""
    env = _env_path(workspace)
    if not env.exists():
        return []
    infos: list[SecretInfo] = []
    for key, value, _raw in _parse_env(env.read_text(encoding="utf-8")):
        if key:
            infos.append(SecretInfo(name=key, masked=mask(value)))
    return infos


def delete_secret(workspace: Path, name: str) -> bool:
    env = _env_path(workspace)
    if not env.exists():
        return False
    rows = _parse_env(env.read_text(encoding="utf-8"))
    kept = [raw for key, _val, raw in rows if key != name]
    if len(kept) == len(rows):
        return False
    env.write_text("\n".join(kept).rstrip("\n") + "\n", encoding="utf-8")
    return True


def has_secret(workspace: Path, name: str) -> bool:
    return any(s.name == name for s in list_secrets(workspace))


def load_env(workspace: Path) -> dict[str, str]:
    """Читает секреты из `.env` рабочей папки для передачи в окружение процесса.

    Так запускаемый агентом код (тесты, dev-сервер, скрипты) видит секреты по их
    именам, а сам агент значений не получает.
    """
    env = _env_path(workspace)
    if not env.exists():
        return {}
    result: dict[str, str] = {}
    try:
        for key, value, _raw in _parse_env(env.read_text(encoding="utf-8")):
            if key:
                result[key] = value
    except OSError:
        return {}
    return result


def _ensure_gitignored(workspace: Path) -> None:
    """Добавляет `.env` в `.gitignore`, чтобы секреты не попали в git."""
    gitignore = workspace / ".gitignore"
    try:
        existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
        lines = {ln.strip() for ln in existing.splitlines()}
        if ".env" in lines or "*.env" in lines:
            return
        addition = ("" if existing.endswith("\n") or not existing else "\n") + ".env\n"
        gitignore.write_text(existing + addition, encoding="utf-8")
    except OSError:
        pass  # не удалось — не критично, секрет всё равно записан
