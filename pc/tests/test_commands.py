"""Быстрые команды (шаблоны промптов)."""

from __future__ import annotations

import pytest

from core.commands import Command, CommandStore


def test_defaults_seeded_and_speak_the_interface_language(settings):
    store = CommandStore(settings.data_dir)
    assert {"tests", "review", "explain", "refactor"} <= {c.name for c in store.all("en")}
    assert {"тесты", "ревью", "объясни", "рефактор"} <= {c.name for c in store.all("ru")}
    assert all(c.builtin and c.key for c in store.all())
    explain_en = next(c for c in store.all("en") if c.key == "explain")
    assert explain_en.description == "Explain simply and in detail"
    assert explain_en.expand("recursion").endswith("with examples: recursion")


def test_a_builtin_answers_to_both_of_its_names(settings):
    store = CommandStore(settings.data_dir)
    assert store.get("тесты").key == store.get("tests").key == "tests"
    assert store.get("tests", "ru").name == "тесты"
    assert store.delete("ревью") and store.get("review") is None


def test_builtins_stored_before_keys_are_recognised(settings):
    import json

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    old = {"commands": [{"name": "тесты", "template": "старый", "description": "", "builtin": True},
                        {"name": "мой", "template": "свой", "description": "", "builtin": False}]}
    (settings.data_dir / "commands.json").write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    store = CommandStore(settings.data_dir)
    names = [c.name for c in store.all("en")]
    assert names == ["tests", "мой"]                   # the user's own command stays as written


def test_expand_with_placeholder():
    cmd = Command(name="объясни", template="Объясни просто: {{ввод}}")
    assert cmd.expand("рекурсию") == "Объясни просто: рекурсию"
    # Пустой аргумент оставляет плейсхолдер пустым.
    assert cmd.expand("") == "Объясни просто: "


def test_expand_without_placeholder_appends_argument():
    cmd = Command(name="тесты", template="Прогони тесты.")
    assert cmd.expand("") == "Прогони тесты."
    assert cmd.expand("только модуль X") == "Прогони тесты.\n\nтолько модуль X"


def test_save_validates_name(settings):
    store = CommandStore(settings.data_dir)
    with pytest.raises(ValueError, match="name"):
        store.save(Command(name="с пробелом", template="x"))
    with pytest.raises(ValueError, match="template"):
        store.save(Command(name="пусто", template="   "))


def test_save_strips_leading_slash_and_updates(settings):
    store = CommandStore(settings.data_dir)
    store.save(Command(name="/мой", template="первый"))
    store.save(Command(name="мой", template="второй"))  # то же имя без слэша
    mine = [c for c in store.all() if c.name == "мой"]
    assert len(mine) == 1
    assert mine[0].template == "второй"
    assert mine[0].builtin is False


def test_cyrillic_name_allowed(settings):
    store = CommandStore(settings.data_dir)
    saved = store.save(Command(name="отчёт", template="Собери отчёт про {{ввод}}"))
    assert saved.name == "отчёт"
    assert store.get("отчёт") is not None


def test_delete(settings):
    store = CommandStore(settings.data_dir)
    store.save(Command(name="временная", template="x"))
    assert store.delete("временная") is True
    assert store.delete("нет") is False


def test_broken_file_falls_back_to_defaults(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "commands.json").write_text("{битый", encoding="utf-8")
    store = CommandStore(settings.data_dir)
    assert len(store.all()) >= 6
