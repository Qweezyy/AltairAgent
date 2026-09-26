"""File-panel OS actions: path confinement and registry-name parsing."""

from __future__ import annotations

import pytest

from server import file_actions as fa


def test_resolve_stays_inside_workspace(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("x", encoding="utf-8")
    got = fa.resolve_in_workspace(str(tmp_path), "sub/a.txt")
    assert got == (tmp_path / "sub" / "a.txt").resolve()


def test_resolve_rejects_escape(tmp_path):
    (tmp_path / "ws").mkdir()
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
    with pytest.raises(fa.PathOutsideWorkspace):
        fa.resolve_in_workspace(str(tmp_path / "ws"), "../secret.txt")


def test_resolve_rejects_absolute_elsewhere(tmp_path):
    (tmp_path / "ws").mkdir()
    with pytest.raises(fa.PathOutsideWorkspace):
        fa.resolve_in_workspace(str(tmp_path / "ws"), "C:/Windows/System32")


def test_resolve_missing_file(tmp_path):
    with pytest.raises(fa.PathOutsideWorkspace):
        fa.resolve_in_workspace(str(tmp_path), "nope.txt")


def test_resolve_root_itself(tmp_path):
    assert fa.resolve_in_workspace(str(tmp_path), "") == tmp_path.resolve()


@pytest.mark.parametrize(
    "command,expected",
    [
        (r'"C:\Program Files\App\app.exe" "%1"', r"C:\Program Files\App\app.exe"),
        (r'"C:\A\b.exe"', r"C:\A\b.exe"),
        (r"C:\NoSpaces\tool.exe %1", r"C:\NoSpaces\tool.exe"),
        (r"C:\NoSpaces\tool.exe /edit %1", r"C:\NoSpaces\tool.exe"),
        ("", ""),
    ],
)
def test_exe_from_command(command, expected):
    assert fa.exe_from_command(command) == expected


def test_clean_display_rejects_indirect_and_garbled():
    assert fa._clean_display("Visual Studio Code") == "Visual Studio Code"
    assert fa._clean_display("@shell32.dll,-1234") == ""      # unresolved indirect
    assert fa._clean_display("bad\udc98name") == ""            # lone surrogate
    assert fa._clean_display("   ") == ""


def test_pretty_basename():
    assert fa._pretty_basename(r"C:\x\Code.exe") == "Code"
    assert fa._pretty_basename(r"C:\x\Antigravity IDE.exe") == "Antigravity IDE"


def test_list_openers_no_extension_is_empty(tmp_path):
    p = tmp_path / "Makefile"
    p.write_text("", encoding="utf-8")
    assert fa.list_openers(str(p)) == []
