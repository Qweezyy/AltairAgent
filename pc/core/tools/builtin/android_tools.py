"""Инструменты управления внешним Android-эмулятором для проверки UI."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from core.events import ArtifactCreated
from core.i18n import tr
from core.security.paths import resolve_path, safe_relpath
from core.tools.base import EmptyArgs, Tool, ToolContext, ToolResult
from core.utils.proc import run_process

_MAX_OUTPUT = 20_000


class AndroidError(RuntimeError):
    """Понятная ошибка Android SDK/ADB."""


def _sdk_root(ctx: ToolContext) -> Path | None:
    raw = ctx.settings.android_sdk_path.strip()
    candidates = [Path(raw)] if raw else []
    for name in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(name, "").strip()
        if value:
            candidates.append(Path(value))
    for candidate in candidates:
        path = candidate.expanduser().resolve()
        if path.is_dir():
            return path
    return None


def _tool_path(ctx: ToolContext, name: str, relative: str) -> str | None:
    root = _sdk_root(ctx)
    if root:
        candidate = root / relative
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name) or shutil.which(f"{name}.exe")


def _adb(ctx: ToolContext) -> str:
    path = _tool_path(ctx, "adb", "platform-tools/adb.exe")
    if not path:
        raise AndroidError(
            "adb не найден. Установите Android SDK Platform-Tools или задайте ANDROID_SDK_PATH."
        )
    return path


def _emulator(ctx: ToolContext) -> str:
    path = _tool_path(ctx, "emulator", "emulator/emulator.exe")
    if not path:
        raise AndroidError(
            "Android Emulator не найден. Установите компонент Android Emulator или задайте "
            "ANDROID_SDK_PATH."
        )
    return path


def _format_process(result: Any) -> str:
    parts = [f"Код возврата: {result.returncode}"]
    if result.stdout.strip():
        parts.append(f"stdout:\n{result.stdout.strip()}")
    if result.stderr.strip():
        parts.append(f"stderr:\n{result.stderr.strip()}")
    return "\n\n".join(parts)


class AndroidManager:
    """Процесс-глобальное состояние запущенного этим приложением эмулятора."""

    def __init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None
        self.avd: str | None = None

    def start(self, executable: str, avd: str) -> str:
        if self.process and self.process.poll() is None:
            return f"Эмулятор «{self.avd}» уже запущен (pid {self.process.pid})."
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            [executable, "-avd", avd, "-no-snapshot-save"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        self.avd = avd
        return f"Эмулятор «{avd}» запущен (pid {self.process.pid})."

    def stop(self) -> bool:
        if not self.process or self.process.poll() is not None:
            self.process = None
            self.avd = None
            return False
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()
        self.process = None
        self.avd = None
        return True


_MANAGER = AndroidManager()


async def _adb_run(ctx: ToolContext, args: list[str], *, timeout: float | None = None) -> Any:
    return await run_process(
        [_adb(ctx), *args],
        cwd=ctx.settings.workspace,
        timeout=timeout or ctx.settings.android_timeout,
    )


class AndroidDiagnoseTool(Tool):
    name = "android_diagnose"
    description = (
        "Проверяет готовность Android SDK, adb и эмулятора для разработки и проверки UI. "
        "Используй первым перед любыми Android-инструментами."
    )
    Args = EmptyArgs
    category = "read"

    async def run(self, args: EmptyArgs, ctx: ToolContext) -> ToolResult:
        rows = ["Диагностика Android:"]
        root = _sdk_root(ctx)
        rows.append(f"SDK: {root or 'не найден'}")
        adb = _tool_path(ctx, "adb", "platform-tools/adb.exe")
        emulator = _tool_path(ctx, "emulator", "emulator/emulator.exe")
        rows.append(f"adb: {adb or 'не найден'}")
        rows.append(f"emulator: {emulator or 'не найден'}")
        if adb:
            result = await _adb_run(ctx, ["version"])
            rows.append(f"adb version: {result.stdout.strip() or result.stderr.strip() or 'ошибка'}")
        if not adb or not emulator:
            rows.append(
                "Установите Android Studio с Android SDK, Platform-Tools и Android Emulator, "
                "затем создайте AVD в Device Manager."
            )
            return ToolResult(content="\n".join(rows), ok=False)
        return ToolResult(content="\n".join(rows))


class AndroidDevicesTool(Tool):
    name = "android_devices"
    description = "Показывает подключённые Android-устройства и эмуляторы через adb."
    Args = EmptyArgs
    category = "read"

    async def run(self, args: EmptyArgs, ctx: ToolContext) -> ToolResult:
        try:
            result = await _adb_run(ctx, ["devices", "-l"])
        except AndroidError as exc:
            return ToolResult.fail(str(exc))
        return ToolResult(content=_format_process(result), ok=result.ok)


class AndroidAvdsTool(Tool):
    name = "android_avds"
    description = "Показывает имена доступных виртуальных устройств (AVD)."
    Args = EmptyArgs
    category = "read"

    async def run(self, args: EmptyArgs, ctx: ToolContext) -> ToolResult:
        try:
            result = await run_process(
                [_emulator(ctx), "-list-avds"],
                timeout=ctx.settings.android_timeout,
            )
        except AndroidError as exc:
            return ToolResult.fail(str(exc))
        return ToolResult(content=_format_process(result), ok=result.ok)


class AndroidStartArgs(BaseModel):
    avd: str = Field(default="", description="Имя AVD; пусто — значение ANDROID_AVD_NAME")


class AndroidStartTool(Tool):
    name = "android_start"
    description = "Запускает внешний Android-эмулятор для последующей проверки UI."
    Args = AndroidStartArgs
    category = "execute"
    dangerous = True
    timeout = None

    def approval_reason(self, args: AndroidStartArgs) -> str:  # type: ignore[override]
        return tr("appr.android_start", avd=args.avd or tr("appr.default"))

    async def run(self, args: AndroidStartArgs, ctx: ToolContext) -> ToolResult:
        avd = args.avd.strip() or ctx.settings.android_avd_name.strip()
        if not avd:
            return ToolResult.fail("Не указано имя AVD. Передайте avd или задайте ANDROID_AVD_NAME.")
        try:
            message = await asyncio.to_thread(_MANAGER.start, _emulator(ctx), avd)
        except (AndroidError, OSError) as exc:
            return ToolResult.fail(str(exc))
        return ToolResult(content=message)


class AndroidStopTool(Tool):
    name = "android_stop"
    description = "Останавливает Android-эмулятор, запущенный этим приложением."
    Args = EmptyArgs
    category = "execute"
    dangerous = True
    timeout = None

    def auto_verdict(self, args: EmptyArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        return "allow"

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.android_stop")

    async def run(self, args: EmptyArgs, ctx: ToolContext) -> ToolResult:
        stopped = await asyncio.to_thread(_MANAGER.stop)
        return ToolResult(content="Эмулятор остановлен." if stopped else "Запущенный приложением эмулятор не найден.")


class AndroidApkArgs(BaseModel):
    apk: str = Field(description="Путь к APK внутри рабочей папки")
    serial: str = Field(default="", description="Серийный номер устройства; пусто — adb выберет устройство")


class AndroidInstallTool(Tool):
    name = "android_install_apk"
    description = "Устанавливает APK на подключённый эмулятор для проверки интерфейса."
    Args = AndroidApkArgs
    category = "execute"
    dangerous = True

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.android_install", apk=args.apk)

    async def run(self, args: AndroidApkArgs, ctx: ToolContext) -> ToolResult:
        try:
            apk = resolve_path(args.apk, settings=ctx.settings, must_exist=True, must_be_file=True)
            command = ["install", "-r"]
            if args.serial.strip():
                command[0:0] = ["-s", args.serial.strip()]
            result = await _adb_run(ctx, [*command, str(apk)])
        except (AndroidError, OSError) as exc:
            return ToolResult.fail(str(exc))
        return ToolResult(content=_format_process(result), ok=result.ok)


class AndroidScreenshotArgs(BaseModel):
    output: str = Field(default=".screenshots/android.png", description="Путь PNG внутри рабочей папки")
    serial: str = Field(default="", description="Серийный номер устройства; пусто — adb выберет устройство")


async def _capture_png(ctx: ToolContext, serial: str) -> bytes:
    adb = _adb(ctx)
    command = [adb]
    if serial.strip():
        command.extend(["-s", serial.strip()])
    command.extend(["exec-out", "screencap", "-p"])

    def capture() -> bytes:
        completed = subprocess.run(command, capture_output=True, timeout=ctx.settings.android_timeout, check=False)
        if completed.returncode != 0:
            raise AndroidError(completed.stderr.decode("utf-8", "replace").strip() or "Не удалось снять скриншот.")
        return completed.stdout

    return await asyncio.to_thread(capture)


class AndroidScreenshotTool(Tool):
    name = "android_screenshot"
    description = "Снимает PNG-скриншот экрана подключённого Android-эмулятора и показывает его в «Превью»."
    Args = AndroidScreenshotArgs
    category = "read"

    async def run(self, args: AndroidScreenshotArgs, ctx: ToolContext) -> ToolResult:
        try:
            png = await _capture_png(ctx, args.serial)
            destination = resolve_path(args.output, settings=ctx.settings)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(png)
            relative = safe_relpath(destination, ctx.settings).replace("\\", "/")
            await ctx.emitter(ArtifactCreated(path=relative, name=destination.name, kind="image", size_bytes=len(png)))
        except (AndroidError, OSError) as exc:
            return ToolResult.fail(str(exc))
        return ToolResult(content=f"Скриншот сохранён: {relative} ({len(png) // 1024} КБ).")


class AndroidLogcatArgs(BaseModel):
    lines: int = Field(default=100, ge=1, le=1000, description="Сколько последних строк logcat вернуть")
    serial: str = Field(default="", description="Серийный номер устройства; пусто — adb выберет устройство")


class AndroidLogcatTool(Tool):
    name = "android_logcat"
    description = "Возвращает последние строки logcat для поиска ошибок UI и падений Android-приложения."
    Args = AndroidLogcatArgs
    category = "read"

    async def run(self, args: AndroidLogcatArgs, ctx: ToolContext) -> ToolResult:
        command = []
        if args.serial.strip():
            command.extend(["-s", args.serial.strip()])
        command.extend(["logcat", "-d", "-t", str(args.lines)])
        try:
            result = await _adb_run(ctx, command)
        except AndroidError as exc:
            return ToolResult.fail(str(exc))
        return ToolResult(content=result.stdout[-_MAX_OUTPUT:], ok=result.ok)
