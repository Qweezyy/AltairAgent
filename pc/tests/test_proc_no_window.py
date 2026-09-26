"""No console-window flashing for child processes on Windows."""

from __future__ import annotations

import subprocess
import sys

import pytest

import core.utils.proc as proc


def test_python_executable_dev_is_sys_executable():
    # Вне собранного exe python_executable — это текущий интерпретатор.
    assert proc.python_executable() == sys.executable


def test_python_executable_prefers_project_venv(tmp_path):
    sub = "Scripts" if sys.platform == "win32" else "bin"
    name = "python.exe" if sys.platform == "win32" else "python"
    venv_py = tmp_path / ".venv" / sub / name
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("", encoding="utf-8")
    assert proc.python_executable(tmp_path) == str(venv_py)


def test_frozen_without_python_never_returns_the_app_itself(monkeypatch):
    """Packaged exe on a machine without Python: sys.executable is LocalAIAgent.exe, and
    running it with a script would start the app (argparse error) instead of Python."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    assert proc.python_executable() == "python"
    assert proc.python_executable() != sys.executable


def test_frozen_skips_microsoft_store_stub(monkeypatch, tmp_path):
    stub = r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe"
    real = str(tmp_path / "Python312" / "python.exe")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr("shutil.which", lambda name: stub if name == "python" else real)
    proc._runs_python.cache_clear()
    monkeypatch.setattr(proc, "_runs_python", lambda path: "windowsapps" not in path.lower())
    assert proc.python_executable() == real


def test_runs_python_rejects_a_failing_stub(tmp_path):
    proc._runs_python.cache_clear()
    fake = tmp_path / "WindowsApps" / "python.exe"
    fake.parent.mkdir()
    fake.write_text("", encoding="utf-8")  # not a runnable program
    assert proc._runs_python(str(fake)) is False
    assert proc._runs_python(r"C:\Python312\python.exe") is True  # outside WindowsApps: trusted


def test_detect_commands_use_real_python(tmp_path):
    # Команды проверок должны запускаться настоящим python, а не LocalAIAgent.exe.
    from core.quality.detect import detect_lint_commands, detect_test_commands

    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    (tmp_path / "test_x.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    cmds = detect_test_commands(tmp_path) + detect_lint_commands(tmp_path)
    assert cmds, "должны найтись команды pytest/ruff"
    for c in cmds:
        assert c.argv[0] == proc.python_executable(tmp_path)
        assert "LocalAIAgent" not in c.argv[0]


def test_no_window_kwargs_shape():
    kw = proc.no_window_kwargs()
    if sys.platform == "win32":
        assert kw == {"creationflags": proc.CREATE_NO_WINDOW}
        assert proc.CREATE_NO_WINDOW != 0
    else:
        assert kw == {}


@pytest.mark.skipif(sys.platform != "win32", reason="CREATE_NO_WINDOW is Windows-only")
def test_merge_no_window_adds_flag_but_respects_explicit_console():
    assert proc.merge_no_window(0) & proc.CREATE_NO_WINDOW
    # An explicit new/own console request is left as-is (not hidden).
    assert proc.merge_no_window(proc._CREATE_NEW_CONSOLE) == proc._CREATE_NEW_CONSOLE
    assert proc.merge_no_window(proc._DETACHED_PROCESS) == proc._DETACHED_PROCESS
    # Idempotent: applying twice keeps a single flag set.
    once = proc.merge_no_window(0)
    assert proc.merge_no_window(once) == once


@pytest.mark.skipif(sys.platform != "win32", reason="patch only matters on Windows")
def test_install_patches_popen_idempotently_and_hides_console():
    proc.install_no_window_default()
    assert getattr(subprocess.Popen, "_no_window_patched", False) is True
    first = subprocess.Popen.__init__
    proc.install_no_window_default()  # second call must not re-wrap
    assert subprocess.Popen.__init__ is first

    # A real child still runs correctly with the flag injected.
    p = subprocess.Popen(["cmd", "/c", "echo hi"], stdout=subprocess.PIPE)
    out, _ = p.communicate(timeout=10)
    assert b"hi" in out
