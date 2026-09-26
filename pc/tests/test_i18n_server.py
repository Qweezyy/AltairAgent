"""Server-side texts follow the language the app reports."""

from __future__ import annotations

from core.i18n import CATALOG, set_ui_language, tr, ui_language


def test_every_entry_has_english_and_russian():
    for key, entry in CATALOG.items():
        assert entry.get("en"), f"{key} has no English text"
        assert entry.get("ru"), f"{key} has no Russian text"


def test_placeholders_match_between_languages():
    import string

    fmt = string.Formatter()
    for key, entry in CATALOG.items():
        en = {f for _, f, _, _ in fmt.parse(entry["en"]) if f}
        ru = {f for _, f, _, _ in fmt.parse(entry["ru"]) if f}
        assert en == ru, f"{key}: {en} != {ru}"


def test_language_switch_and_fallbacks():
    assert ui_language() == "en"
    assert tr("ws.unknown_mode", mode="x") == "Unknown mode: x"
    set_ui_language("ru-RU")
    assert tr("ws.unknown_mode", mode="x") == "Неизвестный режим: x"
    set_ui_language("de")  # unsupported -> English
    assert tr("ws.unknown_mode", mode="x") == "Unknown mode: x"
    assert tr("no.such.key") == "no.such.key"
    assert tr("ws.unknown_mode") == "Unknown mode: {mode}"  # missing param never raises


def test_injection_reason_is_localised():
    set_ui_language("ru")
    assert "инъекц" in tr("appr.injection")
    set_ui_language("en")
    assert "injection" in tr("appr.injection")
