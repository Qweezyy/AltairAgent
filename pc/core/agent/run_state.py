"""Персистентный след активного прогона — чтобы пережить падение/перезапуск.

Пока задача идёт, на диске лежит маленький файл-маркер со статусом ``running``.
Штатное завершение (успех, ошибка или отмена — всё это ловит цикл агента) маркер
удаляет. Значит, оставшийся файл ``running`` = процесс умер посреди задачи
(kill, OOM, сбой питания, перезапуск). При следующей загрузке чата это видно, и
пользователю можно предложить продолжить: вся история диалога уже сохранена
отдельно (SessionStore), а здесь хранится лишь минимальный след прогона.

Файлы лежат в данных приложения (data_dir/run_state), НЕ рядом с сессиями:
список чатов и поиск по переписке не должны видеть эти служебные маркеры.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from core.fs_atomic import atomic_write_text
from core.logging_setup import get_logger

logger = get_logger("agent.run_state")


class RunStateStore:
    """Маркеры активных прогонов — по одному файлу на сессию."""

    def __init__(self, data_dir: Path | str) -> None:
        self.dir = Path(data_dir) / "run_state"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        safe = "".join(c for c in (session_id or "") if c.isalnum() or c in "-_")[:64] or "session"
        return self.dir / f"{safe}.json"

    def begin(self, session_id: str, run_id: str, task: str, model: str = "") -> None:
        """Отмечает начало прогона (статус ``running``)."""
        now = time.time()
        self._write(
            session_id,
            {
                "session_id": session_id,
                "run_id": run_id,
                "task": (task or "")[:500],
                "model": model,
                "status": "running",
                "step": 0,
                "started_at": now,
                "updated_at": now,
            },
        )

    def update(self, session_id: str, step: int) -> None:
        """Продвигает счётчик шагов активного прогона (обновляет след)."""
        rec = self.read(session_id)
        if not rec or rec.get("status") != "running":
            return
        rec["step"] = step
        rec["updated_at"] = time.time()
        self._write(session_id, rec)

    def clear(self, session_id: str) -> None:
        """Снимает маркер — прогон завершился штатно (успех/ошибка/отмена)."""
        try:
            self._path(session_id).unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover - редкий сбой ФС
            logger.warning("Не удалось снять маркер прогона %s: %s", session_id, exc)

    def read(self, session_id: str) -> dict[str, Any] | None:
        """Сырой маркер сессии (или None, если его нет / файл битый)."""
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def interrupted(self, session_id: str) -> dict[str, Any] | None:
        """След прерванного прогона этой сессии (или None).

        Оставшийся маркер ``running`` означает, что процесс умер посреди задачи:
        штатный выход всегда снимает маркер через ``clear``.
        """
        rec = self.read(session_id)
        if rec and rec.get("status") == "running":
            return rec
        return None

    def list_interrupted(self) -> list[dict[str, Any]]:
        """Все прерванные прогоны (для сводки на старте), свежие сверху."""
        out: list[dict[str, Any]] = []
        try:
            for f in self.dir.glob("*.json"):
                if f.name.endswith(".tmp"):
                    continue
                try:
                    rec = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if rec.get("status") == "running":
                    out.append(rec)
        except OSError:  # pragma: no cover - редкий сбой ФС
            pass
        out.sort(key=lambda r: r.get("updated_at", 0), reverse=True)
        return out

    def _write(self, session_id: str, rec: dict[str, Any]) -> None:
        path = self._path(session_id)
        try:
            atomic_write_text(path, json.dumps(rec, ensure_ascii=False))
        except OSError as exc:  # pragma: no cover - редкий сбой ФС
            logger.warning("Не удалось записать маркер прогона %s: %s", session_id, exc)
