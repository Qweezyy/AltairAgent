"""Puts the `altair` terminal command on the user's PATH.

Only install.ps1 used to do it, so an app unpacked from the zip, built locally or moved to
another folder had `altair` that no terminal could find. Now the packaged app registers its own
`bin/` on start: on Windows in the user's Path (HKCU\\Environment, no admin rights), elsewhere as a
link in ~/.local/bin. Off with CLI_ON_PATH=false. Running from sources changes nothing.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger("cli_path")

CLI_NAME = "altair.exe" if os.name == "nt" else "altair"


def _norm(entry: str) -> str:
    return (
        os.path.normcase(os.path.normpath(os.path.expandvars(entry.strip().strip('"'))))
        if entry.strip()
        else ""
    )


def with_dir(path_value: str, folder: str, *, stale: Callable[[str], bool] | None = None) -> str | None:
    """The Path value with `folder` in it, or None when it is there already.

    `stale(entry)`: True for an entry left by an earlier copy of the app (its altair is gone) —
    removed, so a moved app does not leave dead folders ahead of the live one."""
    parts = [p for p in path_value.split(";") if p.strip()]
    target = _norm(folder)
    kept = [p for p in parts if not (stale and _norm(p) != target and stale(p))]
    if any(_norm(p) == target for p in kept):
        return None if len(kept) == len(parts) else ";".join(kept)
    return ";".join([*kept, folder])


def _stale_altair(entry: str) -> bool:
    """An entry that held an Altair `bin` whose command is gone (the app moved or was deleted)."""
    path = Path(os.path.expandvars(entry.strip().strip('"')))
    owner = path.parent.name.lower()
    ours = "altair" in owner or "localaiagent" in owner
    return ours and path.name.lower() == "bin" and not (path / CLI_NAME).exists()


def _windows_register(folder: str) -> bool:
    import ctypes
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE
    ) as key:
        try:
            value, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            value, kind = "", winreg.REG_EXPAND_SZ
        updated = with_dir(str(value), folder, stale=_stale_altair)
        if updated is None:
            return False
        winreg.SetValueEx(
            key,
            "Path",
            0,
            kind if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) else winreg.REG_EXPAND_SZ,
            updated,
        )
    # New terminals read the change; without the broadcast only after the next sign-in.
    result = ctypes.c_ulong()
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF, 0x001A, 0, "Environment", 0x0002, 3000, ctypes.byref(result)
    )
    return True


def _posix_register(cli: Path, bin_dir: Path) -> bool:
    bin_dir.mkdir(parents=True, exist_ok=True)
    link = bin_dir / "altair"
    if link.is_symlink() and link.resolve() == cli.resolve():
        return False
    if link.exists() and not link.is_symlink():
        logger.info("%s exists and is not a link: leaving it", link)
        return False
    if link.is_symlink():
        link.unlink()
    link.symlink_to(cli)
    return True


def ensure_on_path(enabled: bool = True, app_folder: Path | None = None) -> str:
    """Registers the app's `bin/` (or links its command). Returns what was done, for the log:
    "added", "present", "skipped" (sources, off, or no command next to the app)."""
    if not enabled or (app_folder is None and not getattr(sys, "frozen", False)):
        return "skipped"
    folder = app_folder or Path(sys.executable).parent
    cli = folder / "bin" / CLI_NAME
    if not cli.exists():
        return "skipped"
    try:
        if os.name == "nt":
            changed = _windows_register(str(cli.parent))
        else:
            changed = _posix_register(cli, Path.home() / ".local" / "bin")
    except OSError as exc:
        logger.warning("Could not put altair on PATH: %s", exc)
        return "skipped"
    if changed:
        logger.info("altair is on PATH now (%s); new terminals see it", cli.parent)
    return "added" if changed else "present"
