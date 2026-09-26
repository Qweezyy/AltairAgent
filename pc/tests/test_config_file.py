"""Настройки, которые пользователь меняет прямо в приложении."""

from __future__ import annotations

import core.config_file as config_module
from core.config_file import apply_settings, config_path, mask_secret, read_public_settings, write_values

# ------------------------------------------------------------------ маска


def test_secret_is_never_shown_in_full():
    assert mask_secret("sk-or-v1-abcdef123456") == "…3456"
    assert mask_secret("") == ""
    assert mask_secret("короткий") == "…"


def test_public_settings_hide_the_key(settings):
    settings.llm_api_key = "sk-or-v1-секретный-ключ-1234"
    data = read_public_settings(settings)

    assert data["api_key_set"] is True
    assert data["api_key_hint"] == "…1234"
    assert "секретный" not in str(data), "ключ не должен уезжать в интерфейс целиком"


# ------------------------------------------------------------------ запись


def test_writes_into_app_dir_not_project(settings):
    write_values({"DEFAULT_MODEL": "test/model"}, settings)

    path = config_path(settings)
    assert settings.app_dir in path.parents
    assert "DEFAULT_MODEL=test/model" in path.read_text(encoding="utf-8")


def test_write_survives_non_atomic_replace(settings, monkeypatch):
    """Если os.replace не может атомарно заменить (AppData как junction на другой
    том → WinError 17), запись всё равно проходит через прямую перезапись."""

    import core.config_file as cfg

    def boom(src, dst):
        raise OSError(17, "cannot move to another device")

    monkeypatch.setattr(cfg.os, "replace", boom)
    write_values({"DEFAULT_MODEL": "via/fallback"}, settings)

    path = config_path(settings)
    assert "DEFAULT_MODEL=via/fallback" in path.read_text(encoding="utf-8")
    # временный файл за собой не оставляем
    assert not path.with_name(path.name + ".tmp").exists()
    assert not any(p.name.endswith(".env.tmp") for p in path.parent.iterdir())


def test_existing_comments_and_keys_survive(settings):
    path = config_path(settings)
    path.write_text(
        "# Мой комментарий\nOPENROUTER_API_KEY=старый\nCUSTOM_THING=не трогать\n",
        encoding="utf-8",
    )

    write_values({"DEFAULT_MODEL": "new/model"}, settings)
    text = path.read_text(encoding="utf-8")

    assert "# Мой комментарий" in text
    assert "CUSTOM_THING=не трогать" in text
    assert "OPENROUTER_API_KEY=старый" in text
    assert "DEFAULT_MODEL=new/model" in text


def test_existing_value_is_replaced_not_duplicated(settings):
    path = config_path(settings)
    path.write_text("DEFAULT_MODEL=старая\n", encoding="utf-8")

    write_values({"DEFAULT_MODEL": "новая"}, settings)
    text = path.read_text(encoding="utf-8")

    assert text.count("DEFAULT_MODEL=") == 1
    assert "DEFAULT_MODEL=новая" in text


# ------------------------------------------------------------- применение


def test_apply_saves_and_reloads(settings, monkeypatch):
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(config_module, "reload_settings", lambda: settings)

    _, warnings = apply_settings({"default_model": "openai/gpt-5", "agent_language": "английский"})

    text = config_path(settings).read_text(encoding="utf-8")
    assert "DEFAULT_MODEL=openai/gpt-5" in text
    assert "AGENT_LANGUAGE=английский" in text
    assert warnings == []


def test_masked_key_does_not_overwrite_real_one(settings, monkeypatch):
    """Интерфейс присылает «…1234», если пользователь не менял ключ."""
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(config_module, "reload_settings", lambda: settings)
    config_path(settings).write_text("LLM_API_KEY=настоящий-ключ\n", encoding="utf-8")

    apply_settings({"llm_api_key": "…1234"})

    assert "LLM_API_KEY=настоящий-ключ" in config_path(settings).read_text(encoding="utf-8")


def test_empty_key_keeps_previous_value(settings, monkeypatch):
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(config_module, "reload_settings", lambda: settings)
    config_path(settings).write_text("LLM_API_KEY=прежний\n", encoding="utf-8")

    apply_settings({"llm_api_key": "   "})

    assert "LLM_API_KEY=прежний" in config_path(settings).read_text(encoding="utf-8")


def test_new_key_has_priority_over_legacy_key(settings):
    settings.llm_api_key = "новый-ключ"
    settings.openrouter_api_key = "старый-ключ"

    assert settings.llm_api_key_effective == "новый-ключ"


def test_legacy_key_remains_supported(settings):
    settings.llm_api_key = ""
    settings.openrouter_api_key = "старый-ключ"

    assert settings.llm_api_key_effective == "старый-ключ"


def test_new_key_is_saved_from_settings(settings, monkeypatch):
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(config_module, "reload_settings", lambda: settings)

    apply_settings({"llm_api_key": "новый-ключ"})

    assert "LLM_API_KEY=новый-ключ" in config_path(settings).read_text(encoding="utf-8")


def test_bad_values_are_rejected_with_explanation(settings, monkeypatch):
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(config_module, "reload_settings", lambda: settings)

    _, warnings = apply_settings({"max_steps": "много", "llm_base_url": "ftp://куда-то"})

    assert any("числом" in w for w in warnings)
    assert any("http" in w for w in warnings)
    text = config_path(settings).read_text(encoding="utf-8") if config_path(settings).exists() else ""
    assert "MAX_STEPS=много" not in text


def test_unknown_fields_are_ignored(settings, monkeypatch):
    """Из интерфейса нельзя записать что попало в файл настроек."""
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(config_module, "reload_settings", lambda: settings)

    apply_settings({"app_path": "C:/куда-нибудь", "workspace_path": "C:/тоже"})

    text = config_path(settings).read_text(encoding="utf-8") if config_path(settings).exists() else ""
    assert "APP_PATH" not in text
    assert "WORKSPACE_PATH" not in text
