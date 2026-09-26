"""Проверка и установка обновлений."""

from __future__ import annotations

import json
import zipfile

import pytest

from core.updater import Updater
from core.version import __version__, is_newer, version_tuple

# ------------------------------------------------------------- сравнение


@pytest.mark.parametrize(
    ("candidate", "current", "newer"),
    [
        ("1.5.0", "1.4.0", True),
        ("1.4.1", "1.4.0", True),
        ("2.0.0", "1.9.9", True),
        ("1.4.0", "1.4.0", False),
        ("1.3.9", "1.4.0", False),
        ("v1.5.0", "1.4.0", True),      # префикс v из тегов git
        ("1.10.0", "1.9.0", True),      # числа, а не строки
    ],
)
def test_version_comparison(candidate, current, newer):
    assert is_newer(candidate, current) is newer


def test_broken_version_does_not_crash():
    assert version_tuple("не версия") == (0,)
    assert is_newer("абра", "1.0.0") is False


# ---------------------------------------------------------------- проверка


async def test_no_source_explains_how_to_configure(settings):
    settings.update_url = ""
    info = await Updater(settings).check()

    assert not info.available
    assert "UPDATE_URL" in info.error


async def test_manifest_from_local_file(settings, tmp_path):
    """Обновления можно раздавать через общую папку, без интернета."""
    manifest = tmp_path / "update.json"
    manifest.write_text(
        json.dumps({"version": "99.0.0", "url": "pack.zip", "notes": "Много нового"}),
        encoding="utf-8",
    )
    settings.update_url = str(manifest)

    info = await Updater(settings).check()

    assert info.available
    assert info.version == "99.0.0"
    assert info.notes == "Много нового"


async def test_same_version_is_not_an_update(settings, tmp_path):
    manifest = tmp_path / "update.json"
    manifest.write_text(json.dumps({"version": __version__, "url": "x.zip"}), encoding="utf-8")
    settings.update_url = str(manifest)

    assert (await Updater(settings).check()).available is False


async def test_missing_manifest_is_reported(settings, tmp_path):
    settings.update_url = str(tmp_path / "нет.json")
    info = await Updater(settings).check()

    assert not info.available
    assert "не удалось проверить" in info.error.lower()


async def test_manifest_without_version_is_rejected(settings, tmp_path):
    manifest = tmp_path / "update.json"
    manifest.write_text(json.dumps({"url": "x.zip"}), encoding="utf-8")
    settings.update_url = str(manifest)

    assert "version" in (await Updater(settings).check()).error


async def test_source_updates_are_not_installed_automatically(settings, tmp_path):
    """Из исходников обновляются через git, а не подменой файлов."""
    manifest = tmp_path / "update.json"
    manifest.write_text(json.dumps({"version": "99.0.0", "url": "p.zip"}), encoding="utf-8")
    settings.update_url = str(manifest)

    info = await Updater(settings).check()

    assert info.available
    assert info.installable is False, "мы запущены из исходников"


# --------------------------------------------------------------- установка


async def test_download_verifies_checksum(settings, tmp_path):
    package = tmp_path / "pack.zip"
    package.write_bytes(b"payload")

    updater = Updater(settings)
    info = await updater.check()
    info.url = str(package)
    info.sha256 = "0" * 64  # заведомо неверная сумма

    with pytest.raises(ValueError, match="контрольная сумма"):
        await updater.download(info)


def test_unpack_rejects_paths_outside_archive(settings, tmp_path):
    """Архив не должен уметь записать файл в чужую папку."""
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as bundle:
        bundle.writestr("../../захват.txt", "данные")

    with pytest.raises(ValueError, match="небезопасн"):
        Updater(settings).unpack(evil)


def test_unpack_returns_app_folder(settings, tmp_path):
    package = tmp_path / "pack.zip"
    with zipfile.ZipFile(package, "w") as bundle:
        bundle.writestr("LocalAIAgent/app.exe", "x")
        bundle.writestr("LocalAIAgent/static/app.js", "y")

    folder = Updater(settings).unpack(package)

    assert folder.name == "LocalAIAgent"
    assert (folder / "app.exe").exists()


def test_apply_refuses_outside_frozen_build(settings, tmp_path):
    with pytest.raises(RuntimeError, match="собранного приложения"):
        Updater(settings).apply(tmp_path)
