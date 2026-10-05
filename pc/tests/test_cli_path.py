"""`altair` on the user's PATH: the packaged app registers its own bin/ on start.

Found live: the app unpacked from the zip had bin/altair.exe, but `altair` in a new PowerShell
was "not recognized" — only install.ps1 ever touched PATH.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import core.cli_path as cli_path
from core.cli_path import CLI_NAME, ensure_on_path, with_dir


def test_the_folder_is_appended_once():
    assert (
        with_dir(r"C:\Windows;C:\Tools", r"D:\Apps\Altair\bin") == r"C:\Windows;C:\Tools;D:\Apps\Altair\bin"
    )
    assert with_dir(r"C:\Windows;D:\Apps\Altair\bin", r"D:\Apps\Altair\bin") is None
    # The same folder written differently is still the same folder.
    same = r"d:\apps\altair\bin\\" if os.name == "nt" else "/opt/altair/bin/"
    assert with_dir(f"C:\\x;{same}", same.rstrip("\\/")) is None
    assert with_dir("", r"D:\A\bin") == r"D:\A\bin"


def test_a_dead_entry_of_a_moved_app_is_removed(tmp_path):
    old = tmp_path / "Altair-old" / "bin"  # the app was here once; its altair is gone
    old.mkdir(parents=True)
    other = tmp_path / "tools" / "bin"  # somebody else's bin: never touched
    other.mkdir(parents=True)
    new = tmp_path / "Altair" / "bin"
    value = f"{old};{other}"
    updated = with_dir(value, str(new), stale=cli_path._stale_altair)
    assert updated == f"{other};{new}"


def test_the_packaged_app_registers_its_bin(tmp_path, monkeypatch):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / CLI_NAME).write_bytes(b"")
    seen: list[str] = []
    monkeypatch.setattr(cli_path, "_windows_register", lambda folder: seen.append(folder) or True)
    monkeypatch.setattr(
        cli_path, "_posix_register", lambda cli, bin_dir: seen.append(str(cli.parent)) or True
    )
    assert ensure_on_path(True, app_folder=tmp_path) == "added"
    assert seen == [str(tmp_path / "bin")]
    assert ensure_on_path(False, app_folder=tmp_path) == "skipped"  # CLI_ON_PATH=false


def test_nothing_changes_from_sources_or_without_the_command(tmp_path):
    assert ensure_on_path(True) == "skipped"  # not frozen: python main.py
    assert ensure_on_path(True, app_folder=tmp_path) == "skipped"  # no bin/altair next to it


def test_on_linux_and_macos_a_link_goes_to_local_bin(tmp_path):
    cli = tmp_path / "app" / "bin" / "altair"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    local_bin = tmp_path / "home" / ".local" / "bin"
    try:
        assert cli_path._posix_register(cli, local_bin) is True
    except OSError:
        pytest.skip("symlinks need extra rights here")
    assert (local_bin / "altair").resolve() == cli.resolve()
    assert cli_path._posix_register(cli, local_bin) is False  # already there
    # A file of the user's own with that name is never replaced.
    (local_bin / "altair").unlink()
    (local_bin / "altair").write_text("mine", encoding="utf-8")
    assert cli_path._posix_register(cli, local_bin) is False
    assert (local_bin / "altair").read_text(encoding="utf-8") == "mine"


def test_the_setting_is_documented():
    root = Path(__file__).resolve().parents[1]
    assert "CLI_ON_PATH=true" in (root / ".env.example").read_text(encoding="utf-8")
