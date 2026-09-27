"""Чтение и запись пользовательских настроек в .env.

Зачем: заставлять человека искать текстовый файл и править его руками — плохой
первый запуск. Настройки должны меняться в самом приложении, но при этом
оставаться обычным .env, который можно открыть блокнотом.

Правки точечные: файл переписывается с сохранением комментариев, порядка строк
и незнакомых ключей. Полная перезапись затирала бы то, что пользователь
настроил руками.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from core.fs_atomic import atomic_write_text, path_lock
from core.logging_setup import get_logger
from core.settings import Settings, get_settings, reload_settings

logger = get_logger("config")

#: Что разрешено менять из интерфейса. Остальное — только правкой файла:
#: лимиты и пути слишком легко испортить случайным кликом.
EDITABLE_KEYS = {
    "LLM_API_KEY": "llm_api_key",
    "LLM_BASE_URL": "llm_base_url",
    "DEFAULT_MODEL": "default_model",
    "MODEL_ROUTING": "model_routing",
    "MODEL_FAST": "model_fast",
    "MODEL_STRONG": "model_strong",
    "MODEL_ROUTER": "model_router",
    "MODEL_TIERS": "model_tiers",
    "AGENT_LANGUAGE": "agent_language",
    "APPROVAL_MODE": "approval_mode",
    "UPDATE_URL": "update_url",
    "MAX_STEPS": "max_steps",
    "MAX_RUN_TOKENS": "max_run_tokens",
    "ALLOW_SUBAGENTS": "allow_subagents",
    "TAVILY_API_KEY": "tavily_api_key",
    "BRAVE_API_KEY": "brave_api_key",
    "SEARXNG_URL": "searxng_url",
    "CUSTOM_INSTRUCTIONS": "custom_instructions",
    "LLM_TEMPERATURE": "llm_temperature",
    "MAX_PARALLEL_TOOLS": "max_parallel_tools",
    "CONTEXT_TOKEN_BUDGET": "context_token_budget",
    "CONTEXT_COMPACTION": "context_compaction",
    "TOOL_RESULT_CLEARING": "tool_result_clearing",
    "TOOL_SEARCH": "tool_search",
    "VERIFICATION_GATE": "verification_gate",
    "CHAT_TITLES": "chat_titles",
    "BROWSER_SNAPSHOT_DIFF": "browser_snapshot_diff",
    "BRIDGE_TOKEN": "bridge_token",
    "BRIDGE_LAN": "bridge_lan",
    "BROWSER_NETWORK": "browser_network",
}

#: Настройки-переключатели: в .env пишем строго "true"/"false".
BOOL_KEYS = {
    "ALLOW_SUBAGENTS", "CONTEXT_COMPACTION", "TOOL_RESULT_CLEARING", "TOOL_SEARCH",
    "VERIFICATION_GATE", "MODEL_ROUTING", "BRIDGE_LAN", "CHAT_TITLES", "BROWSER_SNAPSHOT_DIFF",
}

#: Ключи, значения которых нельзя показывать целиком.
SECRET_KEYS = {"LLM_API_KEY", "OPENROUTER_API_KEY", "TAVILY_API_KEY", "BRAVE_API_KEY", "BRIDGE_TOKEN"}

_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def config_path(settings: Settings | None = None) -> Path:
    """Файл настроек пользователя (в папке данных приложения)."""
    settings = settings or get_settings()
    return settings.app_dir / ".env"


def mask_secret(value: str) -> str:
    """Показываем только хвост ключа: достаточно, чтобы узнать свой."""
    value = (value or "").strip()
    if not value:
        return ""
    return f"…{value[-4:]}" if len(value) > 8 else "…"


def _public_tiers(raw: str) -> dict:
    """Разбирает model_tiers JSON для интерфейса, ВЫРЕЗАЯ ключи (только model+base_url)."""
    import json as _json

    try:
        data = _json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}
    out: dict[str, dict] = {}
    for tier in ("fast", "strong", "router"):
        t = data.get(tier) or {}
        if isinstance(t, dict) and t.get("model"):
            entry = {"model": t.get("model", ""), "base_url": t.get("base_url", "")}
            # Запасные модели тира — тоже без ключей (только model+base_url).
            fbs = [
                {"model": fb.get("model", ""), "base_url": fb.get("base_url", "")}
                for fb in (t.get("fallbacks") or [])
                if isinstance(fb, dict) and fb.get("model")
            ]
            if fbs:
                entry["fallbacks"] = fbs
            out[tier] = entry
    return out


def read_public_settings(settings: Settings | None = None) -> dict:
    """Текущие настройки для интерфейса. Секреты не отдаём."""
    settings = settings or get_settings()
    return {
        "api_key_set": bool(settings.llm_api_key_effective),
        "api_key_hint": mask_secret(settings.llm_api_key_effective),
        "llm_base_url": settings.llm_base_url,
        "default_model": settings.default_model,
        "model_routing": settings.model_routing,
        "model_fast": settings.model_fast,
        "model_strong": settings.model_strong,
        "model_router": settings.model_router,
        "model_tiers": _public_tiers(settings.model_tiers),
        "agent_language": settings.agent_language,
        "approval_mode": settings.approval_mode,
        "update_url": settings.update_url,
        "max_steps": settings.max_steps,
        "max_run_tokens": settings.max_run_tokens,
        "allow_subagents": settings.allow_subagents,
        "custom_instructions": settings.custom_instructions,
        "llm_temperature": settings.llm_temperature,
        "max_parallel_tools": settings.max_parallel_tools,
        "context_token_budget": settings.context_token_budget,
        "context_compaction": settings.context_compaction,
        "tool_result_clearing": settings.tool_result_clearing,
        "tool_search": settings.tool_search,
        "verification_gate": settings.verification_gate,
        "chat_titles": settings.chat_titles,
        "searxng_url": settings.searxng_url,
        "tavily_key_set": bool(settings.tavily_api_key),
        "tavily_key_hint": mask_secret(settings.tavily_api_key),
        "brave_key_set": bool(settings.brave_api_key),
        "brave_key_hint": mask_secret(settings.brave_api_key),
        "config_path": str(config_path(settings)),
        "app_dir": str(settings.app_dir),
        "bridge_token_set": bool(settings.bridge_token),
        "bridge_lan": settings.bridge_lan,
        "browser_network": settings.browser_network,
    }


def write_values(values: dict[str, str], settings: Settings | None = None) -> Path:
    """Записывает значения в .env, сохраняя остальное содержимое файла."""
    settings = settings or get_settings()
    path = config_path(settings)
    # Read-modify-write under the file's lock: two saves at once must not drop each other's keys.
    with path_lock(path):
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        remaining = dict(values)

        for index, line in enumerate(lines):
            match = _LINE_RE.match(line)
            if not match:
                continue
            key = match.group(1)
            if key in remaining:
                lines[index] = f"{key}={remaining.pop(key)}"

        if remaining:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append("# Changed from the app")
            lines.extend(f"{key}={value}" for key, value in remaining.items())

        atomic_write_text(path, "\n".join(lines) + "\n")

    _restrict_access(path)
    logger.info("Настройки сохранены: %s", ", ".join(sorted(values)))
    return path


def _restrict_access(path: Path) -> None:
    """Ограничивает доступ к файлу: в нём лежит ключ API."""
    try:
        if os.name == "nt":
            # На Windows наследуемые права снимает icacls; тихо пропускаем,
            # если команда недоступна — файл всё равно лежит в профиле.
            import subprocess

            subprocess.run(  # noqa: S603
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{os.getlogin()}:F"],
                capture_output=True,
                timeout=10,
            )
        else:
            path.chmod(0o600)
    except Exception:  # noqa: BLE001 - права не критичны для работы
        logger.debug("Не удалось ограничить доступ к %s", path, exc_info=True)


def apply_settings(payload: dict) -> tuple[Settings, list[str]]:
    """Проверяет и сохраняет настройки из интерфейса.

    Возвращает обновлённые настройки и список предупреждений.
    """
    values: dict[str, str] = {}
    warnings: list[str] = []

    for env_key, field in EDITABLE_KEYS.items():
        if field not in payload:
            continue
        raw = payload[field]
        if raw is None:
            continue
        # model_tiers — JSON-блоб: принимаем и объект, и строку; пишем компактной
        # одной строкой (без пробелов/переносов), чтобы .env остался валидным.
        if env_key == "MODEL_TIERS":
            import json as _json

            if not isinstance(raw, str):
                values[env_key] = _json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
            else:
                values[env_key] = raw.strip()
            continue
        # Переключатели пишем нормализованно, чтобы pydantic их корректно читал.
        if env_key in BOOL_KEYS:
            truthy = raw is True or str(raw).strip().lower() in ("1", "true", "yes", "on")
            values[env_key] = "true" if truthy else "false"
            continue
        value = str(raw).strip()

        # Пустой ключ означает «не трогать»: иначе замаскированное значение
        # из интерфейса затёрло бы настоящий ключ.
        if env_key in SECRET_KEYS and (not value or value.startswith("…")):
            continue
        if env_key == "MAX_STEPS" and not value.isdigit():
            warnings.append("Лимит шагов должен быть числом — значение не изменено.")
            continue
        if env_key == "MAX_RUN_TOKENS" and value and not value.isdigit():
            warnings.append("Лимит токенов должен быть числом (0 — без лимита) — не изменён.")
            continue
        if env_key in ("MAX_PARALLEL_TOOLS", "CONTEXT_TOKEN_BUDGET") and value and not value.isdigit():
            warnings.append(f"{env_key}: нужно целое число — не изменено.")
            continue
        if env_key == "LLM_TEMPERATURE" and value:
            try:
                t = float(value)
                if not 0.0 <= t <= 2.0:
                    raise ValueError
            except ValueError:
                warnings.append("Температура должна быть числом от 0 до 2 — не изменена.")
                continue
        if env_key in ("LLM_BASE_URL", "SEARXNG_URL") and value and not value.startswith(("http://", "https://")):
            warnings.append(f"{env_key}: адрес должен начинаться с http:// или https:// — не изменён.")
            continue

        values[env_key] = value

    if values:
        write_values(values)

    settings = reload_settings()
    return settings, warnings
