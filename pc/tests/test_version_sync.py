"""One version for the whole product: <repo>/VERSION. Nothing may drift from it."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import version_sync
from core.version import __version__

REPO = Path(version_sync.__file__).resolve().parent.parent


def test_every_stamped_file_matches_version():
    expected = version_sync.read_version()
    assert __version__ == expected  # what the app shows and the updater compares
    for path, found in version_sync.stamped_versions().items():
        assert found == expected, f"{path} says {found}, VERSION says {expected} — run python version_sync.py"


def test_tauri_takes_its_version_from_package_json():
    conf = json.loads((REPO / "pc" / "desktop" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    assert conf["version"] == "../package.json"


def test_android_reads_version_file():
    gradle = (REPO / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    assert "VERSION" in gradle and 'versionName = "' not in gradle


def test_sync_bumps_only_the_package_versions(tmp_path, monkeypatch):
    for name, src in {"VERSION": version_sync.VERSION_FILE, "package.json": version_sync.PACKAGE_JSON,
                      "Cargo.toml": version_sync.CARGO_TOML, "pyproject.toml": version_sync.PYPROJECT}.items():
        shutil.copy(src, tmp_path / name)
    (tmp_path / "VERSION").write_text("2.3.4\n", encoding="utf-8")
    monkeypatch.setattr(version_sync, "REPO", tmp_path)
    monkeypatch.setattr(version_sync, "PC", tmp_path)
    for attr, name in (("VERSION_FILE", "VERSION"), ("PACKAGE_JSON", "package.json"),
                       ("CARGO_TOML", "Cargo.toml"), ("PYPROJECT", "pyproject.toml")):
        monkeypatch.setattr(version_sync, attr, tmp_path / name)

    assert sorted(version_sync.sync()) == ["Cargo.toml", "package.json", "pyproject.toml"]
    assert set(version_sync.stamped_versions().values()) == {"2.3.4"}
    cargo = (tmp_path / "Cargo.toml").read_text(encoding="utf-8")
    assert 'tauri = { version = "2.11.3"' in cargo  # dependency versions are left alone
    assert version_sync.sync() == []  # idempotent


def test_bad_version_file_is_refused(tmp_path, monkeypatch):
    bad = tmp_path / "VERSION"
    bad.write_text("v1.0-beta", encoding="utf-8")
    monkeypatch.setattr(version_sync, "VERSION_FILE", bad)
    import pytest

    with pytest.raises(ValueError):
        version_sync.read_version()
