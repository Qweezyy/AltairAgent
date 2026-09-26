"""Персистентное хранилище сессий и истории диалогов на диске."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from core.agent.session import Session
from core.fs_atomic import safe_replace
from core.logging_setup import get_logger
from core.settings import Settings, get_settings

logger = get_logger("agent.storage")


class SessionStore:
    """Управляет сохранением и загрузкой сессий из JSON-файлов."""

    def __init__(self, base_dir: Path | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        # Чаты хранятся в папке ПРИЛОЖЕНИЯ, а не в рабочей папке проекта:
        # иначе при переключении папки список чатов пропадал бы, а в чужом
        # проекте появлялась бы директория storage/.
        self.storage_dir = (base_dir or self.settings.storage_dir).resolve()
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _file_path(self, session_id: str) -> Path:
        safe_id = "".join(c for c in session_id if c.isalnum() or c in ("-", "_"))
        return self.storage_dir / f"{safe_id}.json"

    def save(self, session: Session) -> None:
        """Синхронное сохранение сессии в файл."""
        path = self._file_path(session.id)
        try:
            temp_path = path.with_suffix(".tmp")
            content = json.dumps(session.to_dict(), ensure_ascii=False, indent=2)
            temp_path.write_text(content, encoding="utf-8")
            safe_replace(temp_path, path)
        except OSError as exc:
            logger.warning("Не удалось сохранить сессию %s: %s", session.id, exc)

    async def async_save(self, session: Session) -> None:
        """Асинхронная обёртка для безопасного сохранения без блокировки event loop."""
        await asyncio.to_thread(self.save, session)

    def load(self, session_id: str) -> Session | None:
        """Загрузка сессии по её ID."""
        path = self._file_path(session_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Session.from_dict(data)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Ошибка чтения файла сессии %s: %s", session_id, exc)
            return None

    async def async_load(self, session_id: str) -> Session | None:
        return await asyncio.to_thread(self.load, session_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        """Возвращает список всех сохранённых сессий, отсортированных по дате изменения."""
        results = []
        try:
            for file in self.storage_dir.glob("*.json"):
                if file.name.endswith(".tmp"):
                    continue
                try:
                    data = json.loads(file.read_text(encoding="utf-8"))
                    results.append(
                        {
                            "id": data.get("id", file.stem),
                            "title": data.get("title", "Диалог"),
                            "workspace": data.get("workspace") or str(self.settings.workspace),
                            "model": data.get("model", ""),
                            "created_at": data.get("created_at", 0),
                            "updated_at": data.get("updated_at", 0),
                            "message_count": len(data.get("messages", [])),
                        }
                    )
                except (OSError, json.JSONDecodeError):
                    continue
        except OSError as exc:
            logger.warning("Ошибка чтения каталога сессий: %s", exc)

        results.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
        return results

    async def async_list(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.list_sessions)

    #: Корзина: удалённые чаты не стираются сразу, а переезжают сюда — чтобы
    #: случайное удаление можно было отменить (см. restore/list_trashed).
    _TRASH_TTL_DAYS = 30

    @property
    def _trash_dir(self) -> Path:
        return self.storage_dir / ".trash"

    def delete(self, session_id: str) -> bool:
        """Мягкое удаление: файл сессии переезжает в корзину, а не стирается.

        Так случайно удалённый чат можно вернуть (restore). Старое содержимое
        корзины периодически чистится (_TRASH_TTL_DAYS)."""
        path = self._file_path(session_id)
        if not path.exists():
            return False
        try:
            self._trash_dir.mkdir(parents=True, exist_ok=True)
            self._purge_trash()
            safe_id = "".join(c for c in session_id if c.isalnum() or c in ("-", "_"))
            dest = self._trash_dir / f"{safe_id}.json"
            safe_replace(path, dest)   # атомарно, переживает резкое выключение
            return True
        except OSError as exc:
            logger.warning("Не удалось удалить сессию %s: %s", session_id, exc)
            return False

    def _purge_trash(self) -> None:
        """Удаляет из корзины файлы старше _TRASH_TTL_DAYS."""
        import time

        cutoff = time.time() - self._TRASH_TTL_DAYS * 86400
        try:
            for file in self._trash_dir.glob("*.json"):
                try:
                    if file.stat().st_mtime < cutoff:
                        file.unlink()
                except OSError:
                    continue
        except OSError:
            pass

    def list_trashed(self) -> list[dict[str, Any]]:
        """Недавно удалённые чаты (для восстановления)."""
        results: list[dict[str, Any]] = []
        try:
            for file in self._trash_dir.glob("*.json"):
                try:
                    data = json.loads(file.read_text(encoding="utf-8"))
                    results.append({
                        "id": data.get("id", file.stem),
                        "title": data.get("title", "Диалог"),
                        "message_count": len(data.get("messages", [])),
                        "updated_at": data.get("updated_at", 0),
                        "deleted_at": file.stat().st_mtime,
                    })
                except (OSError, json.JSONDecodeError):
                    continue
        except OSError:
            pass
        results.sort(key=lambda x: x.get("deleted_at", 0), reverse=True)
        return results

    def restore(self, session_id: str) -> bool:
        """Возвращает чат из корзины обратно в список."""
        safe_id = "".join(c for c in session_id if c.isalnum() or c in ("-", "_"))
        src = self._trash_dir / f"{safe_id}.json"
        if not src.exists():
            return False
        try:
            safe_replace(src, self._file_path(session_id))
            return True
        except OSError as exc:
            logger.warning("Не удалось восстановить сессию %s: %s", session_id, exc)
            return False

    async def async_delete(self, session_id: str) -> bool:
        return await asyncio.to_thread(self.delete, session_id)

    async def async_list_trashed(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.list_trashed)

    async def async_restore(self, session_id: str) -> bool:
        return await asyncio.to_thread(self.restore, session_id)
