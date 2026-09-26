"""Профили-пресеты композера."""

from __future__ import annotations

from core.presets import Preset, PresetStore


def test_defaults_are_seeded_on_first_run(settings):
    store = PresetStore(settings.data_dir)
    names = {p.name for p in store.all()}
    assert {"Кодинг", "Учёба", "Быт"} <= names
    assert all(p.builtin for p in store.all())


def test_defaults_persist_to_disk(settings):
    PresetStore(settings.data_dir)  # создаёт файл с дефолтами
    assert (settings.data_dir / "presets.json").exists()
    # Второй экземпляр читает их с диска, а не пересоздаёт.
    again = PresetStore(settings.data_dir)
    assert len(again.all()) == 3


def test_save_creates_and_updates_by_name(settings):
    store = PresetStore(settings.data_dir)
    store.save(Preset(name="Мой", model="x/y", approval_mode="bypass", web_mode="off"))
    assert any(p.name == "Мой" and p.model == "x/y" for p in store.all())

    # Повторное сохранение с тем же именем — обновление, не дубль.
    store.save(Preset(name="Мой", model="a/b"))
    mine = [p for p in store.all() if p.name == "Мой"]
    assert len(mine) == 1
    assert mine[0].model == "a/b"
    assert mine[0].builtin is False


def test_invalid_values_are_sanitized(settings):
    store = PresetStore(settings.data_dir)
    saved = store.save(Preset(name="  Кривой  ", approval_mode="чепуха", web_mode="ерунда"))
    assert saved.name == "Кривой"
    assert saved.approval_mode == "manual"  # неизвестный режим -> дефолт
    assert saved.web_mode == "auto"


def test_delete_removes_preset(settings):
    store = PresetStore(settings.data_dir)
    store.save(Preset(name="Временный"))
    assert store.delete("Временный") is True
    assert store.delete("нет-такого") is False
    assert all(p.name != "Временный" for p in store.all())


def test_broken_file_falls_back_to_defaults(settings):
    (settings.data_dir).mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "presets.json").write_text("{ битый json", encoding="utf-8")
    store = PresetStore(settings.data_dir)
    assert len(store.all()) == 3  # вернулись дефолты, без падения
