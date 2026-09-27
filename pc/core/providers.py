"""Model providers and per-model API keys, kept by the backend.

The settings screen lets the user add providers (endpoint + key) and models under them, each
model optionally with its own key. That list used to live only in the app window's browser
storage: the key reached the backend only at the moment a model was picked, routing tiers kept
a stale copy, and a different window origin (another port, the dev build vs the release build)
simply had no list at all. Now the window mirrors the list here (``providers.json`` in the app
data folder) and every LLM client resolves its credentials from it when it is created.

Precedence for a model: the model's own key > its provider's key > a per-call key (routing
tiers keep a copy that goes stale) > the global key from the settings.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from core.fs_atomic import atomic_write_text
from core.logging_setup import get_logger
from core.settings import Settings, get_settings

logger = get_logger("providers")

_LOCK = threading.Lock()


def _path(settings: Settings) -> Path:
    return Path(settings.app_dir) / "providers.json"


def load(settings: Settings | None = None) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    try:
        data = json.loads(_path(settings).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [p for p in data if isinstance(p, dict)] if isinstance(data, list) else []


def save(providers: list[dict[str, Any]], settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    clean = []
    for p in providers:
        if not isinstance(p, dict):
            continue
        models = [m for m in (p.get("models") or []) if isinstance(m, dict) and str(m.get("id") or "").strip()]
        clean.append({**p, "models": models})
    atomic_write_text(_path(settings), json.dumps(clean, ensure_ascii=False, indent=2))


def _norm_url(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


def credentials_for(model: str | None, base_url: str | None = None,
                    settings: Settings | None = None) -> dict[str, str]:
    """{"base_url", "api_key"} for `model` from the providers list ({} when it is not there).

    When the same model id sits under several providers, the one at `base_url` (or at the
    settings' endpoint) wins, then the first that has a key.
    """
    settings = settings or get_settings()
    model = (model or "").strip()
    if not model:
        return {}
    matches: list[tuple[dict, dict]] = [
        (p, m) for p in load(settings) for m in (p.get("models") or []) if str(m.get("id") or "").strip() == model
    ]
    if not matches:
        return {}
    wanted = _norm_url(base_url or settings.llm_base_url)
    matches.sort(key=lambda pm: (_norm_url(pm[0].get("base_url", "")) != wanted,
                                 not (pm[1].get("api_key") or pm[0].get("api_key"))))
    provider, entry = matches[0]
    out: dict[str, str] = {}
    if provider.get("base_url"):
        out["base_url"] = str(provider["base_url"]).strip()
    key = str(entry.get("api_key") or provider.get("api_key") or "").strip()
    if key:
        out["api_key"] = key
    return out
