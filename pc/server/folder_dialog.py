"""Системные диалоги выбора папки и файлов.

Два способа, по убыванию качества:
  1. tkinter в отдельном процессе — современный диалог Windows, если есть Python с Tk;
  2. PowerShell — есть на любой Windows, работает и без Python.

Почему отдельный процесс для tkinter: Tk требует главного потока и своего
цикла событий, поэтому внутри uvicorn его запускать нельзя — приложение
зависнет. Отдельный процесс изолирует эту проблему.

Каждый способ возвращает (путь | None, причина_неудачи). Причина доходит до
интерфейса — «молча ничего не произошло» здесь недопустимо: именно так
предыдущая версия и выглядела сломанной.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from core.i18n import tr
from core.logging_setup import get_logger
from core.utils.proc import no_window_kwargs, python_executable

logger = get_logger("server.dialog")

DIALOG_TIMEOUT = 300.0
TITLE = "Choose the project folder"  # the localised one comes from core.i18n at call time

def has_native_window() -> bool:
    """The Tauri shell has no dialog bridge to the backend yet, so the UI falls back to
    an HTML file input when every system dialog failed."""
    return False


# --------------------------------------------------------------------- 1

_TK_SCRIPT = '''
import os
import sys
import tkinter as tk
from tkinter import filedialog

initial = sys.argv[1] if len(sys.argv) > 1 else ""
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
root.update()
path = filedialog.askdirectory(
    parent=root,
    initialdir=initial if initial and os.path.isdir(initial) else None,
    title=sys.argv[2] if len(sys.argv) > 2 else "Choose the project folder",
    mustexist=True,
)
root.destroy()
if path:
    sys.stdout.write(os.path.normpath(path))
'''


def _pick_via_tkinter(initial: str) -> tuple[str | None, str]:
    """Отдельный процесс с Tk. Скрипт пишется в файл, а не в `python -c`:
    многострочный код с `if` в одну строку через `;` — синтаксическая ошибка."""
    script_path = Path(tempfile.gettempdir()) / "agent_folder_dialog.py"
    try:
        script_path.write_text(_TK_SCRIPT, encoding="utf-8")
        proc = subprocess.run(  # noqa: S603 - запускаем настоящий Python (не сам exe)
            [python_executable(), str(script_path), initial or "", tr("dlg.pick_folder")],
            capture_output=True,
            text=True,
            timeout=DIALOG_TIMEOUT,
            encoding="utf-8",
            errors="replace",
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return None, "диалог не закрыли вовремя"
    except OSError as exc:
        return None, f"tkinter: {exc}"
    finally:
        try:
            script_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            pass

    path = (proc.stdout or "").strip()
    if path and os.path.isdir(path):
        return os.path.normpath(path), ""
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()[-1:] or ["неизвестная ошибка"]
        return None, f"tkinter: {detail[0][:200]}"
    return None, "cancelled"


# --------------------------------------------------------------------- 3

#: PowerShell 5.1 writes stdout in the OEM code page, which mangles Cyrillic paths;
#: switching the console to UTF-8 first makes the path survive the round trip.
_PS_UTF8 = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"

_PS_SCRIPT = (
    "$ErrorActionPreference='Stop';"
    + _PS_UTF8
    + "$app = New-Object -ComObject Shell.Application;"
    "$folder = $app.BrowseForFolder(0, '{title}', 0x51, '{initial}');"
    "if ($folder) {{ [Console]::Out.Write($folder.Self.Path) }}"
)


def _pick_via_powershell(initial: str) -> tuple[str | None, str]:
    if os.name != "nt":
        return None, "PowerShell-диалог только для Windows"
    script = _PS_SCRIPT.format(
        title=tr("dlg.pick_folder").replace("'", "''"), initial=(initial or "").replace("'", "''")
    )
    try:
        proc = subprocess.run(  # noqa: S603
            ["powershell.exe", "-STA", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=DIALOG_TIMEOUT,
            encoding="utf-8",
            errors="replace",
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return None, "диалог не закрыли вовремя"
    except OSError as exc:
        return None, f"powershell: {exc}"

    path = (proc.stdout or "").strip()
    if path and os.path.isdir(path):
        return os.path.normpath(path), ""
    return None, "cancelled"


# ---------------------------------------------------------------- фасад


_TK_FILES_SCRIPT = '''
import os
import sys
import tkinter as tk
from tkinter import filedialog

initial = sys.argv[1] if len(sys.argv) > 1 else ""
kind = sys.argv[2] if len(sys.argv) > 2 else "any"
filters = {
    "media": [(sys.argv[4], "*.png *.jpg *.jpeg *.webp *.gif *.mp4 *.mov *.webm *.mpeg")],
    "any": [],
}.get(kind, [])
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
root.update()
paths = filedialog.askopenfilenames(
    parent=root,
    initialdir=initial if initial and os.path.isdir(initial) else None,
    title=sys.argv[3],
    filetypes=filters + [(sys.argv[5], "*.*")],
)
root.destroy()
sys.stdout.write("\\n".join(os.path.normpath(p) for p in paths))
'''

def _files_via_tkinter(initial: str, kind: str) -> tuple[list[str], str]:
    script_path = Path(tempfile.gettempdir()) / "agent_files_dialog.py"
    try:
        script_path.write_text(_TK_FILES_SCRIPT, encoding="utf-8")
        proc = subprocess.run(  # noqa: S603 - запускаем настоящий Python (не сам exe)
            [python_executable(), str(script_path), initial or "", kind,
             tr("dlg.pick_files"), tr("dlg.media"), tr("dlg.all_files")],
            capture_output=True,
            text=True,
            timeout=DIALOG_TIMEOUT,
            encoding="utf-8",
            errors="replace",
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return [], "диалог не закрыли вовремя"
    except OSError as exc:
        return [], f"tkinter: {exc}"
    finally:
        try:
            script_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            pass

    paths = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    if paths:
        return paths, ""
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()[-1:] or ["неизвестная ошибка"]
        return [], f"tkinter: {detail[0][:200]}"
    return [], "cancelled"


_PS_FILES_SCRIPT = (
    "$ErrorActionPreference='Stop';"
    + _PS_UTF8
    + "Add-Type -AssemblyName System.Windows.Forms;"
    "$owner = New-Object System.Windows.Forms.Form -Property @{{TopMost=$true}};"
    "$d = New-Object System.Windows.Forms.OpenFileDialog;"
    "$d.Multiselect = $true; $d.Filter = '{filter}';"
    "if ('{initial}' -and (Test-Path -LiteralPath '{initial}')) {{ $d.InitialDirectory = '{initial}' }};"
    "if ($d.ShowDialog($owner) -eq 'OK') {{ [Console]::Out.Write(($d.FileNames -join \"`n\")) }}"
)

_MEDIA_MASK = "*.png;*.jpg;*.jpeg;*.webp;*.gif;*.mp4;*.mov;*.webm;*.mpeg"


def ps_filter(kind: str) -> str:
    """The dialog's file-type list, with labels in the UI language."""
    everything = f"{tr('dlg.all_files')}|*.*"
    media = f"{tr('dlg.media')}|{_MEDIA_MASK}|{everything}"
    return (media if kind == "media" else everything).replace("'", "''")


def _files_via_powershell(initial: str, kind: str) -> tuple[list[str], str]:
    """Windows' own open-file dialog: works without Python, unlike the Tk fallback."""
    if os.name != "nt":
        return [], "PowerShell-диалог только для Windows"
    script = _PS_FILES_SCRIPT.format(filter=ps_filter(kind), initial=(initial or "").replace("'", "''"))
    try:
        proc = subprocess.run(  # noqa: S603
            ["powershell.exe", "-STA", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=DIALOG_TIMEOUT,
            encoding="utf-8",
            errors="replace",
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return [], "диалог не закрыли вовремя"
    except OSError as exc:
        return [], f"powershell: {exc}"

    paths = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    if paths:
        return [os.path.normpath(p) for p in paths], ""
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()[-1:] or ["неизвестная ошибка"]
        return [], f"powershell: {detail[0][:200]}"
    return [], "cancelled"


def pick_files(initial: str = "", kind: str = "any") -> tuple[list[str], str]:
    """Системный диалог выбора файлов. kind: 'any' или 'media'.

    Возвращает (пути, причина_неудачи); пустой список с "cancelled" означает,
    что пользователь просто закрыл окно.
    """
    reasons: list[str] = []
    for attempt in (_files_via_tkinter, _files_via_powershell):
        paths, reason = attempt(initial, kind)
        if paths:
            logger.info("Выбрано файлов: %d (%s)", len(paths), attempt.__name__)
            return paths, ""
        if reason == "cancelled":
            return [], "cancelled"
        reasons.append(f"{attempt.__name__.replace('_files_via_', '')}: {reason}")

    logger.warning("Диалог выбора файлов не открылся: %s", "; ".join(reasons))
    return [], "; ".join(reasons)


def pick_folder(initial: str = "") -> tuple[str | None, str]:
    """Открывает системный диалог. Возвращает (путь, причина_неудачи).

    Путь = None и причина "cancelled" означают, что пользователь просто закрыл
    окно — это не ошибка.
    """
    reasons: list[str] = []
    for attempt in (_pick_via_tkinter, _pick_via_powershell):
        path, reason = attempt(initial)
        if path:
            logger.info("Выбрана папка через %s: %s", attempt.__name__, path)
            return path, ""
        if reason == "cancelled":
            return None, "cancelled"
        reasons.append(f"{attempt.__name__.replace('_pick_via_', '')}: {reason}")

    logger.warning("Ни один системный диалог не открылся: %s", "; ".join(reasons))
    return None, "; ".join(reasons)
