"""WebSocket-транспорт: привязка веб-интерфейса к AgentRunner и Session.

Каждое подключение обслуживает одну сессию диалога. Поддерживаются:
- стриминг рассуждений и ответов (TextDelta, ReasoningDelta);
- отправка прогресса выполнения инструментов и планов (Manus-style);
- подтверждения опасных операций (ApprovalRequest);
- переключение и сохранение сессий в storage/sessions/;
- переключение рабочей директории (workspace) для изоляции проектов.
"""

from __future__ import annotations

import asyncio
import base64
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from core.agent.run_options import RunOptions
from core.agent.run_state import RunStateStore
from core.agent.runner import AgentRunner
from core.agent.session import Session
from core.agent.storage import SessionStore
from core.checkpoints import CheckpointStore
from core.errors import AgentError, ConfigError
from core.events import (
    ArtifactCreated,
    CheckpointRestored,
    Event,
    PlanUpdate,
    RunFailed,
    RunFinished,
    RunStarted,
    ShowFile,
    ShowHtml,
    ShowImage,
    TextDelta,
    ToolFinished,
    ToolStarted,
)
from core.i18n import set_ui_language, tr
from core.llm import build_llm_client
from core.logging_setup import get_logger
from core.memory import MemoryStore
from core.security.permissions import MODES, PermissionStore, mode_catalog
from core.settings import get_settings
from core.tools.base import ApprovalRequest
from core.tools.registry import ToolRegistry
from core.version import __version__
from server.browser_ws import BrowserChannel

#: Session's default title (core/agent/session.py); the UI shows it localised as "New chat".
DEFAULT_TITLE = "Новый диалог"

logger = get_logger("server.ws")

#: Адреса, которые считаем локальным веб-интерфейсом, а не удалённым устройством
#: по мосту. Единый источник правды: server/app.py импортирует этот же набор для
#: авторизации /ws. "testclient" — sentinel-хост Starlette TestClient (реальные
#: клиенты присылают сюда IP-адрес, не эту строку), в проде он ничего не ослабляет.
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}

#: Потолок на один файл, идущий по мосту (get_file/put_file). Больше — отказ.
MAX_FILE_BYTES = 25 * 1024 * 1024

#: Сколько файлов максимум перечисляем в list_files, чтобы не залить телефон.
MAX_LIST_ITEMS = 500


def _resolve_in_workspace(workspace: str | Path, rel: str) -> Path:
    """Резолвит относительный путь строго внутри рабочей папки (песочница).

    Raises:
        ValueError: путь выходит за пределы workspace.
    """
    base = Path(workspace).resolve()
    target = (base / rel).resolve()
    if base != target and base not in target.parents:
        raise ValueError(f"путь вне рабочей папки: {rel}")
    return target


def _iso(ts: float) -> str:
    """Время файла как ISO-строка (телефон показывает её как есть)."""
    try:
        return datetime.fromtimestamp(ts).isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return ""


def _kind(name: str) -> str:
    """Тип файла по расширению — телефону, чтобы выбрать превью."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in {"png", "jpg", "jpeg", "gif", "webp", "bmp", "heic"}:
        return "image"
    if ext in {
        "txt", "md", "json", "csv", "xml", "yaml", "yml", "py", "kt", "java",
        "js", "ts", "html", "css", "log", "sh", "toml", "ini", "gradle",
    }:
        return "text"
    return "file"


class Connection:
    """Одно WebSocket-подключение = одна активная сессия агента."""

    def __init__(self, websocket: WebSocket, registry: ToolRegistry) -> None:
        self.ws = websocket
        self.registry = registry
        self.settings = get_settings()
        self.store = SessionStore(settings=self.settings)
        # Новый чат по умолчанию получает свою персональную папку файлов —
        # агент может писать туда заметки/файлы, и они всегда доступны в этом чате.
        self.session = Session()
        chat_dir = self.settings.chat_files_dir(self.session.id)
        self.session.workspace = str(chat_dir)
        try:
            self.session_settings = self.settings.for_workspace(chat_dir)
        except ConfigError:
            self.session_settings = self.settings
        self.outbox: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=2000)
        self.run_task: asyncio.Task | None = None
        self.pending_approvals: dict[str, asyncio.Future[str]] = {}
        # Подтверждения показываем по одному: параллельные инструменты
        # иначе завалили бы экран карточками разом.
        self._approval_lock = asyncio.Lock()
        self.permissions = PermissionStore(settings=self.settings)
        #: Аргументы запущенных инструментов — нужны, когда придёт результат.
        self._tool_args: dict[str, dict[str, Any]] = {}
        #: Накопитель текста ответа между раундами инструментов — чтобы в истории
        #: раунды разделялись так же, как вживую (текст → инструменты → текст → …).
        self._text_seg: str = ""
        #: Были ли в текущем ответе инлайн-вставки (медиа/виджет). Если да — финальный
        #: текст уже разложен по сегментам, и целиком его как «answer» писать нельзя.
        self._turn_had_inline: bool = False
        #: Память инструментов сессии: сюда ask кладёт ожидание ответа.
        self._scratch: dict[str, Any] = {}
        #: The new chat's title, asked from the model in parallel with its first answer.
        self._title_task: asyncio.Task[None] | None = None
        self._pending_title: str | None = None
        self._title_for_new_chat = False
        self._retitle_tried: set[str] = set()
        self._writer: asyncio.Task | None = None
        #: Мост к телефону. Ожидания обратных запросов (need_file/need_capability)
        #: по req_id — резолвятся, когда телефон пришлёт ответ.
        self._bridge_waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        #: Что рассказал о себе собеседник в hello (platform, capabilities).
        self.peer: dict[str, Any] = {}
        #: Удалённое подключение (не localhost) = телефон через мост.
        self._is_remote = self._detect_remote()
        #: Даём инструментам моста (phone_*) доступ к этому соединению.
        self._scratch["_bridge"] = self
        #: The browser panel (embedded tabs host or screencast fallback).
        self.browser = BrowserChannel(self.send)

    def _detect_remote(self) -> bool:
        """Подключение пришло не с localhost — значит, это телефон через мост."""
        try:
            host = (self.ws.client.host if self.ws.client else "") or ""
        except AttributeError:
            return False
        return host not in _LOOPBACK_HOSTS

    @property
    def phone_connected(self) -> bool:
        """Есть ли на том конце телефон, которому можно слать обратные запросы.

        Телефон представляется через `hello`, но даже без него удалённый сокет
        (не localhost) — это устройство по мосту, а веб-интерфейс всегда локален.
        """
        return self._is_remote or self.peer.get("platform") == "android"

    # ---------------------------------------------------------------- io

    async def serve(self) -> None:
        await self.ws.accept()
        self._register_connection()
        self._writer = asyncio.create_task(self._writer_loop(), name="ws-writer")
        await self._send_ready()
        await self._send_context_usage()
        try:
            while True:
                message = await self.ws.receive_json()
                await self._handle_message(message)
        except WebSocketDisconnect:
            logger.info("Клиент отключился (сессия %s).", self.session.id)
            await self._save_session()
        except (ValueError, TypeError) as exc:
            logger.warning("Некорректное сообщение от клиента: %s", exc)
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        # Stop the panel's screencast and hand the embedded browser back — otherwise
        # frames and host requests would keep going to a closed socket.
        await self.browser.close()
        if self.run_task and not self.run_task.done():
            self.run_task.cancel()
            await asyncio.gather(self.run_task, return_exceptions=True)
        for future in self.pending_approvals.values():
            if not future.done():
                future.set_result("deny")
        self.pending_approvals.clear()
        self._cancel_questions()
        self._cancel_bridge_waiters()
        self._unregister_connection()
        await self._save_session()
        await self.outbox.put(None)
        if self._writer:
            await asyncio.gather(self._writer, return_exceptions=True)

    async def _writer_loop(self) -> None:
        while True:
            payload = await self.outbox.get()
            if payload is None:
                return
            try:
                await self.ws.send_json(payload)
            except (WebSocketDisconnect, RuntimeError):
                return
            except Exception:  # noqa: BLE001
                logger.debug("Не удалось отправить сообщение в сокет", exc_info=True)
                return

    async def send(self, payload: dict[str, Any]) -> None:
        try:
            self.outbox.put_nowait(payload)
        except asyncio.QueueFull:
            logger.warning("Очередь отправки переполнена — событие отброшено.")

    async def emit(self, event: Event) -> None:
        if isinstance(event, RunStarted):
            await self._on_run_started()
        if isinstance(event, PlanUpdate):
            self.session.set_plan([s.model_dump() for s in event.steps])
            await self._save_session()
        elif isinstance(event, ArtifactCreated):
            self.session.add_artifact(event.model_dump())
            await self._save_session()
        else:
            self._record_timeline(event)

        await self.send(event.model_dump(mode="json"))

    def _record_timeline(self, event: Event) -> None:
        """Пишет ход работы в сессию, чтобы открытый заново чат выглядел так же.

        Поле messages для этого не годится: это формат модели, по нему не
        восстановить, какие инструменты вызывались и чем закончились.
        """
        if isinstance(event, TextDelta):
            # Копим текущий сегмент ответа; он станет либо промежуточным «text»
            # перед следующим раундом инструментов, либо финальным «answer».
            self._text_seg += event.text or ""
        elif isinstance(event, ToolFinished):
            self.session.append_timeline(
                {
                    "kind": "step",
                    "name": event.name,
                    "args": _jsonable(self._tool_args.pop(event.call_id, {})),
                    "ok": event.ok,
                    "duration_ms": event.duration_ms,
                    # Полный вывод в истории не нужен: он уже отдан модели,
                    # а файл сессии не должен пухнуть до мегабайтов.
                    "output": event.output[:4000],
                    "ts": event.ts,
                }
            )
        elif isinstance(event, ToolStarted):
            self._tool_args[event.call_id] = event.args
            # Начинается раунд инструментов: если перед ним был текст — фиксируем его
            # отдельной записью, чтобы в истории раунды разделялись как вживую.
            seg = self._text_seg.strip()
            if seg:
                self.session.append_timeline({"kind": "text", "text": seg, "ts": event.ts})
            self._text_seg = ""
        elif isinstance(event, (ShowImage, ShowHtml, ShowFile)):
            # Инлайн-вставка в ответе: сначала фиксируем накопленный текст отдельным
            # сегментом (чтобы медиа/виджет встал ровно на своё место в истории),
            # затем саму вставку. Так перезагруженный чат сохраняет расположение.
            seg = self._text_seg.strip()
            if seg:
                self.session.append_timeline({"kind": "text", "text": seg, "ts": event.ts})
            self._text_seg = ""
            self._turn_had_inline = True
            self.session.append_timeline(self._inline_entry(event))
        elif isinstance(event, RunFinished):
            # Если ответ содержал инлайн-вставки, его текст уже разложен по сегментам —
            # как финальный «answer» пишем только незафиксированный хвост, иначе весь
            # текст задублируется поверх уже сохранённых сегментов.
            trailing = self._text_seg.strip()
            self._text_seg = ""
            answer_text = trailing if self._turn_had_inline else event.text
            self._turn_had_inline = False
            self.session.append_timeline(
                {
                    "kind": "answer",
                    "text": answer_text,
                    "full": event.text,
                    "steps": event.steps,
                    "duration_ms": event.duration_ms,
                    "usage": event.usage,
                    "cost_usd": event.cost_usd,
                    "run_id": event.run_id,  # для «откатить прогон» из истории
                    "ts": event.ts,
                }
            )
        elif isinstance(event, RunFailed):
            self.session.append_timeline({"kind": "error", "text": event.message, "ts": event.ts})

    @staticmethod
    def _inline_entry(event: ShowImage | ShowHtml | ShowFile) -> dict[str, Any]:
        """Запись инлайн-вставки для истории — тот же формат, что уходит в UI."""
        if isinstance(event, ShowImage):
            return {"kind": "image", "path": event.path, "name": event.name,
                    "caption": event.caption, "ts": event.ts}
        if isinstance(event, ShowHtml):
            return {"kind": "widget", "html": event.html, "caption": event.caption,
                    "widget_kind": event.kind, "ts": event.ts}
        return {"kind": "media", "path": event.path, "name": event.name,
                "caption": event.caption, "media_kind": event.kind,
                "size_bytes": event.size_bytes, "ts": event.ts}

    async def _save_session(self) -> None:
        self.session.workspace = str(self.session_settings.workspace)
        if self.session.messages:
            await self.store.async_save(self.session)

    async def _send_ready(self) -> None:
        await self.send(
            {
                "type": "ready",
                "version": __version__,
                "session_id": self.session.id,
                "session": self.session.to_dict(),
                "model": self.settings.default_model,
                "approval_mode": self.session_settings.approval_mode,
                "modes": mode_catalog(),
                "workspace": str(self.session_settings.workspace),
                "tools": [
                    {"name": tool.name, "description": tool.description, "dangerous": tool.dangerous}
                    for tool in self.registry.all()
                ],
                "warnings": self.settings.problems(),
            }
        )

    async def _state(self, state: str) -> None:
        await self.send({"type": "state", "state": state})

    async def _send_context_usage(self) -> None:
        """Сколько токенов сейчас занимает контекст чата — для кольца у строки ввода."""
        try:
            estimate = self.session.token_estimate()
        except Exception:  # noqa: BLE001 - the ring must never break anything
            return
        # The provider's own count from the latest request is exact (it includes the system
        # prompt and the tool schemas, which the estimate does not see); the estimate is for
        # a chat that has not talked to a model yet.
        exact = self.session.context_tokens
        await self.send({"type": "context.usage", "tokens": exact or estimate, "exact": bool(exact)})

    # ----------------------------------------------------------- команды

    async def _handle_message(self, message: dict[str, Any]) -> None:
        kind = str(message.get("type") or "run")

        if kind == "run":
            await self._start_run(message)
        elif kind == "stop":
            await self._stop_run()
        elif kind == "reset":
            self.session.reset()
            await self._save_session()
            await self.send({"type": "log", "level": "info", "text": tr("ws.history_cleared")})
        elif kind == "load_session":
            await self._load_session(str(message.get("session_id") or ""))
        elif kind == "rename_session":
            await self._rename_session(str(message.get("session_id") or ""), str(message.get("title") or ""))
        elif kind == "new_session":
            await self._new_session(message)
        elif kind == "fork_session":
            await self._fork_session(message)
        elif kind == "rewind":
            await self._rewind(message)
        elif kind == "set_workspace":
            await self._set_workspace(str(message.get("workspace") or ""))
        elif kind == "set_mode":
            await self._set_approval_mode(str(message.get("mode") or ""))
        elif kind == "answer":
            self._resolve_question(message)
        elif kind == "approval":
            self._resolve_approval(message)
        elif kind == "restore_checkpoint":
            await self._restore_checkpoint(str(message.get("path") or ""))
        elif kind == "run_audit":
            await self._run_audit(str(message.get("run_id") or ""))
        elif kind == "rollback_run":
            await self._rollback_run(str(message.get("run_id") or ""))
        elif kind == "resume_run":
            await self._resume_run()
        elif kind == "dismiss_interrupted":
            await self._dismiss_interrupted()
        elif kind == "ui_lang":
            # Texts the server shows (approvals, the run log, errors) follow the UI.
            set_ui_language(str(message.get("lang") or "en"))
        elif kind == "ping":
            await self.send({"type": "pong"})
        # --- The shared browser panel (server/browser_ws.py) ---
        elif isinstance(kind, str) and kind.startswith("browser_"):
            self.browser.handle(message)
        elif kind == "handoff_done":
            self._resolve_handoff(message)
        # --- Мост к телефону: слой 1-2 (сервер отвечает напрямую, без агента) ---
        elif kind == "hello":
            await self._bridge_hello(message)
        elif kind == "list_files":
            await self._bridge_list_files(message)
        elif kind == "stat_file":
            await self._bridge_stat_file(message)
        elif kind == "get_file":
            await self._bridge_get_file(message)
        elif kind == "put_file":
            await self._bridge_put_file(message)
        elif kind == "sync_memory":
            await self._bridge_sync_memory(message)
        elif kind == "mcp_list":
            await self._bridge_mcp_list(message)
        elif kind == "skills_list":
            await self._bridge_skills_list(message)
        elif kind == "skill_get":
            await self._bridge_skill_get(message)
        # --- Мост: слой 3 — ответы телефона на обратные запросы агента ---
        elif kind in ("need_file.done", "need_file.cancel", "capability.result"):
            self._bridge_resolve(message)
        else:
            await self.send({"type": "log", "level": "warning", "text": tr("ws.unknown_command", kind=kind)})

    async def _load_session(self, session_id: str) -> None:
        if not session_id:
            return
        loaded = await self.store.async_load(session_id)
        if not loaded:
            await self.send({"type": "log", "level": "error", "text": tr("ws.session_missing", id=session_id)})
            return
        self.session = loaded
        if self.session.workspace:
            try:
                self.session_settings = await asyncio.to_thread(
                    self.settings.for_workspace, self.session.workspace
                )
            except ConfigError as exc:
                # Папку могли удалить или отключить внешний диск — чат всё равно
                # должен открыться, просто в папке по умолчанию.
                logger.warning("Папка чата недоступна: %s", exc)
                await self.send(
                    {
                        "type": "log",
                        "level": "warning",
                        "text": tr("ws.chat_folder_missing", path=self.session.workspace),
                    }
                )
                self.session_settings = self.settings
                self.session.workspace = str(self.settings.workspace)
        else:
            self.session.workspace = str(self.settings.workspace)
            self.session_settings = self.settings

        await self.send(
            {
                "type": "session.loaded",
                "session": self.session.to_dict(),
                "workspace": str(self.session_settings.workspace),
                "mode": self.session_settings.approval_mode,
                "auto_workspace": self._is_auto_ws(),
            }
        )
        if self.session.approval_mode:
            self.session_settings = self.session_settings.model_copy(
                update={"approval_mode": self.session.approval_mode}
            )
        self._register_workspace()
        await self._send_context_usage()
        await self._notify_interrupted_run()
        logger.info("Сессия %s загружена (workspace: %s)", self.session.id, self.session.workspace)

    def _auto_ws_root(self) -> Path:
        return self.settings.app_dir / "storage" / "chat_files"

    def _is_auto_ws(self, path: str | None = None) -> bool:
        """Папка чата — авто-персональная (под storage/chat_files), а не проект."""
        target = str(path or self.session.workspace)
        try:
            root = self._auto_ws_root().resolve()
            return root == Path(target).resolve() or root in Path(target).resolve().parents
        except OSError:
            return False

    async def _rewind(self, message: dict[str, Any]) -> None:
        """Rewind: обрезает историю к указанному запросу пользователя (это
        сообщение и всё после него удаляются) без перезапуска — промпт вернётся в
        строку ввода на клиенте, а модель забудет всё после этой точки."""
        turn = message.get("turn")
        if turn is None:
            return
        try:
            ok = self.session.rewind_to_user_turn(int(turn))
        except (TypeError, ValueError):
            return
        if ok:
            await self._save_session()
        else:
            await self.send({"type": "log", "level": "warning", "text": tr("ws.no_checkpoint")})

    async def _fork_session(self, message: dict[str, Any]) -> None:
        """Ветка: копия текущего чата (опц. обрезанная до turn) как новый чат."""
        import copy

        turn = message.get("turn")
        clone = copy.deepcopy(self.session)
        clone.id = uuid.uuid4().hex[:12]
        base = (self.session.title or "Диалог").replace(" (ветка)", "")
        clone.title = f"{base} (ветка)"
        clone.created_at = clone.updated_at = time.time()
        if turn is not None:
            try:
                clone.rewind_to_user_turn(int(turn) + 1)  # оставить 0..turn включительно
            except (TypeError, ValueError):
                pass
        self.session = clone
        try:
            await self.store.async_save(self.session)
        except Exception:  # noqa: BLE001
            logger.debug("Не удалось сохранить ветку", exc_info=True)
        await self.send({
            "type": "session.loaded",
            "session": self.session.to_dict(),
            "workspace": str(self.session_settings.workspace),
            "mode": self.session_settings.approval_mode,
            "auto_workspace": self._is_auto_ws(),
        })

    async def _new_session(self, message: dict[str, Any]) -> None:
        requested = str(message.get("workspace") or "").strip()
        title = str(message.get("title") or "Новый диалог").strip()
        new_id = uuid.uuid4().hex[:12]

        # Нет выбранного проекта (или прислали прошлую авто-папку) → своя папка чата.
        if not requested or self._is_auto_ws(requested):
            workspace: Path | str = self.settings.chat_files_dir(new_id)
        else:
            workspace = requested
        try:
            self.session_settings = await asyncio.to_thread(self.settings.for_workspace, workspace)
        except ConfigError as exc:
            await self.send({"type": "log", "level": "warning", "text": tr("ws.default_folder", error=exc)})
            self.session_settings = self.settings

        self.session = Session(
            id=new_id,
            title=title,
            workspace=str(self.session_settings.workspace),
        )
        await self.send(
            {
                "type": "session.loaded",
                "session": self.session.to_dict(),
                "workspace": str(self.session_settings.workspace),
                "mode": self.session_settings.approval_mode,
                "auto_workspace": self._is_auto_ws(),
            }
        )
        self._register_workspace()
        await self._send_context_usage()
        logger.info("Создан новый чат %s (workspace: %s)", self.session.id, self.session.workspace)

    async def _apply_workspace(self, workspace: str) -> Path | None:
        """Переключает рабочую папку сессии. Возвращает None, если не вышло.

        Вся проверка и создание папки — в потоке: обращения к диску не должны
        блокировать event loop, иначе на медленных/сетевых дисках подвисает
        весь интерфейс.
        """
        target = (workspace or "").strip()
        if not target:
            return None
        try:
            self.session_settings = await asyncio.to_thread(self.settings.for_workspace, target)
        except ConfigError as exc:
            await self.send(
                {
                    "type": "workspace.error",
                    "workspace": target,
                    "message": str(exc),
                }
            )
            await self.send({"type": "log", "level": "error", "text": str(exc)})
            return None

        self.session.workspace = str(self.session_settings.workspace)
        self._register_workspace()
        return self.session_settings.workspace

    def _register_workspace(self) -> None:
        """Разрешает панели превью читать файлы из папки этого чата."""
        try:
            active = self.ws.app.state.active_workspaces
        except AttributeError:  # приложение собрано без lifespan (в тестах)
            return
        active.add(str(self.session_settings.workspace))

    def _register_connection(self) -> None:
        """Регистрирует соединение — планировщик напоминаний шлёт события в чат."""
        try:
            self.ws.app.state.connections.add(self)
        except AttributeError:  # без lifespan (часть тестов)
            pass

    def _unregister_connection(self) -> None:
        try:
            self.ws.app.state.connections.discard(self)
        except AttributeError:
            pass

    async def _set_workspace(self, workspace: str) -> None:
        previous = self.session.workspace
        # Рабочую папку выбирают ОДИН раз — до первого сообщения. После старта чата
        # менять нельзя: смена на ходу оставляла инструменты в старой папке и
        # блокировала доступ к новой. Хочешь другую папку — новый чат.
        if any(m.get("role") == "user" for m in self.session.messages):
            await self.send({
                "type": "workspace.error",
                "workspace": workspace,
                "message": tr("ws.folder_locked"),
            })
            return

        applied = await self._apply_workspace(workspace)
        if applied is None:
            return

        if str(applied) == previous:
            await self.send({"type": "workspace.updated", "workspace": str(applied)})
            return

        # Чат ещё не начат — просто переезжаем в выбранную папку.
        self.session.reset()
        await self._save_session()
        await self.send({"type": "workspace.updated", "workspace": str(applied)})
        await self.send(
            {"type": "log", "level": "info", "text": tr("ws.folder", path=applied)}
        )

    async def _set_approval_mode(self, mode: str) -> None:
        """The approval mode of this chat, which also becomes the default for new chats: the
        user's last choice survives new chats, restarts and updates (it is kept in the app
        data .env, which an update does not touch)."""
        if mode not in MODES:
            await self.send({"type": "log", "level": "warning", "text": tr("ws.unknown_mode", mode=mode)})
            return
        self.session_settings = self.session_settings.model_copy(update={"approval_mode": mode})
        self.session.approval_mode = mode
        await self._save_session()
        if self.settings.approval_mode != mode:
            import core.settings as settings_module
            from core.config_file import write_values

            try:
                await asyncio.to_thread(write_values, {"APPROVAL_MODE": mode}, self.settings)
                clear = getattr(settings_module.get_settings, "cache_clear", None)
                if clear is not None:  # the next get_settings() reads the saved default
                    clear()
                self.settings = self.settings.model_copy(update={"approval_mode": mode})
            except OSError as exc:  # the chat keeps its mode; only the default was not saved
                logger.warning("approval mode default not saved: %s", exc)
        await self.send({"type": "mode.updated", "mode": mode})
        await self.send(
            {"type": "log", "level": "info", "text": tr("ws.mode", title=MODES[mode]["title"])}
        )

    async def _start_run(self, message: dict[str, Any]) -> None:
        task_text = str(message.get("task") or "").strip()
        if not task_text:
            await self.send({"type": "run.failed", "run_id": "", "message": tr("ws.empty_task")})
            await self._state("idle")
            return
        if self.run_task and not self.run_task.done():
            # Живой steering: сообщение во время прогона не отвергаем и не
            # перезапускаем задачу, а передаём агенту следующим ходом.
            self._scratch.setdefault("steering", []).append(task_text)
            self.session.append_timeline({"kind": "user", "text": task_text, "ts": time.time()})
            await self._save_session()
            await self.send(
                {"type": "log", "level": "info", "text": tr("ws.steer_queued")}
            )
            await self.send({"type": "steering.queued", "text": task_text})
            return

        # Повтор задачи: откатываем историю к этому запросу, чтобы модель
        # ответила заново, а не «как я уже говорил выше».
        rerun_turn = message.get("rerun_turn")
        if rerun_turn is not None:
            if not self.session.rewind_to_user_turn(int(rerun_turn)):
                await self.send(
                    {"type": "log", "level": "warning", "text": tr("ws.no_retry")}
                )
            else:
                await self._save_session()

        # Папка, выбранная в интерфейсе, применяется к каждому запуску: так чат
        # не «уедет» в другую директорию из-за рассинхрона UI и сервера.
        ws_override = str(message.get("workspace") or "").strip()
        if ws_override and ws_override != self.session.workspace:
            if await self._apply_workspace(ws_override) is None:
                await self.send({"type": "run.failed", "run_id": "", "message": tr("ws.folder_unavailable")})
                await self._state("idle")
                return

        # Подхватываем СВЕЖИЕ настройки перед каждым запуском: окно «Настройки»
        # пишет .env и сбрасывает кэш get_settings, но это WS-соединение держало
        # свою старую копию — из-за этого правки не применялись в реальном времени.
        # Session-переопределение режима подтверждений сохраняем.
        saved_mode = self.session_settings.approval_mode
        self.settings = get_settings()
        try:
            base = (self.settings.for_workspace(self.session.workspace)
                    if self.session.workspace else self.settings)
        except ConfigError:
            base = self.settings
        self.session_settings = base.model_copy(update={"approval_mode": saved_mode})

        options = RunOptions.from_message(message.get("options"))

        model = str(message.get("model") or "").strip() or None
        if model:
            self.session.model = model

        # Медиа (фото/видео) идёт ТОЙ ЖЕ основной модели — отдельной vision-модели
        # больше нет. Если модель не понимает картинки, она сама ответит отказом.

        entry: dict[str, Any] = {"kind": "user", "text": task_text, "ts": time.time()}
        if options.attachments.items:
            entry["attachments"] = [
                {"name": item.name, "kind": item.kind, "path": str(item.path)}
                for item in options.attachments.items
            ]
        self.session.append_timeline(entry)
        # Маршрутизацию по сложности при медиа НЕ выключаем (по просьбе): вместо
        # этого клиент не даёт прикрепить медиа, если модель его не принимает.
        allow_route = True
        self.run_task = asyncio.create_task(
            self._run(task_text, model, options, allow_route), name="agent-run"
        )

    async def _run(
        self, task_text: str, model: str | None, options: RunOptions | None = None,
        allow_route: bool = False,
    ) -> None:
        new_chat = self.session.title == DEFAULT_TITLE and not self.session.messages
        self._title_for_new_chat = new_chat
        if self.session_settings.chat_titles:
            first = task_text if new_chat else self._untitled_first_message()
            if first:
                self._start_title(first, model)
        if new_chat:
            # A new chat goes into the list at once: routing may take a while before the run
            # itself starts (and stores the chat again). _save_session skips a chat without
            # messages, and the user's message reaches them only when the run starts.
            self.session.workspace = str(self.session_settings.workspace)
            await self.store.async_save(self.session)
        await self._state("running")
        # Маршрутизация по сложности. Оценщик разбивает запрос на подзадачи с
        # тиром модели у каждой. Если тиры РАЗНЫЕ — раздаём подзадачи обеим
        # моделям по очереди (дешёвая — простое, сильная — сложное). Иначе —
        # одна модель на весь прогон. Не применяется при навязанной медиа-модели.
        base_url: str | None = None
        api_key: str | None = None
        # Маршрутизацию на этот прогон задаёт тумблер «Routing» у ввода
        # (options.routing); если он не прислан — постоянная настройка.
        route_on = options.routing if (options and options.routing is not None) else self.session_settings.model_routing
        route_chosen: dict[str, Any] | None = None  # выбранный тир (с запасными моделями)
        if allow_route and route_on:
            from core.agent.router import (
                choose_model,
                is_distributed_plan,
                plan_subtasks,
                resolve_tiers,
            )

            tiers = resolve_tiers(self.session_settings)
            if tiers:
                plan = await plan_subtasks(task_text, self.session_settings, build_llm_client)
                if is_distributed_plan(plan):
                    await self._run_distributed(plan, tiers, options)
                    return
                # Без распределения — выбираем одну модель: если все подзадачи
                # одного тира, берём его; смешанного плана тут нет; пустой план
                # (судья не ответил) → надёжный однословный choose_model.
                if plan:
                    name = plan[0]["tier"] if len({s["tier"] for s in plan}) == 1 else "strong"
                    chosen: dict[str, str] | None = tiers[name]
                    note = f"{'простая' if name == 'fast' else 'сложная'} задача → {chosen.get('model')}"
                else:
                    chosen, note = await choose_model(task_text, self.session_settings, build_llm_client)
                if chosen and chosen.get("model"):
                    route_chosen = chosen
                    model = chosen["model"]
                    base_url = chosen.get("base_url") or None
                    api_key = chosen.get("api_key") or None
                    await self.send({"type": "log", "level": "info", "text": tr("ws.routing", note=note)})
                    await self.send({"type": "model.routed", "model": model, "note": note})
        # Переопределения провайдера передаём только когда они есть (иначе обычный
        # вызов build_llm_client(model=...) — совместим и с монкипатчами в тестах).
        client_kwargs: dict[str, Any] = {"model": model}
        if base_url:
            client_kwargs["base_url"] = base_url
        if api_key:
            client_kwargs["api_key"] = api_key
        try:
            # У выбранного тира могут быть запасные модели: если основная не
            # ответит — FallbackLLM переключится на следующую.
            if route_chosen:
                from core.agent.router import tier_candidates
                from core.llm.fallback import FallbackLLM

                cands = tier_candidates(route_chosen)
                llm = FallbackLLM(cands, build_llm_client) if len(cands) > 1 else build_llm_client(**client_kwargs)
            else:
                llm = build_llm_client(**client_kwargs)
        except AgentError as exc:
            await self.send({"type": "run.failed", "run_id": "", "message": str(exc)})
            await self._state("idle")
            return

        runner = AgentRunner(
            llm=llm,
            registry=self.registry,
            session=self.session,
            settings=self.session_settings,
            emitter=self.emit,
            approver=self.ask_approval,
        )
        # Инструменту ask нужно достучаться до соединения, чтобы дождаться
        # ответа пользователя: отдаём ему общий scratch.
        runner.tool_context.scratch = self._scratch

        try:
            await runner.run(task_text, options)
            await self._save_session()
        except asyncio.CancelledError:
            logger.info("Задача отменена пользователем (сессия %s).", self.session.id)
            await self._save_session()
        finally:
            await llm.aclose()
            await self._state("idle")
            await self._send_context_usage()

    # ------------------------------------------------------------------ chat title

    async def _rename_session(self, session_id: str, title: str) -> None:
        """The user's own title wins over the model's: a pending title request is dropped."""
        title = " ".join(title.split())[:120]
        if not session_id or not title:
            return
        if session_id == self.session.id:
            if self._title_task is not None and not self._title_task.done():
                self._title_task.cancel()
            self._title_task, self._pending_title = None, None
            self.session.title = title
            await self._save_session()
        else:
            stored = await self.store.async_load(session_id)
            if stored is None:
                return
            stored.title = title
            await self.store.async_save(stored)
        await self.send({"type": "session.title", "session_id": session_id, "title": title, "renamed": True})

    def _untitled_first_message(self) -> str:
        """The first message of a chat still called after it (named by the old first-line rule,
        before the model wrote titles), or "" — the user's own names are left alone. Tried
        once per chat per connection, so a model that cannot answer is not asked every time."""
        from core.agent.titler import fallback_title

        if not self.session.messages or self.session.id in self._retitle_tried:
            return ""
        # The UI feed keeps its last 400 entries and long histories get compacted, so the
        # first message may survive in either place only.
        candidates = [next((str(e.get("text") or "") for e in self.session.timeline if e.get("kind") == "user"), "")]
        for m in self.session.messages:
            if m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, list):
                    content = " ".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
                candidates.append(str(content or ""))
                break
        first = next((c for c in candidates if c.strip() and self.session.title == fallback_title(c)), "")
        if not first:
            return ""
        self._retitle_tried.add(self.session.id)
        return first

    def _start_title(self, task_text: str, model: str | None) -> None:
        """Ask for the chat's title alongside the first answer (the router's cheap model when
        routing is set up, otherwise the chat's model)."""
        from core.agent.router import _build_kwargs, resolve_tiers

        tiers = resolve_tiers(self.session_settings) if self.session_settings.model_routing else None
        candidates: list[dict[str, Any]] = []
        for tier in (tiers["router"], tiers["fast"], tiers["strong"]) if tiers else ():
            candidates.append(_build_kwargs(tier))
        candidates.append({"model": model or self.session.model or self.session_settings.default_model})
        unique: list[dict[str, Any]] = []
        for kw in candidates:
            if kw.get("model") and kw not in unique:
                unique.append(kw)
        self._pending_title = None
        self._title_task = asyncio.create_task(
            self._make_title(task_text, unique, self.session.id), name="chat-title"
        )

    async def _make_title(self, task_text: str, kwargs: list[dict[str, Any]], session_id: str) -> None:
        # A background task: an error here would vanish with it, so it is logged.
        try:
            await self._apply_title(task_text, kwargs, session_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("chat title failed", exc_info=True)

    async def _apply_title(self, task_text: str, kwargs: list[dict[str, Any]], session_id: str) -> None:
        from core.agent.titler import fallback_title, generate_title

        title = await generate_title(task_text, build_llm_client, kwargs) or fallback_title(task_text)
        if self.session.id != session_id:
            # The user moved to another chat meanwhile: title the stored one, not this.
            stored = await self.store.async_load(session_id)
            if stored is not None and stored.title in (DEFAULT_TITLE, fallback_title(task_text)):
                stored.title = title
                await self.store.async_save(stored)
            await self.send({"type": "session.title", "session_id": session_id, "title": title})
            return
        self._pending_title = title
        if self.session.messages:  # the run has started: apply and store it now
            self.session.title = title
            await self._save_session()
        await self.send({"type": "session.title", "session_id": session_id, "title": title})

    async def _on_run_started(self) -> None:
        """The user's message is in the session now: store the chat at once, so it is in the
        list even if the app closes mid-answer. Until the model's title arrives the chat is
        called "New chat" (Session.add_user put the first line there)."""
        if self._title_task is not None:
            if self._pending_title:
                self.session.title = self._pending_title
            elif not self._title_task.done() and self._title_for_new_chat:
                self.session.title = DEFAULT_TITLE
        await self._save_session()

    async def _run_distributed(
        self, plan: list[dict[str, str]], tiers: dict[str, dict[str, str]], options: RunOptions | None,
    ) -> None:
        """Раздаёт подзадачи по тирам в ОДНОЙ сессии: модель переключается между
        подзадачами (дешёвая делает простое, сильная — сложное). Контекст общий,
        поэтому поздние подзадачи видят результат ранних."""
        clients: dict[str, Any] = {}

        def client_for(tier_name: str) -> Any:
            if tier_name not in clients:
                from core.agent.router import tier_candidates
                from core.llm.fallback import FallbackLLM

                tier = tiers.get(tier_name) or tiers["strong"]
                cands = tier_candidates(tier)
                if len(cands) > 1:
                    clients[tier_name] = FallbackLLM(cands, build_llm_client)
                else:
                    kw: dict[str, Any] = {"model": tier.get("model")}
                    if tier.get("base_url"):
                        kw["base_url"] = tier["base_url"]
                    if tier.get("api_key"):
                        kw["api_key"] = tier["api_key"]
                    clients[tier_name] = build_llm_client(**kw)
            return clients[tier_name]

        summary = ", ".join(f"{i + 1}:{s['tier']}" for i, s in enumerate(plan))
        await self.send({
            "type": "log", "level": "info",
            "text": tr("ws.routing_split", n=len(plan), summary=summary),
        })
        runner: AgentRunner | None = None
        try:
            for i, sub in enumerate(plan):
                try:
                    llm = client_for(sub["tier"])
                except AgentError as exc:
                    await self.send({"type": "run.failed", "run_id": "", "message": str(exc)})
                    break
                model_name = (tiers.get(sub["tier"]) or {}).get("model", "")
                await self.send({
                    "type": "model.routed", "model": model_name,
                    "note": f"подзадача {i + 1}/{len(plan)} → {sub['tier']} ({model_name})",
                })
                if runner is None:
                    runner = AgentRunner(
                        llm=llm, registry=self.registry, session=self.session,
                        settings=self.session_settings, emitter=self.emit, approver=self.ask_approval,
                    )
                    runner.tool_context.scratch = self._scratch
                else:
                    runner.llm = llm
                await runner.run(sub["text"], options)
                await self._save_session()
        except asyncio.CancelledError:
            logger.info("Распределённая задача отменена (сессия %s).", self.session.id)
            await self._save_session()
        finally:
            for client in clients.values():
                try:
                    await client.aclose()
                except Exception:  # noqa: BLE001
                    pass
            await self._state("idle")
            await self._send_context_usage()

    async def _restore_checkpoint(self, rel_path: str) -> None:
        """Откатывает последнее изменение файла по кнопке в интерфейсе."""
        if not rel_path:
            return
        store = CheckpointStore(
            self.session_settings.storage_dir,
            self.session.id,
            self.session_settings.workspace,
        )
        try:
            message = await asyncio.to_thread(store.restore_latest, rel_path)
        except (LookupError, RuntimeError) as exc:
            await self.send({"type": "log", "level": "warning", "text": str(exc)})
            return

        await self.emit(CheckpointRestored(path=rel_path, message=message))
        await self.send({"type": "log", "level": "info", "text": message})

    def _checkpoint_store(self) -> CheckpointStore:
        return CheckpointStore(
            self.session_settings.storage_dir,
            self.session.id,
            self.session_settings.workspace,
        )

    def _run_state_store(self) -> RunStateStore:
        return RunStateStore(self.session_settings.data_dir)

    async def _notify_interrupted_run(self) -> None:
        """Если прошлый прогон этого чата не завершился штатно (процесс умер) —
        сообщаем интерфейсу, чтобы предложить продолжить прерванную задачу."""
        rec = await asyncio.to_thread(self._run_state_store().interrupted, self.session.id)
        if rec:
            await self.send({"type": "run_interrupted", **rec})

    async def _dismiss_interrupted(self) -> None:
        """Скрывает пометку о прерванном прогоне (пользователь не хочет продолжать)."""
        await asyncio.to_thread(self._run_state_store().clear, self.session.id)

    async def _resume_run(self) -> None:
        """Продолжает прерванную задачу: вся история диалога уже загружена, поэтому
        достаточно попросить агента доделать начатое. Маркер снимаем сразу, чтобы
        не предлагать продолжение повторно."""
        rec = await asyncio.to_thread(self._run_state_store().interrupted, self.session.id)
        await asyncio.to_thread(self._run_state_store().clear, self.session.id)
        if not rec:
            await self.send({"type": "log", "level": "warning",
                             "text": tr("ws.nothing_to_resume")})
            return
        task = str(rec.get("task") or "").strip()
        prompt = (
            "Продолжи прерванную задачу — прошлый запуск оборвался из-за сбоя/перезапуска. "
            "Свежим взглядом проверь, что уже сделано в этом чате и рабочей папке, доделай "
            "оставшееся и доведи до конца."
        )
        if task:
            prompt += f"\n\nИсходная задача была: {task}"
        await self._start_run({"task": prompt})

    async def _run_audit(self, run_id: str) -> None:
        """Отдаёт интерфейсу аудит прогона: какие файлы затронуты и откатываемо ли."""
        if not run_id:
            return
        manifest = await asyncio.to_thread(self._checkpoint_store().run_manifest, run_id)
        await self.send({"type": "run_audit", **manifest})

    async def _rollback_run(self, run_id: str) -> None:
        """Откат ВСЕХ изменений прогона по кнопке «Откатить прогон»."""
        if not run_id:
            return
        store = self._checkpoint_store()
        try:
            result = await asyncio.to_thread(store.restore_run, run_id)
        except (LookupError, RuntimeError) as exc:
            await self.send({"type": "log", "level": "warning", "text": str(exc)})
            await self.send({"type": "run_rollback", "run_id": run_id, "restored": [], "error": str(exc)})
            return
        n = len(result["restored"])
        skipped = result.get("skipped") or []
        summary = f"Откат прогона: восстановлено файлов — {n}"
        if skipped:
            summary += f", пропущено (без снимка) — {len(skipped)}"
        await self.send({"type": "log", "level": "info", "text": summary})
        await self.send({"type": "run_rollback", "run_id": run_id, "restored": result["restored"],
                         "skipped": skipped, "message": summary})

    async def _stop_run(self) -> None:
        if self.run_task and not self.run_task.done():
            self.run_task.cancel()
            await self.send({"type": "log", "level": "warning", "text": tr("ws.stopping")})
        for future in self.pending_approvals.values():
            if not future.done():
                future.set_result("deny")
        self.pending_approvals.clear()
        self._cancel_questions()
        self._cancel_bridge_waiters()

    def _cancel_questions(self) -> None:
        """Снимает ожидание ответов, иначе задача зависнет навсегда."""
        for waiter in self._scratch.get("_ask_answers", {}).values():
            if not waiter.done():
                waiter.set_result({})

    async def ask_approval(self, request: ApprovalRequest) -> bool:
        """Спрашивает пользователя — строго по одному запросу за раз.

        Замок обязателен: инструменты выполняются параллельно, и без него на
        экран вываливалось бы несколько карточек сразу, а ответ уходил бы не
        тому запросу.
        """
        workspace = str(self.session_settings.workspace)

        # Запомненное «всегда разрешать» снимает вопрос ещё до показа карточки.
        if self.permissions.is_allowed(request.name, workspace):
            return True

        async with self._approval_lock:
            if self.permissions.is_allowed(request.name, workspace):
                return True

            req_id = uuid.uuid4().hex[:8]
            future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
            self.pending_approvals[req_id] = future

            await self._state("waiting_approval")
            await self.send(
                {
                    "type": "approval.requested",
                    "request_id": req_id,
                    "name": request.name,
                    "reason": request.reason,
                    "args": request.args,
                    "category": request.category,
                    "tier": request.tier,
                    "reasons": request.reasons,
                    "workspace": workspace,
                }
            )

            try:
                answer = await future
            finally:
                self.pending_approvals.pop(req_id, None)
                await self._state("running")

            if answer == "project":
                await asyncio.to_thread(self.permissions.allow_project, request.name, workspace)
            elif answer == "global":
                await asyncio.to_thread(self.permissions.allow_global, request.name)

            approved = answer in ("once", "project", "global")
            await self.send(
                {
                    "type": "approval.resolved",
                    "request_id": req_id,
                    "approved": approved,
                    "scope": answer,
                }
            )
            return approved

    def _resolve_question(self, message: dict[str, Any]) -> None:
        """Ответы на вопросы агента: {"0": ["вариант"], "1": [...]}."""
        request_id = str(message.get("request_id") or "")
        waiters = self._runner_scratch().get("_ask_answers", {})
        waiter = waiters.get(request_id)
        if waiter and not waiter.done():
            waiter.set_result(message.get("answers") or {})

    def _resolve_handoff(self, message: dict[str, Any]) -> None:
        """Пользователь нажал «Готово» в передаче управления браузером."""
        request_id = str(message.get("request_id") or "")
        waiters = self._runner_scratch().get("_browser_handoff", {})
        waiter = waiters.get(request_id)
        if waiter and not waiter.done():
            waiter.set_result(message.get("result") or {})

    def _runner_scratch(self) -> dict[str, Any]:
        """Общая память инструментов текущего прогона."""
        return self._scratch

    def _resolve_approval(self, message: dict[str, Any]) -> None:
        """Ответ пользователя: once | project | global | deny."""
        req_id = str(message.get("request_id") or "")
        scope = str(message.get("scope") or "").strip()
        if not scope:
            scope = "once" if message.get("approved") else "deny"
        if scope not in ("once", "project", "global", "deny"):
            scope = "deny"

        future = self.pending_approvals.get(req_id)
        if future and not future.done():
            future.set_result(scope)

    # ------------------------------------------------------ мост к телефону

    def _bridge_ws(self, message: dict[str, Any]) -> Path:
        """Папка для файловой операции моста.

        Телефон открывает отдельный сокет на каждую файловую команду и присылает
        `workspace` из настроек моста. Резолвим в неё; если не задана/недоступна —
        общая папка обмена storage/bridge.
        """
        ws = str(message.get("workspace") or "").strip()
        if ws:
            try:
                return self.settings.for_workspace(ws).workspace
            except ConfigError:
                pass
        base = self.settings.app_dir / "storage" / "bridge"
        base.mkdir(parents=True, exist_ok=True)
        return base

    async def _bridge_hello(self, message: dict[str, Any]) -> None:
        """Взаимное представление возможностей (слой 1). Необязательное."""
        self.peer = {
            "platform": str(message.get("platform") or "").strip() or "unknown",
            "capabilities": list(message.get("capabilities") or []),
            "version": str(message.get("version") or ""),
        }
        logger.info(
            "Мост: подключён %s (возможности: %s)",
            self.peer["platform"],
            ", ".join(self.peer["capabilities"]) or "—",
        )
        await self.send(
            {
                "type": "hello",
                "platform": "pc",
                "capabilities": ["shell", "python", "git", "files", "web", "heavy_compute"],
                "workspace": str(self.session_settings.workspace),
                "version": __version__,
            }
        )

    async def _bridge_list_files(self, message: dict[str, Any]) -> None:
        """Список файлов на ПК (слой 2)."""
        base = self._bridge_ws(message)
        pattern = str(message.get("glob") or "**/*").strip() or "**/*"
        # Телефон шлёт «out/**» в смысле «всё под out». В pathlib «**» матчит
        # директории, а не файлы, поэтому добавляем «/*», иначе файлы теряются.
        if pattern == "**" or pattern.endswith("/**"):
            pattern += "/*"
        items: list[dict[str, Any]] = []
        try:
            matches = sorted(base.glob(pattern))
        except (ValueError, OSError) as exc:
            await self.send({"type": "files", "items": [], "error": str(exc)})
            return
        for p in matches:
            try:
                if not p.is_file():
                    continue
                st = p.stat()
                items.append(
                    {
                        "path": p.relative_to(base).as_posix(),
                        "bytes": st.st_size,
                        "mtime": _iso(st.st_mtime),
                        "kind": _kind(p.name),
                    }
                )
            except (OSError, ValueError):
                # ValueError — путь вне base (глоб с ..); OSError — гонка с ФС.
                continue
            if len(items) >= MAX_LIST_ITEMS:
                break
        await self.send({"type": "files", "items": items})

    async def _bridge_stat_file(self, message: dict[str, Any]) -> None:
        """Метаданные одного файла — посмотреть до того, как забрать (слой 2)."""
        base = self._bridge_ws(message)
        rel = str(message.get("path") or "")
        try:
            target = _resolve_in_workspace(base, rel)
        except ValueError:
            await self.send({"type": "file.stat", "path": rel, "exists": False})
            return
        if not target.is_file():
            await self.send({"type": "file.stat", "path": rel, "exists": False})
            return
        st = target.stat()
        await self.send(
            {
                "type": "file.stat",
                "path": rel,
                "exists": True,
                "bytes": st.st_size,
                "mtime": _iso(st.st_mtime),
                "kind": _kind(target.name),
            }
        )

    async def _bridge_get_file(self, message: dict[str, Any]) -> None:
        """Отдать файл телефону в base64 (слой 2)."""
        base = self._bridge_ws(message)
        rel = str(message.get("path") or "")
        try:
            target = _resolve_in_workspace(base, rel)
        except ValueError:
            await self.send({"type": "file.missing", "path": rel})
            return
        try:
            if not target.is_file() or target.stat().st_size > MAX_FILE_BYTES:
                await self.send({"type": "file.missing", "path": rel})
                return
            data = await asyncio.to_thread(target.read_bytes)
        except OSError:
            await self.send({"type": "file.missing", "path": rel})
            return
        await self.send(
            {
                "type": "file",
                "path": rel,
                "bytes": len(data),
                "b64": base64.b64encode(data).decode("ascii"),
            }
        )

    async def _bridge_put_file(self, message: dict[str, Any]) -> None:
        """Принять файл от телефона (слой 2 и ответ на need_file слоя 3).

        Во время `run` (ответ на need_file) поле `workspace` отсутствует — тогда
        пишем в рабочую папку текущего прогона (обычно inbox/).
        """
        rel = str(message.get("path") or "")
        has_ws = bool(str(message.get("workspace") or "").strip())
        base = self._bridge_ws(message) if has_ws else Path(self.session_settings.workspace)
        try:
            target = _resolve_in_workspace(base, rel)
            raw = base64.b64decode(str(message.get("b64") or ""), validate=False)
            if len(raw) > MAX_FILE_BYTES:
                raise ValueError("файл больше 25 МБ")
            target.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(target.write_bytes, raw)
        except Exception as exc:  # noqa: BLE001 - любая ошибка уходит телефону текстом
            await self.send({"type": "put_file.error", "path": rel, "message": str(exc)})
            return
        await self.send({"type": "put_file.ok", "path": rel, "bytes": len(raw)})

    async def _bridge_sync_memory(self, message: dict[str, Any]) -> None:
        """Синхронизация общей памяти (слой 3-bis).

        Форматы хранения разные (телефон — global.md с буллетами, ПК —
        memory.json из Fact), поэтому обмениваемся ТЕКСТАМИ фактов. Доливаем
        присланные факты к себе (MemoryStore сам дедупит по нормализованному
        тексту) и возвращаем ВСЕ факты ПК — телефон допишет недостающее у себя.
        """
        incoming = [str(text) for text in (message.get("facts") or []) if str(text).strip()]

        def _merge() -> list[str]:
            store = MemoryStore(self.settings.data_dir)  # та же общая память, что у агента
            for text in incoming:
                store.remember(text, category="fact", session_id="bridge")
            return [f.text for f in store.all()]

        facts = await asyncio.to_thread(_merge)
        await self.send({"type": "memory_sync", "facts": facts})

    # --- Мост: синк плагинов (MCP) и навыков с телефоном ---

    async def _bridge_mcp_list(self, message: dict[str, Any]) -> None:
        """Отдать телефону список HTTP/SSE MCP-серверов (контракт синка плагинов)."""
        from core.mcp.manager import MCPManager

        servers = await asyncio.to_thread(lambda: MCPManager(self.settings).shareable_servers())
        await self.send({"type": "mcp", "servers": servers})

    async def _bridge_skills_list(self, message: dict[str, Any]) -> None:
        """Отдать телефону метаданные навыков (name/description/version)."""
        from core.skills.manager import SkillManager, parse_frontmatter

        def _list() -> list[dict[str, str]]:
            items: list[dict[str, str]] = []
            for skill in SkillManager(self.settings).list_skills():
                version = ""
                try:
                    meta, _ = parse_frontmatter(skill.path.read_text(encoding="utf-8", errors="ignore"))
                    version = meta.get("version", "")
                except OSError:
                    pass
                items.append({"name": skill.name, "description": skill.description, "version": version})
            return items

        items = await asyncio.to_thread(_list)
        await self.send({"type": "skills", "items": items})

    async def _bridge_skill_get(self, message: dict[str, Any]) -> None:
        """Отдать телефону файлы навыка в base64 → пишутся в filesDir/skills/<name>/."""
        from core.skills.manager import SkillManager

        name = str(message.get("name") or "").strip()

        def _collect() -> list[dict[str, str]] | None:
            skill = SkillManager(self.settings).get(name)
            if skill is None:
                return None
            folder = skill.path.parent
            files: list[dict[str, str]] = []
            total = 0
            for path in sorted(folder.rglob("*")):
                if not path.is_file():
                    continue
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                total += len(data)
                if total > MAX_FILE_BYTES:  # предохранитель от гигантских навыков
                    break
                files.append(
                    {
                        "path": path.relative_to(folder).as_posix(),
                        "b64": base64.b64encode(data).decode("ascii"),
                    }
                )
            return files

        files = await asyncio.to_thread(_collect)
        if files is None:
            await self.send({"type": "skill", "name": name, "files": [], "error": tr("ws.skill_missing")})
            return
        await self.send({"type": "skill", "name": name, "files": files})

    # --- слой 3: обратные запросы агента ПК к телефону ---

    async def request_from_phone(
        self, msg: dict[str, Any], timeout: float = 180.0
    ) -> dict[str, Any]:
        """Шлёт телефону запрос и ждёт ответ по req_id.

        Возвращает сообщение-ответ телефона либо {"cancelled": True} при таймауте,
        отключении или остановке задачи.
        """
        req_id = uuid.uuid4().hex[:8]
        payload = {**msg, "req_id": req_id}
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._bridge_waiters[req_id] = fut
        await self.send(payload)
        try:
            return await asyncio.wait_for(fut, timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return {"cancelled": True}
        finally:
            self._bridge_waiters.pop(req_id, None)

    async def ask_phone(
        self, question: str, options: list[str] | None = None, timeout: float = 180.0
    ) -> dict[str, Any]:
        """Спрашивает пользователя телефона. Телефон отвечает `answer` с request_id.

        Переиспользует существующий канал `_ask_answers` (тот же, что у ask): его
        резолвит ветка `answer`, а на остановке снимает `_cancel_questions`.
        """
        answers = self._scratch.setdefault("_ask_answers", {})
        req_id = uuid.uuid4().hex[:8]
        fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        answers[req_id] = fut
        payload: dict[str, Any] = {"type": "ask_user", "req_id": req_id, "question": question}
        if options:
            payload["options"] = list(options)
        await self.send(payload)
        try:
            return await asyncio.wait_for(fut, timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return {}
        finally:
            answers.pop(req_id, None)

    def _bridge_resolve(self, message: dict[str, Any]) -> None:
        """Резолвит ожидание обратного запроса по req_id (need_file/capability)."""
        fut = self._bridge_waiters.get(str(message.get("req_id") or ""))
        if fut and not fut.done():
            fut.set_result(message)

    def _cancel_bridge_waiters(self) -> None:
        """Снимает висящие обратные запросы, иначе прогон зависнет при обрыве."""
        for fut in self._bridge_waiters.values():
            if not fut.done():
                fut.set_result({"cancelled": True})
        self._bridge_waiters.clear()


def _jsonable(value: Any) -> Any:
    """Аргументы инструментов могут содержать Path и прочее — приводим к JSON."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
