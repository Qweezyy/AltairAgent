"""Проверка и установка обновлений.

Источник обновлений задаётся в `.env` (`UPDATE_URL`) и может быть:

  * ссылкой на JSON-манифест в интернете (GitHub Releases, свой сайт, S3);
  * путём к такому же файлу на диске или в сетевой папке — удобно, когда
    обновления раздаются внутри команды без публикации в сеть.

Формат манифеста:

    {
      "version": "1.5.0",
      "url": "https://example.com/LocalAIAgent-1.5.0.zip",
      "notes": "Что нового",
      "sha256": "..."          // необязательно, но проверяется, если указан
    }

Почему обновление не распаковывается поверх себя напрямую: работающее
приложение держит свои файлы открытыми, и Windows не даст их заменить.
Поэтому распаковываем рядом, а подмену делает короткий скрипт, который
дожидается выхода приложения.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx

from core.logging_setup import get_logger
from core.settings import Settings, get_settings
from core.version import __version__, is_newer

logger = get_logger("updater")

#: Больше этого не качаем: приложение весит десятки мегабайт, не гигабайты.
MAX_PACKAGE_BYTES = 500 * 1024 * 1024


@dataclass(slots=True)
class UpdateInfo:
    available: bool = False
    current: str = __version__
    version: str = ""
    url: str = ""
    notes: str = ""
    sha256: str = ""
    error: str = ""
    #: Можно ли поставить обновление автоматически.
    installable: bool = False

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "current": self.current,
            "version": self.version,
            "notes": self.notes,
            "error": self.error,
            "installable": self.installable,
        }


@dataclass(slots=True)
class Updater:
    settings: Settings = field(default_factory=get_settings)

    @property
    def is_frozen(self) -> bool:
        """Запущены ли мы как собранное приложение, а не из исходников."""
        return bool(getattr(sys, "frozen", False))

    @property
    def install_dir(self) -> Path:
        """Куда установлено приложение."""
        if self.is_frozen:
            return Path(sys.executable).parent
        return self.settings.app_dir

    # ------------------------------------------------------------ проверка

    async def check(self) -> UpdateInfo:
        source = self.settings.update_url.strip()
        if not source:
            return UpdateInfo(
                error=(
                    "Источник обновлений не настроен. Укажите UPDATE_URL в .env — "
                    "ссылку на манифест или путь к файлу в общей папке."
                )
            )

        try:
            manifest = await self._load_manifest(source)
        except Exception as exc:  # noqa: BLE001 - причину показываем пользователю
            logger.warning("Проверка обновлений не удалась: %s", exc)
            return UpdateInfo(error=f"Не удалось проверить обновления: {exc}")

        version = str(manifest.get("version") or "").strip()
        if not version:
            return UpdateInfo(error="В манифесте нет поля version.")

        info = UpdateInfo(
            available=is_newer(version),
            version=version,
            url=str(manifest.get("url") or "").strip(),
            notes=str(manifest.get("notes") or "").strip(),
            sha256=str(manifest.get("sha256") or "").strip().lower(),
        )
        # Ставить автоматически можно только собранное приложение: рядом с
        # исходниками правильнее обновляться через git.
        info.installable = bool(info.available and info.url and self.is_frozen)
        return info

    async def _load_manifest(self, source: str) -> dict:
        if urlparse(source).scheme in ("http", "https"):
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                response = await client.get(source)
                response.raise_for_status()
                return response.json()

        def read_local() -> dict:
            path = Path(source).expanduser()
            if not path.exists():
                raise FileNotFoundError(f"файл манифеста не найден: {path}")
            return json.loads(path.read_text(encoding="utf-8"))

        # Сетевая папка может отвечать секундами — читаем вне event loop.
        return await asyncio.to_thread(read_local)

    # ------------------------------------------------------------ установка

    async def download(self, info: UpdateInfo) -> Path:
        """Скачивает пакет обновления во временную папку."""
        target = Path(tempfile.gettempdir()) / f"{info.version}-update.zip"

        if urlparse(info.url).scheme in ("http", "https"):
            handle = await asyncio.to_thread(open, target, "wb")
            try:
                async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
                    async with client.stream("GET", info.url) as response:
                        response.raise_for_status()
                        total = 0
                        async for chunk in response.aiter_bytes(65536):
                            total += len(chunk)
                            if total > MAX_PACKAGE_BYTES:
                                raise ValueError("пакет обновления подозрительно большой")
                            await asyncio.to_thread(handle.write, chunk)
            finally:
                await asyncio.to_thread(handle.close)
        else:
            # Копирование из сетевой папки может длиться минуты — в поток.
            await asyncio.to_thread(_copy_local, info.url, target)

        if info.sha256:
            digest = await asyncio.to_thread(lambda: hashlib.sha256(target.read_bytes()).hexdigest())
            if digest != info.sha256:
                target.unlink(missing_ok=True)
                raise ValueError("контрольная сумма не совпала — файл повреждён или подменён")

        return target

    def unpack(self, archive: Path) -> Path:
        """Распаковывает пакет и возвращает папку с новой версией."""
        staging = Path(tempfile.gettempdir()) / f"{archive.stem}-unpacked"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)

        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.namelist():
                # Защита от архивов с путями вида ../../windows/system32
                destination = (staging / member).resolve()
                if not str(destination).startswith(str(staging.resolve())):
                    raise ValueError(f"архив содержит небезопасный путь: {member}")
            bundle.extractall(staging)

        # Если внутри одна папка — считаем её корнем приложения.
        entries = [item for item in staging.iterdir()]
        if len(entries) == 1 and entries[0].is_dir():
            return entries[0]
        return staging

    def apply(self, new_version_dir: Path) -> None:
        """Заменяет файлы и перезапускает приложение.

        Подмену делает отдельный скрипт: работающая программа не может
        переписать собственный exe, пока он запущен.
        """
        if not self.is_frozen:
            raise RuntimeError(
                "Автообновление доступно только для собранного приложения. "
                "В режиме исходников обновляйтесь через git pull."
            )

        script = Path(tempfile.gettempdir()) / "agent_update.bat"
        exe = Path(sys.executable)
        script.write_text(
            "@echo off\r\n"
            "chcp 65001 >nul\r\n"
            f"echo Обновление {APP_TITLE}...\r\n"
            f':wait\r\n'
            f'tasklist /fi "PID eq {os.getpid()}" | find "{os.getpid()}" >nul\r\n'
            "if not errorlevel 1 (\r\n"
            "  timeout /t 1 /nobreak >nul\r\n"
            "  goto wait\r\n"
            ")\r\n"
            f'robocopy "{new_version_dir}" "{self.install_dir}" /E /IS /R:2 /W:1 >nul\r\n'
            f'start "" "{exe}"\r\n'
            f'rmdir /s /q "{new_version_dir}" 2>nul\r\n',
            encoding="utf-8",
        )

        subprocess.Popen(  # noqa: S603 - собственный скрипт во временной папке
            ["cmd", "/c", str(script)],
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        logger.info("Обновление подготовлено, приложение будет перезапущено")


def _copy_local(source: str, target: Path) -> None:
    """Копирует пакет обновления из локальной или сетевой папки."""
    shutil.copy2(Path(source).expanduser(), target)


APP_TITLE = "Локальный ИИ-агент"
