"""Desktop entry point launches the Tauri shell, not pywebview."""

from __future__ import annotations

import sys

import main


def test_find_tauri_shell_dev_prefers_release_over_debug(tmp_path, monkeypatch):
    # Dev layout: desktop/src-tauri/target/{release,debug}/<exe>.
    target = tmp_path / "desktop" / "src-tauri" / "target"
    name = "Altair.exe" if sys.platform == "win32" else "Altair"
    dev = "app.exe" if sys.platform == "win32" else "app"
    (target / "release").mkdir(parents=True)
    (target / "debug").mkdir(parents=True)
    (target / "debug" / dev).write_text("", encoding="utf-8")
    (target / "release" / name).write_text("", encoding="utf-8")

    # Pretend main.py lives next to this fake desktop/ tree.
    monkeypatch.setattr(main, "__file__", str(tmp_path / "main.py"))
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    found = main._find_tauri_shell()
    assert found == str(target / "release" / name)


def test_find_tauri_shell_absent_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "__file__", str(tmp_path / "main.py"))
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert main._find_tauri_shell() is None


REPO = main.Path(main.__file__).resolve().parent.parent
SHELL_SRC = REPO / "pc" / "desktop" / "src-tauri"


def test_build_puts_shell_next_to_backend(tmp_path):
    """The packaged folder must contain Altair.exe next to LocalAIAgent.exe — otherwise
    the release opens in a browser window instead of the app's own window."""
    import build_app

    shell = tmp_path / "app.exe"
    shell.write_bytes(b"MZ")
    folder = tmp_path / "LocalAIAgent"
    folder.mkdir()
    placed = build_app.bundle_shell(shell, folder)
    assert placed == folder / build_app.SHELL_NAME and placed.read_bytes() == b"MZ"


def test_shell_config_is_release_safe():
    import json

    conf = json.loads((SHELL_SRC / "tauri.conf.json").read_text(encoding="utf-8"))
    # A Tauri NSIS/MSI bundle would not contain the Python backend: the installed app
    # would open a window with nothing behind it. Releases ship the zip instead.
    assert conf["bundle"]["active"] is False
    cargo = (SHELL_SRC / "Cargo.toml").read_text(encoding="utf-8")
    # The splash and the error page are data: URLs; without this feature the shell panics.
    assert "webview-data-url" in cargo


def test_shell_pages_are_utf8_and_waiting_is_generous():
    src = (SHELL_SRC / "src" / "lib.rs").read_text(encoding="utf-8")
    assert src.count("data:text/html;charset=utf-8,") >= 2  # splash + error page
    assert "for _ in 0..480" in src  # ~2 min: a cold first start can exceed 20 s
    assert "try_wait" in src  # ...but a dead backend is reported at once


def test_installer_is_session_safe_and_prefers_the_zip():
    text = (REPO / "install.ps1").read_text(encoding="utf-8")
    assert '$ProgressPreference = "SilentlyContinue"' in text  # PS 5.1 downloads crawl otherwise
    assert text.lstrip().splitlines()[0].startswith("#")
    assert "& {" in text  # runs in its own scope under `irm | iex`
    assert text.index("$zip") < text.index("$setup")


def test_backend_exits_when_its_shell_dies():
    """A crashed/killed shell must not leave the backend holding port 8137 — the next
    launch would get another port (another origin) and lose the UI's saved state."""
    import subprocess
    import time

    pc_dir = str(REPO / "pc")
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    child = subprocess.Popen(
        [sys.executable, "-c", f"import main, time; main._exit_with_parent({parent.pid}); time.sleep(120)"],
        cwd=pc_dir,
    )
    try:
        time.sleep(3)
        assert child.poll() is None  # alive while the parent lives
        parent.kill()
        child.wait(timeout=20)  # ...and gone soon after it dies
        assert child.returncode == 0
    finally:
        for proc in (parent, child):
            if proc.poll() is None:
                proc.kill()


def test_windowed_build_gets_std_streams(monkeypatch):
    """A windowed PyInstaller exe starts with sys.stdout/stderr = None; uvicorn then
    crashes on sys.stdout.isatty() and the backend hangs on an error dialog."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    main._ensure_std_streams()
    assert sys.stdout is not None and sys.stderr is not None
    sys.stdout.isatty()  # what uvicorn calls at startup — must not raise
    sys.stdout.write("ignored")
    sys.stdout.close()
    sys.stderr.close()


def test_app_commands_are_allowed_for_the_app_origin():
    """The window shows http://127.0.0.1 — a remote origin for Tauri — so every custom
    command needs a declared permission, or the call fails with "not allowed by ACL"."""
    import json
    import re

    lib = (SHELL_SRC / "src" / "lib.rs").read_text(encoding="utf-8")
    commands = re.search(r"generate_handler!\[([^\]]*)\]", lib).group(1)
    names = [c.strip().rsplit("::", 1)[-1] for c in commands.split(",") if c.strip()]
    build = (SHELL_SRC / "build.rs").read_text(encoding="utf-8")
    perms = json.loads((SHELL_SRC / "capabilities" / "default.json").read_text(encoding="utf-8"))["permissions"]
    for name in names:
        assert f'"{name}"' in build, f"{name} missing from the app manifest in build.rs"
        assert "allow-" + name.replace("_", "-") in perms, f"{name} has no capability permission"


def test_shell_passes_its_pid_to_the_backend():
    src = (SHELL_SRC / "src" / "lib.rs").read_text(encoding="utf-8")
    assert src.count('"--parent-pid"') == 2  # packaged sidecar and dev launch


def test_find_tauri_shell_frozen_sibling(tmp_path, monkeypatch):
    # Prod layout: packaged Altair shell sits next to the backend exe.
    name = "Altair.exe" if sys.platform == "win32" else "Altair"
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    (exe_dir / name).write_text("", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "LocalAIAgent.exe"), raising=False)
    assert main._find_tauri_shell() == str(exe_dir / name)
