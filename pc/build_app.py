"""Сборка нативного приложения для Windows.

Делает папку `dist/LocalAIAgent` с exe и всеми зависимостями, а рядом —
zip-пакет и манифест для обновлений.

    python build_app.py            # собрать
    python build_app.py --zip      # собрать и упаковать в zip с манифестом
    python build_app.py --check    # проверить готовность к сборке

Почему одна папка, а не один файл: приложение стартует заметно быстрее, а
обновление сводится к подмене изменившихся файлов, а не всего образа.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
NAME = "LocalAIAgent"

sys.path.insert(0, str(ROOT))
from core.version import __version__  # noqa: E402


def running_instances() -> list[int]:
    """PID запущенных копий приложения.

    Windows не даёт перезаписать exe, который выполняется, а PyInstaller
    сообщает об этом невнятным «Отказано в доступе» к случайной dll. Лучше
    сказать прямо: закройте приложение.
    """
    if sys.platform != "win32":
        return []
    try:
        output = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {NAME}.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
        ).stdout
    except Exception:  # noqa: BLE001 - без tasklist просто не проверяем
        return []

    pids: list[int] = []
    for line in output.splitlines():
        parts = [part.strip('" ') for part in line.split('","')]
        if len(parts) > 1 and parts[0].lower() == f"{NAME.lower()}.exe" and parts[1].isdigit():
            pids.append(int(parts[1]))
    return pids


#: PyInstaller's --add-data separator: ";" on Windows, ":" elsewhere.
SEP = os.pathsep
WINDOWS = sys.platform == "win32"


def app_icon() -> list[str]:
    """The icon per system: .ico on Windows, .icns on macOS (when present), none on Linux."""
    if WINDOWS:
        return ["--icon", str(ROOT / "assets" / "icon.ico")]
    icns = ROOT / "assets" / "icon.icns"
    return ["--icon", str(icns)] if sys.platform == "darwin" and icns.exists() else []


def build() -> Path:
    """Runs PyInstaller and returns the folder of the build (Windows, Linux or macOS)."""
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", NAME,
        "--windowed",                       # без чёрного окна консоли
        *app_icon(),
        # Данные интерфейса и навыки кладём внутрь пакета.
        "--add-data", f"{ROOT / 'static'}{SEP}static",
        "--add-data", f"{ROOT / 'skills'}{SEP}skills",
        "--add-data", f"{ROOT / '.env.example'}{SEP}.",
        # Единая версия продукта: core/version.py читает VERSION из корня репо.
        "--add-data", f"{ROOT.parent / 'VERSION'}{SEP}.",
        # Быстрый поиск: забандленные tgrep/ripgrep (open-source, MIT) — работают
        # без установки, приложение находит их в vendor/bin. См. core/search_backend.py.
        "--add-data", f"{ROOT / 'vendor' / 'bin'}{SEP}vendor/bin",
        # PyInstaller не видит эти импорты: они подтягиваются динамически.
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan.on",
        # Разбор документов: PyInstaller не находит их через строковые импорты
        # в core/research/documents.py.
        "--hidden-import", "pypdf",
        "--hidden-import", "openpyxl",
        "--hidden-import", "docx",
        # Playwright тащит за собой драйвер, иначе headless-браузер не стартует.
        "--collect-all", "playwright",
        # Remote MCP servers: the SDK is imported inside a try block, pull all of it in.
        "--collect-submodules", "mcp",
        # SymPy и genanki грузятся отложенно, внутри функций.
        "--collect-all", "sympy",
        "--hidden-import", "genanki",
        # DuckDB импортируется отложенно (в функции) — иначе PyInstaller не увидит.
        "--collect-all", "duckdb",
        # Интерактивный терминал: winpty тащит conpty.dll и winpty-agent.exe —
        # без --collect-all PyInstaller соберёт только .py и терминал не стартует.
        *(["--collect-all", "winpty"] if WINDOWS else ["--collect-all", "ptyprocess"]),
        # Проверка типов: pyright тащит свой JS-дистрибутив (dist/) и качает Node
        # в кэш при первом запуске. Без --collect-all в exe не попадёт langserver.
        "--collect-all", "pyright",
        # Грамматики tree-sitter грузятся динамически (__import__), поэтому
        # PyInstaller их не видит — собираем явно бинарники каждой.
        "--collect-all", "tree_sitter",
        "--collect-all", "tree_sitter_javascript",
        "--collect-all", "tree_sitter_typescript",
        "--collect-all", "tree_sitter_go",
        "--collect-all", "tree_sitter_rust",
        "--collect-all", "tree_sitter_java",
        "--collect-all", "tree_sitter_c_sharp",
        str(ROOT / "main.py"),
    ]
    subprocess.run(command, check=True)
    return DIST / NAME


CLI_NAME = "altair"


def build_cli(folder: Path) -> Path:
    """The terminal command `altair`: a small console exe next to the backend.

    One file on purpose: it is only a client (a socket to the backend, rich for the output), so
    it stays small and starts fast, and it does not mix its files into the backend's _internal.
    It starts LocalAIAgent.exe (one folder up) when no backend is running.
    """
    work = ROOT / "build" / "altair_cli"
    out = DIST / "altair_cli"
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--console",
        "--name", CLI_NAME, "--workpath", str(work), "--distpath", str(out), "--specpath", str(work),
        *app_icon(),
        # rich loads its unicode tables by name; websockets picks its client lazily.
        "--collect-submodules", "rich", "--collect-submodules", "websockets",
        "--exclude-module", "tkinter", "--exclude-module", "numpy", "--exclude-module", "playwright",
        str(ROOT / "altair_cli.py"),
    ], check=True)
    exe = out / (f"{CLI_NAME}.exe" if sys.platform == "win32" else CLI_NAME)
    # In bin/: Windows does not tell altair.exe from the window's Altair.exe in one folder, and
    # only bin/ goes on PATH, so the other exes stay off it.
    target = folder / "bin" / exe.name
    target.parent.mkdir(exist_ok=True)
    shutil.copy2(exe, target)
    return target


SHELL_DIR = ROOT / "desktop" / "src-tauri"
SHELL_NAME = "Altair.exe" if sys.platform == "win32" else "Altair"


def build_shell() -> Path | None:
    """Builds the native Tauri shell (the app window) in release mode.

    Without it the packaged app still works, but opens in a browser app-window
    instead of its own frameless window — so a missing toolchain is a warning,
    not a failure.
    """
    if shutil.which("cargo") is None:
        print("cargo не найден — оболочка Tauri не собрана; приложение откроется в окне браузера.")
        return None
    subprocess.run(["cargo", "build", "--release"], cwd=SHELL_DIR, check=True)
    built = SHELL_DIR / "target" / "release" / ("app.exe" if sys.platform == "win32" else "app")
    return built if built.is_file() else None


def bundle_shell(shell: Path, folder: Path) -> Path:
    """Puts the shell next to the backend exe: main.py and the shell find each other there."""
    target = folder / SHELL_NAME
    shutil.copy2(shell, target)
    return target


def make_package(folder: Path) -> Path:
    """Пакует сборку в zip и пишет манифест обновления рядом."""
    archive = DIST / f"{NAME}-{__version__}.zip"
    archive.unlink(missing_ok=True)

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for item in folder.rglob("*"):
            if item.is_file():
                bundle.write(item, Path(NAME) / item.relative_to(folder))

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = {
        "version": __version__,
        "url": archive.name,   # замените на публичную ссылку при выкладывании
        "notes": f"Версия {__version__}",
        "sha256": digest,
    }
    (DIST / "update.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description="Сборка приложения")
    parser.add_argument("--zip", action="store_true", help="упаковать в zip и создать манифест")
    parser.add_argument("--check", action="store_true", help="только проверить готовность к сборке")
    parser.add_argument("--no-shell", action="store_true", help="не собирать оболочку Tauri (Altair.exe)")
    args = parser.parse_args()
    # VERSION is the one source: stamp it into package.json / Cargo.toml / pyproject.toml,
    # which cannot read another file themselves (see version_sync.py).
    from version_sync import sync as sync_version

    stamped = sync_version()
    if stamped:
        print("Version from VERSION stamped into: " + ", ".join(stamped))

    if shutil.which("pyinstaller") is None:
        try:
            import PyInstaller  # noqa: F401
        except ImportError:
            print("Сначала установите PyInstaller: pip install pyinstaller")
            return 1

    running = running_instances()
    if running:
        print(
            f"Приложение сейчас запущено (PID {', '.join(map(str, running))}).\n"
            f"Windows не даст перезаписать {NAME}.exe, пока он работает — закройте окно и повторите."
        )
        return 1

    if args.check:
        print("Сборка доступна: приложение закрыто, PyInstaller найден.")
        return 0

    folder = build()
    print(f"\nГотово: {folder}")
    print(f"CLI: {build_cli(folder)}")

    if not args.no_shell:
        shell = build_shell()
        if shell:
            print(f"Оболочка: {bundle_shell(shell, folder)}")

    if args.zip:
        archive = make_package(folder)
        print(f"Пакет:   {archive}")
        print(f"Манифест: {DIST / 'update.json'}")
        print("\nЧтобы обновления работали, выложите zip и манифест на любой хостинг")
        print("и укажите ссылку на манифест в UPDATE_URL (.env).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
