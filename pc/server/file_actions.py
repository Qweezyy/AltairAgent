"""OS-level file actions for the Files panel: reveal in Explorer, enumerate
programs that can open a file ("Open with"), and open a file / folder.

Kept separate from the web layer so the tricky bits (path confinement to the
workspace, parsing an executable out of a registry command) are unit-testable.
Windows-first; other platforms get a sensible default-open fallback.
"""

from __future__ import annotations

import ntpath
import os
import shlex
import subprocess
import sys
from pathlib import Path

from core.logging_setup import get_logger
from core.utils.proc import no_window_kwargs

logger = get_logger("file_actions")

# Sentinels understood by open_path() instead of a concrete executable.
OPEN_DEFAULT = "__default__"  # open with the OS default app
OPEN_CHOOSE = "__choose__"  # show the system "Open with…" dialog


class PathOutsideWorkspace(ValueError):
    """Requested path escapes the workspace — refuse it."""


def resolve_in_workspace(workspace: str, rel: str) -> Path:
    """Absolute, real path of ``rel`` inside ``workspace``. Rejects anything
    that resolves outside the workspace (``..`` tricks, symlinks, absolute
    paths pointing elsewhere)."""
    root = Path(workspace).resolve()
    if not root.is_dir():
        raise PathOutsideWorkspace("рабочая папка недоступна")
    target = (root / rel).resolve() if rel else root
    if target != root and root not in target.parents:
        raise PathOutsideWorkspace("путь вне рабочей папки")
    if not target.exists():
        raise PathOutsideWorkspace("файл не найден")
    return target


def exe_from_command(command: str) -> str:
    """Pull the executable path out of a registry ``shell\\open\\command`` value,
    e.g. ``"C:\\Program Files\\App\\app.exe" "%1"`` → ``C:\\Program Files\\App\\app.exe``.
    Returns "" if nothing usable is found."""
    command = (command or "").strip()
    if not command:
        return ""
    if command[0] == '"':
        end = command.find('"', 1)
        if end > 0:
            return command[1:end]
    # Unquoted: take everything up to the EARLIEST argument that looks like a
    # placeholder or switch. A bare path with spaces and no quotes is ambiguous;
    # we keep it simple and cut at the first " %" or " /" that appears.
    cuts = [command.find(m) for m in (' "%', " %", " /")]
    cuts = [c for c in cuts if c > 0]
    if cuts:
        return command[: min(cuts)].strip()
    return command.split()[0] if command.split() else ""


def _clean_display(name: str) -> str:
    """A registry name is usable only if it's real text: not an unresolved
    indirect string (``@dll,-id``) and not garbled (lone surrogates from a
    mis-encoded REG_SZ). Returns "" when it isn't."""
    name = (name or "").strip()
    if not name or name.startswith("@"):
        return ""
    try:
        name.encode("utf-8")  # lone surrogates from winreg raise here
    except UnicodeEncodeError:
        return ""
    if not name.isprintable():
        return ""
    return name


def _pretty_basename(exe: str) -> str:
    """Clean, recognizable name from an exe path: ``Code.exe`` → ``Code``."""
    # Registry paths are Windows paths: split them the Windows way on any system.
    base = ntpath.basename(exe or "")
    return base[:-4] if base.lower().endswith(".exe") else base


def _friendly_name(exe: str, progid: str, hive_name: str) -> str:
    """Best-effort human name for an opener; falls back to the exe file name.
    Prefers a clean FriendlyAppName, but never returns garbled/indirect text."""
    import winreg

    base = os.path.basename(exe) if exe else ""
    for root, sub in (
        (winreg.HKEY_CLASSES_ROOT, rf"Applications\{base}"),
        (winreg.HKEY_CLASSES_ROOT, progid),
    ):
        if not sub or sub.endswith("\\"):
            continue
        try:
            with winreg.OpenKey(root, sub) as key:
                # Only FriendlyAppName is a real application name. The key's
                # default value is a file-TYPE description ("Text document"),
                # shared across apps — never use it as the program name.
                name, _ = winreg.QueryValueEx(key, "FriendlyAppName")
                clean = _clean_display(str(name))
                if clean:
                    return clean
        except OSError:
            continue
    return _pretty_basename(exe) or _pretty_basename(hive_name) or hive_name


def _command_for_progid(progid: str) -> str:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{progid}\shell\open\command") as key:
            cmd, _ = winreg.QueryValueEx(key, "")
            return str(cmd or "")
    except OSError:
        return ""


def list_openers(path: str) -> list[dict]:
    """Programs registered to open this file's type. Each item is
    ``{"name": str, "exec": <exe path>}``. Empty on non-Windows or when nothing
    is registered (the caller still offers the default + "choose" entries)."""
    if sys.platform != "win32":
        return []
    import winreg

    ext = os.path.splitext(path)[1].lower()
    if not ext:
        return []

    progids: list[str] = []

    def add_progid(pid: str) -> None:
        pid = (pid or "").strip()
        if pid and pid not in progids:
            progids.append(pid)

    # 1) Per-user explicit choice wins.
    try:
        key_path = rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{ext}\UserChoice"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            pid, _ = winreg.QueryValueEx(key, "ProgId")
            add_progid(pid)
    except OSError:
        pass

    # 2) Default association for the extension.
    for hive, sub in ((winreg.HKEY_CLASSES_ROOT, ext),):
        try:
            with winreg.OpenKey(hive, sub) as key:
                default, _ = winreg.QueryValueEx(key, "")
                add_progid(default)
        except OSError:
            pass

    # 3) OpenWithProgids lists (HKCR and per-user).
    for hive, sub in (
        (winreg.HKEY_CLASSES_ROOT, rf"{ext}\OpenWithProgids"),
        (winreg.HKEY_CURRENT_USER,
         rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{ext}\OpenWithProgids"),
    ):
        try:
            with winreg.OpenKey(hive, sub) as key:
                i = 0
                while True:
                    name, _val, _t = winreg.EnumValue(key, i)
                    add_progid(name)
                    i += 1
        except OSError:
            pass

    openers: list[dict] = []
    seen_exe: set[str] = set()
    for pid in progids:
        cmd = _command_for_progid(pid)
        exe = exe_from_command(cmd)
        if not exe or not os.path.exists(exe):
            continue
        key = exe.lower()
        if key in seen_exe:
            continue
        seen_exe.add(key)
        openers.append({"name": _friendly_name(exe, pid, pid), "exec": exe})

    # 4) OpenWithList exe names → resolve via Applications\<exe>.
    for hive, sub in (
        (winreg.HKEY_CLASSES_ROOT, rf"{ext}\OpenWithList"),
        (winreg.HKEY_CURRENT_USER,
         rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{ext}\OpenWithList"),
    ):
        try:
            with winreg.OpenKey(hive, sub) as key:
                i = 0
                while True:
                    _name, exe_name, _t = winreg.EnumValue(key, i)
                    i += 1
                    exe_name = str(exe_name or "")
                    if not exe_name.lower().endswith(".exe"):
                        continue
                    cmd = _command_for_progid(rf"Applications\{exe_name}") or ""
                    exe = exe_from_command(cmd)
                    if not exe or not os.path.exists(exe) or exe.lower() in seen_exe:
                        continue
                    seen_exe.add(exe.lower())
                    openers.append({"name": _friendly_name(exe, "", exe_name), "exec": exe})
        except OSError:
            pass

    return openers


def reveal_in_explorer(target: Path) -> None:
    """Open the OS file manager with ``target`` selected."""
    if sys.platform == "win32":
        # explorer returns exit code 1 even on success, so don't check it.
        subprocess.Popen(["explorer", f"/select,{target}"], **no_window_kwargs())
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(target)])
    else:
        # Most Linux file managers can't select; open the containing folder.
        subprocess.Popen(["xdg-open", str(target.parent)])


def open_path(target: Path, opener: str = OPEN_DEFAULT) -> None:
    """Open ``target`` with the default app, a chosen executable, or the system
    "Open with…" dialog."""
    if opener == OPEN_CHOOSE:
        if sys.platform == "win32":
            subprocess.Popen(
                ["rundll32.exe", "shell32.dll,OpenAs_RunDLL", str(target)],
                **no_window_kwargs(),
            )
            return
        opener = OPEN_DEFAULT
    if opener and opener != OPEN_DEFAULT:
        subprocess.Popen([opener, str(target)], **no_window_kwargs())
        return
    # Default app.
    if sys.platform == "win32":
        os.startfile(str(target))  # noqa: S606 - intended: open with default app
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])


def _quote(path: str) -> str:
    """Quote a path for a shell command line (used by the 'open in terminal')."""
    if sys.platform == "win32":
        return f'"{path}"' if " " in path else path
    return shlex.quote(path)
