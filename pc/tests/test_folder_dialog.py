"""System pick dialogs: the Python-free PowerShell fallbacks must be valid and UTF-8 safe.

Under the Tauri shell there is no pywebview window, so a user without Python gets only
the PowerShell dialogs. A syntax slip there means "attach files" silently does nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

import server.folder_dialog as fd

_PARSE = (
    "$e=$null; [void][System.Management.Automation.Language.Parser]::ParseInput("
    "$env:S,[ref]$null,[ref]$e); if($e){$e | ForEach-Object {$_.Message}} else {'OK'}"
)


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell dialogs are Windows-only")
@pytest.mark.parametrize(
    "script",
    [
        fd._PS_FILES_SCRIPT.format(filter=fd.ps_filter("media"), initial=r"C:\Users\Тест O''Brien"),
        fd._PS_FILES_SCRIPT.format(filter=fd.ps_filter("any"), initial=""),
        fd._PS_SCRIPT.format(title=fd.TITLE, initial="C:\\"),
        # Russian labels (the UI language) must not break the script either.
        fd._PS_FILES_SCRIPT.format(filter="Фото и видео|*.png|Все файлы|*.*", initial=""),
        fd._PS_SCRIPT.format(title="Выберите рабочую папку проекта", initial="C:\\"),
    ],
)
def test_powershell_dialog_scripts_parse(script):
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", _PARSE],
        capture_output=True, text=True, env=dict(os.environ, S=script), timeout=60,
    )
    assert out.stdout.strip() == "OK", out.stdout + out.stderr


def test_powershell_scripts_force_utf8_output():
    # Without it PowerShell 5.1 writes OEM code page and Cyrillic paths come back garbled.
    assert "OutputEncoding=[Text.Encoding]::UTF8" in fd._PS_SCRIPT
    assert "OutputEncoding=[Text.Encoding]::UTF8" in fd._PS_FILES_SCRIPT


def test_file_picker_falls_back_to_powershell(monkeypatch):
    monkeypatch.setattr(fd, "_files_via_tkinter", lambda *a: ([], "tkinter: python not found"))
    monkeypatch.setattr(fd, "_files_via_powershell", lambda *a: ([r"C:\a.txt"], ""))
    assert fd.pick_files() == ([r"C:\a.txt"], "")


def test_file_picker_cancel_stops_the_chain(monkeypatch):
    calls = []
    monkeypatch.setattr(fd, "_files_via_tkinter", lambda *a: ([], "cancelled"))
    monkeypatch.setattr(fd, "_files_via_powershell", lambda *a: calls.append(1) or ([], ""))
    assert fd.pick_files() == ([], "cancelled")
    assert not calls  # a closed dialog must not pop up a second one
