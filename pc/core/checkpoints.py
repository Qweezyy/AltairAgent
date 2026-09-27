"""Снимки файлов перед изменением и откат правок.

Зачем: агент редактирует файлы сам, и это главный источник недоверия — «а вдруг
он затрёт что-то нужное». Снимок перед каждым изменением и откат в один клик
снимают этот страх: любую правку можно отменить.

Как устроено: перед записью/правкой/удалением файла его прежнее содержимое
копируется в blob рядом с манифестом (в папке данных приложения, не в проекте).
Снимки складываются в стопку по каждому файлу — откат снимает последний, потом
предыдущий и так далее, давая многоуровневый undo. Хранятся байты, а не текст,
поэтому переживают любой файл.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from core.fs_atomic import safe_replace
from core.logging_setup import get_logger

logger = get_logger("checkpoints")

#: Файлы крупнее не снимаем: обещать откат гигабайтного файла — врать про диск.
#: Для агента, правящего код, это с огромным запасом.
MAX_BLOB_BYTES = 25 * 1024 * 1024

#: Сколько снимков держим на сессию. Дальше вытесняем самые старые.
MAX_CHECKPOINTS = 300


@dataclass(slots=True)
class Checkpoint:
    """Один снимок файла перед изменением."""

    id: str
    path: str  # относительный путь в рабочей папке
    op: str  # write | edit | patch | delete
    existed: bool  # существовал ли файл до изменения
    recoverable: bool  # сохранён ли blob (крупные файлы не снимаем)
    size: int
    ts: float
    #: id прогона, в котором сделан снимок — чтобы откатить весь прогон целиком.
    #: Пусто у старых снимков (до появления поля) — они не привязаны к прогону.
    run_id: str = ""

    def to_public(self) -> dict:
        """Для интерфейса: без внутренних деталей хранения."""
        return {"id": self.id, "path": self.path, "op": self.op,
                "recoverable": self.recoverable, "run_id": self.run_id}


class CheckpointStore:
    """A per-session stack of file snapshots."""

    def __init__(self, base_dir: Path, session_id: str, workspace: Path) -> None:
        safe_id = "".join(c for c in session_id if c.isalnum() or c in ("-", "_")) or "local"
        self.dir = base_dir / "checkpoints" / safe_id
        self.workspace = workspace.resolve()
        self._manifest = self.dir / "manifest.json"
        # Tools run in parallel worker threads, so two edits can snapshot at the same time.
        # Every read-modify-write of the records and the manifest goes through this lock;
        # without it a concurrent save could drop a snapshot, and "roll back the run"
        # would silently skip that file. Re-entrant: restores call _save() while holding it.
        self._lock = threading.RLock()
        self._records: list[Checkpoint] = self._load()
        #: The active run: new snapshots are tagged with it (for whole-run rollback).
        self._run_id = ""

    # ------------------------------------------------------------------

    def set_run(self, run_id: str) -> None:
        """Mark the active run: its id is stamped on new snapshots."""
        self._run_id = str(run_id or "")

    def snapshot(self, abs_path: Path, rel_path: str, op: str) -> Checkpoint:
        """Snapshot the file's current state before it is changed."""
        abs_path = abs_path.resolve()
        existed = abs_path.is_file()
        recoverable = True
        size = 0
        checkpoint_id = uuid.uuid4().hex[:12]

        if existed:
            try:
                size = abs_path.stat().st_size
            except OSError:
                size = 0
            if size > MAX_BLOB_BYTES:
                # Too large: record that it changed, but do not promise a rollback.
                recoverable = False
            else:
                try:
                    self.dir.mkdir(parents=True, exist_ok=True)
                    (self.dir / f"{checkpoint_id}.blob").write_bytes(abs_path.read_bytes())
                except OSError:
                    logger.debug("Не удалось снять снимок %s", abs_path, exc_info=True)
                    recoverable = False

        checkpoint = Checkpoint(
            id=checkpoint_id,
            path=rel_path,
            op=op,
            existed=existed,
            recoverable=recoverable,
            size=size,
            ts=time.time(),
            run_id=self._run_id,
        )
        self._append(checkpoint)
        return checkpoint

    def record_prior(
        self, rel_path: str, op: str, prior_bytes: bytes | None, existed: bool
    ) -> Checkpoint:
        """Register a snapshot whose prior content is given EXPLICITLY.

        Used to roll back shell commands: by the time it is registered the file has
        already changed, so the prior state comes from outside (the shadow git) instead
        of being read from disk as in `snapshot`. `existed=False` — the command created
        the file (rollback = delete it); `existed=True` with `prior_bytes` — it was
        changed or deleted (rollback = put it back).
        """
        checkpoint_id = uuid.uuid4().hex[:12]
        recoverable = True
        size = len(prior_bytes) if prior_bytes is not None else 0
        if existed and prior_bytes is not None:
            if size > MAX_BLOB_BYTES:
                recoverable = False
            else:
                try:
                    self.dir.mkdir(parents=True, exist_ok=True)
                    (self.dir / f"{checkpoint_id}.blob").write_bytes(prior_bytes)
                except OSError:
                    logger.debug("Не удалось записать снимок команды", exc_info=True)
                    recoverable = False

        checkpoint = Checkpoint(
            id=checkpoint_id,
            path=rel_path,
            op=op,
            existed=existed,
            recoverable=recoverable,
            size=size,
            ts=time.time(),
            run_id=self._run_id,
        )
        self._append(checkpoint)
        return checkpoint

    def restore_latest(self, rel_path: str) -> str:
        """Откатывает последнее изменение указанного файла.

        Возвращает описание того, что сделано. Кидает LookupError, если снимков
        для файла нет, и RuntimeError, если снимок нерушим (крупный файл).
        """
        with self._lock:
            for index in range(len(self._records) - 1, -1, -1):
                checkpoint = self._records[index]
                if checkpoint.path != rel_path:
                    continue
                if not checkpoint.recoverable:
                    raise RuntimeError(
                        f"Снимок «{rel_path}» не сохранён (файл слишком большой) — откат невозможен."
                    )
                message = self._apply_restore(checkpoint)
                # The snapshot is used up: the next undo goes one level deeper.
                self._records.pop(index)
                self._drop_blob(checkpoint.id)
                self._save()
                return message

        raise LookupError(f"Нет сохранённых изменений для «{rel_path}».")

    def run_manifest(self, run_id: str) -> dict:
        """Аудит прогона: какие файлы затронуты, сколько снимков, можно ли откатить.

        Возвращает {run_id, files:[{path, op, count, recoverable}], count, recoverable}.
        Пустой список files — откатывать нечего (снимков прогона нет).
        """
        by_path: dict[str, dict] = {}
        for cp in self.records():
            if cp.run_id != run_id:
                continue
            entry = by_path.setdefault(cp.path, {"path": cp.path, "op": cp.op, "count": 0, "recoverable": True})
            entry["count"] += 1
            entry["op"] = cp.op  # последняя операция по файлу
            if not cp.recoverable:
                entry["recoverable"] = False
        files = sorted(by_path.values(), key=lambda e: e["path"])
        total = sum(e["count"] for e in files)
        return {
            "run_id": run_id,
            "files": files,
            "count": total,
            "recoverable": all(e["recoverable"] for e in files) if files else True,
        }

    def restore_run(self, run_id: str) -> dict:
        """Откатывает ВСЕ изменения прогона: файлы возвращаются к состоянию до него.

        Снимки прогона применяются от самых поздних к ранним (так многоуровневый
        стек по каждому файлу разворачивается до состояния перед первой правкой
        прогона) и «расходуются». Возвращает {restored:[пути], messages, skipped}.
        Нерушимые снимки (крупные файлы) пропускаются с пометкой.
        """
        if not run_id:
            raise LookupError("Не задан прогон для отката.")
        with self._lock:
            indices = [i for i in range(len(self._records) - 1, -1, -1)
                       if self._records[i].run_id == run_id]
            if not indices:
                raise LookupError("Для этого прогона нет сохранённых изменений.")

            restored: list[str] = []
            messages: list[str] = []
            skipped: list[str] = []
            for index in indices:  # already newest to oldest
                checkpoint = self._records[index]
                if not checkpoint.recoverable:
                    skipped.append(checkpoint.path)
                    continue
                try:
                    messages.append(self._apply_restore(checkpoint))
                except RuntimeError as exc:
                    skipped.append(checkpoint.path)
                    messages.append(str(exc))
                    continue
                if checkpoint.path not in restored:
                    restored.append(checkpoint.path)
                self._records.pop(index)
                self._drop_blob(checkpoint.id)
            self._save()
        return {"restored": restored, "messages": messages, "skipped": skipped}

    def latest_for(self, rel_path: str) -> Checkpoint | None:
        for checkpoint in reversed(self.records()):
            if checkpoint.path == rel_path:
                return checkpoint
        return None

    def records(self) -> list[Checkpoint]:
        with self._lock:
            return list(self._records)

    # ------------------------------------------------------------------

    def _append(self, checkpoint: Checkpoint) -> None:
        """Add a snapshot and persist the manifest as one atomic step."""
        with self._lock:
            self._records.append(checkpoint)
            self._evict_overflow()
            self._save()

    def _apply_restore(self, checkpoint: Checkpoint) -> str:
        target = self._resolve(checkpoint.path)

        if not checkpoint.existed:
            # Файла не было до изменения — откат означает удалить созданное.
            if target.exists():
                target.unlink()
            return f"Отменено создание «{checkpoint.path}» — файл удалён."

        blob = self.dir / f"{checkpoint.id}.blob"
        if not blob.is_file():
            raise RuntimeError(f"Снимок «{checkpoint.path}» потерян — откат невозможен.")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob.read_bytes())
        return f"Файл «{checkpoint.path}» возвращён к состоянию до правки."

    def _resolve(self, rel_path: str) -> Path:
        """Абсолютный путь внутри рабочей папки. Защита от выхода наружу."""
        candidate = (self.workspace / rel_path).resolve()
        if candidate != self.workspace and self.workspace not in candidate.parents:
            raise RuntimeError(f"Путь снимка «{rel_path}» вне рабочей папки — откат отклонён.")
        return candidate

    def _evict_overflow(self) -> None:
        while len(self._records) > MAX_CHECKPOINTS:
            oldest = self._records.pop(0)
            self._drop_blob(oldest.id)

    def _drop_blob(self, checkpoint_id: str) -> None:
        try:
            (self.dir / f"{checkpoint_id}.blob").unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            pass

    def _load(self) -> list[Checkpoint]:
        try:
            data = json.loads(self._manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        records = []
        for item in data if isinstance(data, list) else []:
            try:
                records.append(Checkpoint(**item))
            except TypeError:
                continue
        return records

    def _save(self) -> None:
        # A unique temp name per save: even two store instances for the same session
        # never write into the same temp file.
        temp = self.dir / f"manifest.{uuid.uuid4().hex[:8]}.tmp"
        with self._lock:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                temp.write_text(
                    json.dumps([asdict(r) for r in self._records], ensure_ascii=False),
                    encoding="utf-8",
                )
                safe_replace(temp, self._manifest)
            except OSError:  # pragma: no cover
                # Not harmless: the manifest drives rollback, so say it loudly.
                logger.warning("Could not save the checkpoint manifest", exc_info=True)
                temp.unlink(missing_ok=True)
