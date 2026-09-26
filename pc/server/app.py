"""FastAPI-приложение: статика, health-эндпоинты, системный проводник и WebSocket агента.

Тяжёлые ресурсы (MCP-серверы, реестр инструментов) создаются один раз в
lifespan и переиспользуются всеми подключениями.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import string
import tempfile
import zipfile
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from core import export as export_mod
from core.agent.storage import SessionStore
from core.commands import Command, CommandStore
from core.config_file import apply_settings, read_public_settings
from core.errors import PathNotAllowed
from core.i18n import tr
from core.logging_setup import get_logger, setup_logging
from core.mcp.manager import MCPManager, parse_pasted_config
from core.memory import MemoryStore
from core.models_catalog import ModelCatalog
from core.presets import Preset, PresetStore
from core.research.browser import close_browser
from core.settings import get_settings
from core.skills.manager import MAX_IMPORT_BYTES, SkillManager
from core.tools import build_default_registry
from core.tools.builtin.web import close_http_client
from core.updater import Updater
from core.version import __version__
from server.files import (
    guess_media_type,
    load_preview,
    preview_roots,
    resolve_preview_path,
)
from server.folder_dialog import has_native_window, pick_files, pick_folder
from server.lan_bridge import LanBridge
from server.remote_auth import RemoteAuthMiddleware
from server.swarm_ws import SwarmConnection, default_config
from server.terminal_ws import serve_terminal
from server.uploads import save_uploads
from server.ws import _LOOPBACK_HOSTS, Connection

logger = get_logger("server")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _attachment(payload: bytes, media_type: str, filename: str) -> Response:
    """Ответ-файл: заставляет браузер скачать, а не открыть во вкладке.

    Имя пробрасываем и в ASCII-fallback, и в RFC 5987 (filename*), иначе
    кириллические заголовки диалогов ломают заголовок Content-Disposition.
    """
    from urllib.parse import quote

    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "export"
    disposition = (
        f"attachment; filename=\"{ascii_name}\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": disposition},
    )


def get_system_drives() -> list[str]:
    """Возвращает список доступных дисков на Windows или корень на Linux."""
    if os.name == "nt":
        drives = []
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                drives.append(drive)
        return drives or ["C:\\"]
    return ["/"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()

    for problem in settings.problems():
        logger.warning("Конфигурация: %s", problem)

    registry = build_default_registry()
    mcp = MCPManager(settings)
    store = SessionStore(settings=settings)

    try:
        mcp_tools = await mcp.start()
    except Exception:  # noqa: BLE001 - MCP не должен мешать запуску
        logger.exception("Не удалось инициализировать MCP")
        mcp_tools = []

    # From here on the manager keeps the registry in step with servers added, switched
    # off or reconnected from the settings — no restart needed.
    mcp.bind_registry(registry)

    app.state.settings = settings
    app.state.registry = registry
    # Папки открытых чатов: превью должно работать сразу после выбора папки,
    # не дожидаясь, пока чат сохранится на диск.
    app.state.active_workspaces = {str(settings.workspace)}
    app.state.mcp = mcp
    app.state.store = store
    # Активные подключения — планировщик напоминаний шлёт сработавшие события в
    # нужный чат, а сигнал phone_online проверяет наличие телефона по мосту.
    app.state.connections = set()
    logger.info(
        "Агент готов: инструментов %d (из них MCP %d), workspace %s",
        len(registry),
        len(mcp_tools),
        settings.workspace,
    )

    from server.reminders import reminder_scheduler

    reminder_task = asyncio.create_task(reminder_scheduler(app), name="reminder-scheduler")

    # The built-in browser's network: direct or through the VPN, per site.
    from core.browser_net import start_proxy, stop_proxy
    from core.browser_session import browser_dir as _browser_dir

    try:
        await start_proxy(_browser_dir() / "network.json", settings.browser_network)
    except OSError as exc:
        logger.warning("browser network proxy did not start: %s", exc)

    # The phone bridge's network entrance (listeners on the LAN / Tailscale addresses).
    app.state.lan = LanBridge(app)
    if settings.bridge_lan:
        await app.state.lan.apply(True)

    try:
        yield
    finally:
        reminder_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reminder_task
        await app.state.lan.close()
        await stop_proxy()
        await mcp.stop()
        await close_http_client()
        # Chromium живёт между запросами; без явного закрытия процесс останется
        # висеть после выхода из приложения.
        await close_browser()
        # Интерактивный браузер агента (постоянный профиль) — тоже гасим.
        from core.browser_session import close_agent_browser

        await close_agent_browser()
        # Dev-серверы, запущенные агентом, тоже надо погасить — иначе npm/uvicorn
        # переживут приложение и займут порты.
        from core.devserver import get_manager

        get_manager().shutdown()
        # Language server'ы (pyright и др.) — живут между запросами, гасим их.
        from core.lsp.servers import get_lsp_manager

        get_lsp_manager().shutdown()
        # Персистентные tgrep-серверы (опция для огромных репо) — гасим, чтобы
        # демоны не пережили приложение.
        from core.tgrep_server import manager as tgrep_manager

        tgrep_manager.stop_all()
        logger.info("Приложение остановлено.")


def _bridge_authorized(websocket: WebSocket) -> bool:
    """True, если подключение к /ws разрешено.

    localhost — всегда (это веб-интерфейс на той же машине). Удалённые адреса
    (телефон через LAN/Tailscale) — только если задан bridge_token и клиент
    прислал его в query (?token=) или заголовке Authorization: Bearer.
    """
    host = (websocket.client.host if websocket.client else "") or ""
    if host in _LOOPBACK_HOSTS:
        return True
    token = get_settings().bridge_token.strip()
    if not token:
        return False  # удалённые запрещены, пока секрет не задан
    supplied = websocket.query_params.get("token") or ""
    if not supplied:
        auth = websocket.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
    return bool(supplied) and hmac.compare_digest(supplied, token)


def create_app() -> FastAPI:
    app = FastAPI(title="Altair", version=__version__, lifespan=lifespan)
    # Every route, not only /ws: in LAN mode the whole server is on the network.
    app.add_middleware(RemoteAuthMiddleware)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_model=None)
    async def index() -> FileResponse | JSONResponse:
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return JSONResponse({"status": "ok", "hint": "static/index.html не найден"})

    @app.get("/api/health")
    async def health() -> dict:
        settings = app.state.settings
        return {
            "status": "ok",
            "version": __version__,
            "model": settings.default_model,
            "workspace": str(settings.workspace),
            "tools": len(app.state.registry),
            "warnings": settings.problems(),
            "mcp": app.state.mcp.status(),
        }

    @app.get("/api/tools")
    async def tools() -> dict:
        return {
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "dangerous": tool.dangerous,
                    "schema": tool.schema()["function"]["parameters"],
                }
                for tool in app.state.registry.all()
            ]
        }

    @app.get("/api/sessions")
    async def list_sessions() -> dict:
        store: SessionStore = app.state.store
        sessions = await store.async_list()
        return {"sessions": sessions}

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> dict:
        store: SessionStore = app.state.store
        session = await store.async_load(session_id)
        if not session:
            raise HTTPException(status_code=404, detail=tr("api.session_missing"))
        return {"session": session.to_dict()}

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str) -> dict:
        store: SessionStore = app.state.store
        deleted = await store.async_delete(session_id)
        return {"ok": deleted}

    @app.get("/api/trash/sessions")
    async def list_trashed_sessions() -> dict:
        """Недавно удалённые чаты (в корзине 30 дней) — для восстановления."""
        store: SessionStore = app.state.store
        return {"sessions": await store.async_list_trashed()}

    @app.post("/api/sessions/{session_id}/restore")
    async def restore_session(session_id: str) -> dict:
        """Вернуть случайно удалённый чат из корзины."""
        store: SessionStore = app.state.store
        return {"ok": await store.async_restore(session_id)}

    @app.get("/api/sessions/{session_id}/export")
    async def export_session(
        session_id: str, format: str = "md", turn: int | None = None
    ) -> Response:
        """Экспорт диалога (или одного ответа) в Markdown / HTML / PDF.

        `format` — md | html | pdf. `turn` — номер ответа (0 — первый); без него
        экспортируется весь чат. Файл отдаётся как вложение с именем по заголовку.
        """
        store: SessionStore = app.state.store
        session = await store.async_load(session_id)
        if not session:
            raise HTTPException(status_code=404, detail=tr("api.session_missing"))
        data = session.to_dict()
        fmt = (format or "md").lower()

        if fmt in ("md", "markdown"):
            text = export_mod.session_to_markdown(data, turn=turn)
            name = export_mod.safe_filename(session.title, "md")
            return _attachment(text.encode("utf-8"), "text/markdown; charset=utf-8", name)
        if fmt == "html":
            text = export_mod.session_to_html(data, turn=turn)
            name = export_mod.safe_filename(session.title, "html")
            return _attachment(text.encode("utf-8"), "text/html; charset=utf-8", name)
        if fmt == "pdf":
            settings = app.state.settings
            out = settings.data_dir / "exports"
            out.mkdir(parents=True, exist_ok=True)
            pdf_path = out / f"{export_mod.timestamp_suffix()}.pdf"
            try:
                await asyncio.to_thread(
                    export_mod.session_to_pdf, data, pdf_path, turn=turn
                )
            except export_mod.PdfEngineMissing as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            payload = pdf_path.read_bytes()
            pdf_path.unlink(missing_ok=True)
            name = export_mod.safe_filename(session.title, "pdf")
            return _attachment(payload, "application/pdf", name)

        raise HTTPException(status_code=400, detail="format: md | html | pdf")

    @app.post("/api/dialog/select-folder")
    async def select_folder_dialog(payload: dict | None = None) -> dict:
        """Открывает системный диалог выбора папки.

        Всегда сообщает причину, если папку выбрать не удалось: интерфейс
        должен показать пользователю внятное объяснение, а не промолчать.
        """
        initial = str((payload or {}).get("initial_dir") or "")
        path, reason = await asyncio.to_thread(pick_folder, initial)
        if path:
            return {"ok": True, "path": path, "cancelled": False}
        cancelled = reason == "cancelled"
        return {
            "ok": False,
            "path": None,
            "cancelled": cancelled,
            "error": "" if cancelled else tr("api.dialog_unavailable", reason=reason),
            "native_window": has_native_window(),
        }

    @app.post("/api/dialog/select-files")
    async def select_files_dialog(payload: dict | None = None) -> dict:
        """Системный диалог выбора файлов для вложений."""
        data = payload or {}
        initial = str(data.get("initial_dir") or "")
        kind = "media" if str(data.get("kind") or "") == "media" else "any"

        paths, reason = await asyncio.to_thread(pick_files, initial, kind)
        if paths:
            return {"ok": True, "paths": paths, "cancelled": False}
        cancelled = reason == "cancelled"
        return {
            "ok": False,
            "paths": [],
            "cancelled": cancelled,
            "error": "" if cancelled else tr("api.dialog_unavailable", reason=reason),
            "native_window": has_native_window(),
        }

    @app.get("/api/models")
    async def models(refresh: bool = False) -> dict:
        """Список моделей для выпадающего списка (живой, с кэшем на полдня)."""
        catalog = ModelCatalog(app.state.settings)
        return await catalog.load(force=refresh)

    @app.post("/api/attachments/upload")
    async def upload_attachments(files: list[UploadFile]) -> dict:
        """Сохраняет перетащенные в чат файлы и возвращает их пути.

        Браузер (даже WebView2) не отдаёт путь перетащенного файла — только имя и
        содержимое. Поэтому байты приходят сюда, сохраняются в папку данных, и
        уже путь идёт в систему вложений (которая читает файлы с диска).
        """
        saved, errors = await save_uploads(files, app.state.settings.data_dir)
        return {"paths": saved, "errors": errors}

    @app.get("/api/memory")
    async def get_memory() -> dict:
        """Что агент запомнил — для прозрачности: пользователь видит и правит."""
        store = MemoryStore(app.state.settings.data_dir)
        return {
            "facts": [
                {"id": f.id, "text": f.text, "category": f.category, "created_at": f.created_at}
                for f in sorted(store.all(), key=lambda f: f.created_at, reverse=True)
            ]
        }

    @app.delete("/api/memory/{fact_id}")
    async def forget_fact(fact_id: str) -> dict:
        store = MemoryStore(app.state.settings.data_dir)
        if fact_id == "all":
            return {"ok": True, "cleared": store.clear()}
        return {"ok": store.forget(fact_id)}

    @app.get("/api/commands")
    async def get_commands() -> dict:
        store = CommandStore(app.state.settings.data_dir)
        return {"commands": [asdict(c) for c in store.all()]}

    @app.post("/api/commands")
    async def save_command(payload: dict) -> dict:
        store = CommandStore(app.state.settings.data_dir)
        try:
            command = store.save(
                Command(
                    name=str(payload.get("name") or ""),
                    template=str(payload.get("template") or ""),
                    description=str(payload.get("description") or ""),
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return {"ok": True, "command": asdict(command)}

    @app.delete("/api/commands/{name}")
    async def delete_command(name: str) -> dict:
        store = CommandStore(app.state.settings.data_dir)
        return {"ok": store.delete(name)}

    def _secrets_workspace(raw: str) -> Path:
        ws = Path(raw.strip()) if raw.strip() else Path(app.state.settings.workspace)
        if not ws.is_dir():
            raise HTTPException(status_code=400, detail=tr("api.folder_missing"))
        return ws

    @app.get("/api/secrets")
    async def get_secrets(workspace: str = "") -> dict:
        """Имена заданных секретов и замаскированные значения (полные не отдаём)."""
        from core.secrets_store import list_secrets

        ws = _secrets_workspace(workspace)
        infos = await asyncio.to_thread(list_secrets, ws)
        return {"secrets": [{"name": s.name, "masked": s.masked} for s in infos]}

    @app.post("/api/secrets")
    async def save_secret(payload: dict) -> dict:
        """Сохраняет секрет в .env рабочей папки. Значение не логируется и не возвращается."""
        from core.secrets_store import SecretError, set_secret

        ws = _secrets_workspace(str(payload.get("workspace") or ""))
        name = str(payload.get("name") or "")
        value = str(payload.get("value") or "")
        try:
            await asyncio.to_thread(set_secret, ws, name, value)
        except SecretError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return {"ok": True, "name": name.strip()}

    @app.delete("/api/secrets/{name}")
    async def delete_secret_endpoint(name: str, workspace: str = "") -> dict:
        from core.secrets_store import delete_secret

        ws = _secrets_workspace(workspace)
        return {"ok": await asyncio.to_thread(delete_secret, ws, name)}

    @app.get("/api/presets")
    async def get_presets() -> dict:
        store = PresetStore(app.state.settings.data_dir)
        return {"presets": [asdict(p) for p in store.all()]}

    @app.post("/api/presets")
    async def save_preset(payload: dict) -> dict:
        store = PresetStore(app.state.settings.data_dir)
        try:
            preset = Preset(
                name=str(payload.get("name") or ""),
                model=str(payload.get("model") or ""),
                approval_mode=str(payload.get("approval_mode") or "manual"),
                web_mode=str(payload.get("web_mode") or "auto"),
                deep_research=bool(payload.get("deep_research")),
                skills=list(payload.get("skills") or []),
            )
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=tr("api.preset_bad")) from None
        if not preset.name.strip():
            raise HTTPException(status_code=400, detail=tr("api.preset_name"))
        return {"ok": True, "preset": asdict(store.save(preset))}

    @app.delete("/api/presets/{name}")
    async def delete_preset(name: str) -> dict:
        store = PresetStore(app.state.settings.data_dir)
        return {"ok": store.delete(name)}

    @app.get("/api/skills")
    async def list_skills() -> dict:
        """Навыки: для меню композера и для раздела «Навыки» в настройках."""
        manager = SkillManager(app.state.settings)

        def collect() -> list[dict]:
            return [
                {
                    "name": skill.name,
                    "description": skill.description,
                    "scope": skill.scope,
                    "path": str(skill.path.parent),
                    "files": len(manager.bundled_files(skill)),
                }
                for skill in manager.list_skills()
            ]

        return {"skills": await asyncio.to_thread(collect), "dir": str(manager.skills_dir)}

    def _local_only(request: Request) -> None:
        # Installing skills and MCP servers runs code on this PC: the phone bridge may read,
        # but only the app window on this machine may change them.
        host = (request.client.host if request.client else "") or ""
        if host not in _LOOPBACK_HOSTS:
            raise HTTPException(status_code=403, detail=tr("api.local_only"))

    def _skills_result(fn, *args, **kwargs) -> dict:
        try:
            installed = fn(*args, **kwargs)
        except FileExistsError as exc:
            return {"ok": False, "exists": str(exc), "error": tr("api.skill_exists", names=str(exc))}
        except (ValueError, OSError, zipfile.BadZipFile) as exc:
            return {"ok": False, "error": tr("api.skill_invalid", reason=str(exc))}
        return {"ok": True, "installed": [s.name for s in installed]}

    @app.post("/api/skills/import")
    async def import_skills(request: Request, payload: dict) -> dict:
        """Навыки из файлов/папок, выбранных системным диалогом (SKILL.md, .zip, папка)."""
        _local_only(request)
        manager = SkillManager(app.state.settings)
        scope = "project" if payload.get("scope") == "project" else "global"
        overwrite = bool(payload.get("overwrite"))
        names: list[str] = []
        for raw in payload.get("paths") or []:
            result = await asyncio.to_thread(
                _skills_result, manager.import_path, Path(str(raw)), scope=scope, overwrite=overwrite
            )
            if not result["ok"]:
                return result
            names += result["installed"]
        return {"ok": True, "installed": names}

    @app.post("/api/skills/upload")
    async def upload_skills(request: Request, files: list[UploadFile], scope: str = "global",
                            overwrite: bool = False) -> dict:
        """То же для файлов, перетащенных в окно или выбранных HTML-диалогом."""
        _local_only(request)
        manager = SkillManager(app.state.settings)
        names: list[str] = []
        with tempfile.TemporaryDirectory(prefix="skill-upload-") as tmp:
            for upload in files:
                name = Path(upload.filename or "skill.md").name
                target = Path(tmp) / name
                data = await upload.read()
                if len(data) > MAX_IMPORT_BYTES:
                    return {"ok": False, "error": tr("api.skill_invalid", reason="file is larger than 25 MB")}
                await asyncio.to_thread(target.write_bytes, data)
                result = await asyncio.to_thread(
                    _skills_result, manager.import_path, target,
                    scope="project" if scope == "project" else "global", overwrite=overwrite,
                )
                if not result["ok"]:
                    return result
                names += result["installed"]
        return {"ok": True, "installed": names}

    @app.delete("/api/skills/{name}")
    async def delete_skill(request: Request, name: str, scope: str = "global") -> dict:
        _local_only(request)
        manager = SkillManager(app.state.settings)
        return {"ok": await asyncio.to_thread(manager.delete, name, scope)}

    # --------------------------------------------------------------- MCP servers

    @app.get("/api/mcp")
    async def mcp_servers() -> dict:
        mcp: MCPManager = app.state.mcp
        return {
            "servers": await asyncio.to_thread(mcp.servers),
            "config_path": str(mcp.config_path),
            "config_error": mcp.errors.get("__config__", ""),
        }

    @app.post("/api/mcp/servers")
    async def mcp_add(request: Request, payload: dict) -> dict:
        """Добавить/изменить сервер: {name, config} или {json: "<вставленный конфиг>"}."""
        _local_only(request)
        mcp: MCPManager = app.state.mcp
        try:
            if payload.get("json"):
                entries = parse_pasted_config(str(payload["json"]))
            else:
                entries = {str(payload.get("name") or "").strip(): dict(payload.get("config") or {})}
            states = [await mcp.upsert(name, cfg) for name, cfg in entries.items()]
        except (ValueError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": tr("api.mcp_invalid", reason=str(exc))}
        return {"ok": True, "servers": states}

    @app.delete("/api/mcp/servers/{name}")
    async def mcp_remove(request: Request, name: str) -> dict:
        _local_only(request)
        try:
            await app.state.mcp.remove(name)
        except KeyError:
            return {"ok": False, "error": tr("api.mcp_missing", name=name)}
        return {"ok": True}

    @app.post("/api/mcp/servers/{name}/toggle")
    async def mcp_toggle(request: Request, name: str, payload: dict) -> dict:
        _local_only(request)
        try:
            state = await app.state.mcp.set_disabled(name, not bool(payload.get("enabled")))
        except KeyError:
            return {"ok": False, "error": tr("api.mcp_missing", name=name)}
        return {"ok": True, "server": state}

    @app.post("/api/mcp/servers/{name}/restart")
    async def mcp_restart(request: Request, name: str) -> dict:
        _local_only(request)
        return {"ok": True, "server": await app.state.mcp.restart(name)}

    @app.get("/api/workspace/recent")
    async def recent_workspaces() -> dict:
        """Недавние рабочие папки — берутся из сохранённых чатов."""
        store: SessionStore = app.state.store
        sessions = await store.async_list()
        def collect() -> list[str]:
            seen: list[str] = []
            for item in sessions:
                folder = str(item.get("workspace") or "").strip()
                # Внутренние папки чата (storage/chat_files/<hex>) — не рабочие
                # папки проекта, в список недавних их не отдаём.
                if "chat_files" in folder.replace("\\", "/").lower():
                    continue
                if folder and folder not in seen and Path(folder).is_dir():
                    seen.append(folder)
            return seen

        seen = await asyncio.to_thread(collect)  # обращения к диску — вне event loop
        return {
            "recent": seen[:8],
            "default": str(app.state.settings.workspace),
            "home": str(Path.home()),
        }

    @app.get("/api/git/diff")
    async def git_diff(workspace: str = "") -> dict:
        """Текущие незакоммиченные изменения рабочей папки для панели «Изменения».

        Возвращает статус (ветка, список файлов) и unified diff. Если папка не
        git-репозиторий или git недоступен — сообщает об этом без ошибки.
        """
        from core.git import git_available, is_repo, run_git
        from core.git.repo import current_branch

        ws = workspace.strip() or str(app.state.settings.workspace)
        if not git_available():
            return {"available": False, "reason": tr("api.no_git")}
        if not await is_repo(ws):
            return {"available": False, "reason": tr("api.not_repo")}

        branch = await current_branch(ws)
        status = await run_git(["status", "--porcelain=v1"], ws)
        files = [line for line in status.stdout.splitlines() if line.strip()]
        # Дифф рабочего дерева плюс проиндексированное (HEAD) — всё, что изменилось.
        diff = await run_git(["diff", "HEAD"], ws)
        # Новые (неотслеживаемые) файлы git diff не показывает — добавим их имена.
        untracked = [ln[3:] for ln in files if ln.startswith("??")]
        return {
            "available": True,
            "branch": branch,
            "files": files,
            "diff": diff.stdout if diff.ok else "",
            "untracked": untracked,
        }

    async def _allowed_roots() -> list:
        """Разрешённые корни: настройки плюс рабочие папки сохранённых чатов."""
        store: SessionStore = app.state.store
        sessions = await store.async_list()
        folders = [str(item.get("workspace") or "") for item in sessions]
        folders.extend(getattr(app.state, "active_workspaces", set()))
        return await asyncio.to_thread(preview_roots, app.state.settings, folders)

    @app.get("/api/find-image")
    async def find_image_endpoint(q: str = "") -> dict:
        """Ищет релевантную картинку по запросу — для инлайн-маркеров ![..](img:..)."""
        query = (q or "").strip()
        if not query:
            return {"ok": False, "url": None}
        try:
            from core.research.images import find_images

            found = await find_images(query, settings=app.state.settings, limit=4)
        except Exception:  # noqa: BLE001 - поиск картинки не должен ронять ответ
            logger.debug("Поиск картинки не удался для %r", query, exc_info=True)
            return {"ok": False, "url": None}
        if not found:
            return {"ok": False, "url": None}
        best = found[0]
        return {"ok": True, "url": best["url"], "title": best.get("title", ""),
                "page": best.get("page", ""), "alternatives": [f["url"] for f in found[1:]]}

    @app.get("/api/file")
    async def read_file(path: str = "") -> dict:
        """Содержимое файла для панели превью (с подсветкой на стороне UI)."""
        roots = await _allowed_roots()
        try:
            target = await asyncio.to_thread(resolve_preview_path, path, roots)
            info = await asyncio.to_thread(load_preview, target)
        except PathNotAllowed as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True, **info}

    @app.get("/files/{file_path:path}", response_model=None)
    async def raw_file(file_path: str) -> FileResponse:
        """Сырой файл. Нужен, чтобы HTML открывался в iframe вместе со своими
        css и js: относительные ссылки разрешаются от этого же пути."""
        roots = await _allowed_roots()
        try:
            target = await asyncio.to_thread(resolve_preview_path, file_path, roots)
        except PathNotAllowed as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(target, media_type=guess_media_type(target))

    @app.get("/api/settings")
    async def get_app_settings() -> dict:
        """Настройки для окна параметров. Ключ API отдаётся только хвостом."""
        return await asyncio.to_thread(read_public_settings, app.state.settings)

    @app.post("/api/settings")
    async def save_app_settings(payload: dict) -> dict:
        """Сохраняет настройки в .env и перечитывает их."""
        try:
            settings, warnings = await asyncio.to_thread(apply_settings, payload)
        except OSError as exc:
            logger.exception("Не удалось сохранить настройки")
            return {"ok": False, "error": tr("api.settings_write", error=exc)}

        # Новые настройки должны действовать сразу, включая новые подключения.
        app.state.settings = settings
        if "bridge_lan" in payload:
            await app.state.lan.apply(settings.bridge_lan)
        if "browser_network" in payload:
            from core.browser_net import MODES, get_proxy

            net = get_proxy()
            if net is not None and settings.browser_network in MODES:
                net.default_mode = settings.browser_network
        return {
            "ok": True,
            "warnings": warnings,
            "settings": read_public_settings(settings),
            "problems": settings.problems(),
        }

    @app.get("/api/pair")
    async def pair_get(request: Request, workspace: str = "") -> dict:
        """Данные для связывания телефона: адрес, токен, ссылка и QR-код.

        Генерирует общий секрет при первом обращении. `workspace` — рабочая папка,
        которую телефон подставит по умолчанию (её знает интерфейс). Порт берём из
        запроса — это реальный порт сервера, а не значение по умолчанию из настроек.
        """
        from core.pairing import pair_info
        from core.settings import get_settings as _gs

        port = request.url.port
        info = await asyncio.to_thread(pair_info, workspace, None, port, app.state.lan.listening())
        app.state.settings = _gs()  # мог появиться новый bridge_token
        return {"ok": True, **info}

    @app.get("/api/browser/net")
    async def browser_net_status(host: str = "") -> dict:
        """How the built-in browser reaches sites (direct / through the VPN) and per-site rules."""
        from core.browser_net import get_proxy

        net = get_proxy()
        if net is None:
            return {"ok": False, "error": "the browser network proxy is not running"}
        data = net.status()
        if host:
            mode, source = net.mode_for(host)
            rule, site = net.rules.rule_for(host)
            data["host"] = {"host": host, "mode": mode, "source": source, "rule": rule or "auto", "site": site,
                            "last": net.recent.get(net.rules.normalize(host), {})}
        return {"ok": True, **data}

    @app.post("/api/browser/net")
    async def browser_net_set(request: Request, payload: dict) -> dict:
        """Set a site's route: {host, mode: auto|direct|vpn}."""
        _local_only(request)
        from core.browser_net import get_proxy

        net = get_proxy()
        if net is None:
            return {"ok": False, "error": "the browser network proxy is not running"}
        try:
            site = await asyncio.to_thread(net.rules.set, str(payload.get("host") or ""), str(payload.get("mode") or ""))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        net.forget(site)
        return {"ok": True, "site": site, "mode": payload.get("mode")}

    @app.get("/api/pair/firewall")
    async def pair_firewall() -> dict:
        """Пропустит ли брандмауэр Windows телефон к приложению."""
        from core import firewall

        return {"ok": True, **(await firewall.status())}

    @app.post("/api/pair/firewall")
    async def pair_firewall_allow(request: Request) -> dict:
        """Разрешить входящие подключения моста (Windows покажет запрос UAC)."""
        _local_only(request)
        from core import firewall

        allowed = await firewall.allow()
        return {"ok": allowed, **(await firewall.status())}

    @app.post("/api/pair/rotate")
    async def pair_rotate(request: Request, payload: dict | None = None) -> dict:
        """Перевыпустить секрет (старые связки отвалятся) и вернуть новые данные."""
        from core.pairing import pair_info, rotate_bridge_token
        from core.settings import get_settings as _gs

        workspace = (payload or {}).get("workspace", "") if payload else ""
        port = request.url.port
        await asyncio.to_thread(rotate_bridge_token)
        info = await asyncio.to_thread(pair_info, workspace, None, port, app.state.lan.listening())
        app.state.settings = _gs()
        return {"ok": True, **info}

    @app.get("/api/firefox/profiles")
    async def firefox_profiles() -> dict:
        """Профили Firefox, из которых можно перенести логины."""
        from core.firefox_import import find_profiles

        try:
            profs = await asyncio.to_thread(find_profiles)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "profiles": []}
        return {"ok": True, "profiles": [{"name": p.name, "path": p.path} for p in profs]}

    @app.get("/api/firefox/domains")
    async def firefox_domains(profile: str) -> dict:
        """Список сайтов профиля (банки/почта исключены)."""
        from core.firefox_import import list_domains

        try:
            domains = await asyncio.to_thread(list_domains, profile)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "domains": []}
        return {"ok": True, "domains": domains}

    @app.post("/api/firefox/import")
    async def firefox_import(payload: dict) -> dict:
        """Переносит куки выбранных сайтов в браузер агента."""
        from core.firefox_import import import_into_agent

        profile = str(payload.get("profile") or "")
        domains = [str(d) for d in (payload.get("domains") or [])]
        if not profile or not domains:
            return {"ok": False, "error": tr("api.ff_pick")}
        try:
            count = await import_into_agent(profile, domains)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Импорт из Firefox не удался")
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "imported": count}

    @app.post("/api/git/revert_hunk")
    async def revert_hunk(payload: dict) -> dict:
        """Откат одного ханка (reject при ревью правок): git apply --reverse."""
        import tempfile

        from core.git.repo import run_git

        ws = str(payload.get("workspace") or "").strip() or str(app.state.settings.workspace)
        file = str(payload.get("file") or "").strip()
        try:
            idx = int(payload.get("hunk"))
        except (TypeError, ValueError):
            return {"ok": False, "error": tr("api.hunk_missing_no")}
        if not file:
            return {"ok": False, "error": tr("api.file_missing")}

        top = (await run_git(["rev-parse", "--show-toplevel"], ws)).stdout.strip() or ws
        diff = await run_git(["diff", "--", file], top)
        if not diff.ok:
            return {"ok": False, "error": diff.stderr or tr("api.git_diff_failed")}

        # Разбираем на шапку файла и ханки (каждый начинается с "@@").
        lines = diff.stdout.splitlines(keepends=True)
        header, hunks, cur = [], [], None
        for ln in lines:
            if ln.startswith("@@"):
                cur = [ln]
                hunks.append(cur)
            elif cur is None:
                header.append(ln)
            else:
                cur.append(ln)
        if idx < 0 or idx >= len(hunks):
            return {"ok": False, "error": tr("api.hunk_gone")}

        patch = "".join(header) + "".join(hunks[idx])
        tmp = Path(tempfile.mkdtemp()) / "hunk.patch"
        tmp.write_text(patch, encoding="utf-8")
        try:
            res = await run_git(["apply", "--reverse", "--recount", str(tmp)], top)
        finally:
            import shutil as _sh
            _sh.rmtree(tmp.parent, ignore_errors=True)
        if not res.ok:
            return {"ok": False, "error": res.stderr or tr("api.hunk_revert_failed")}
        return {"ok": True}

    @app.get("/api/files/list")
    async def list_workspace_files(workspace: str = "", q: str = "", limit: int = 30) -> dict:
        """Файлы рабочей папки для @-упоминаний в композере (по подстроке пути)."""
        import os

        ws = (workspace.strip() or str(app.state.settings.workspace))
        root = Path(ws)
        needle = q.strip().lower()
        skip = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".agent", ".idea", ".vscode"}

        def walk() -> list[str]:
            if not root.is_dir():
                return []
            out: list[str] = []
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
                for name in filenames:
                    rel = os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/")
                    if not needle or needle in rel.lower():
                        out.append(rel)
                        if len(out) >= 400:
                            return out
            return out

        files = await asyncio.to_thread(walk)
        # Совпадения по имени файла — выше, затем короткий путь.
        files.sort(key=lambda p: (needle not in p.rsplit("/", 1)[-1].lower(), len(p)))
        return {"ok": True, "files": files[:limit]}

    @app.get("/api/files/dir")
    async def list_workspace_dir(workspace: str = "", path: str = "") -> dict:
        """Один уровень дерева рабочей папки для панели «Файлы» (ленивое раскрытие)."""
        import os

        ws = (workspace.strip() or str(app.state.settings.workspace))

        def listdir() -> dict:
            root = Path(ws).resolve()
            if not root.is_dir():
                return {"ok": False, "entries": []}
            target = (root / path).resolve() if path else root
            # Не выпускаем за пределы рабочей папки и требуем существующую папку.
            if (target != root and root not in target.parents) or not target.is_dir():
                return {"ok": False, "entries": []}
            out: list[dict] = []
            try:
                entries = list(os.scandir(target))
            except OSError:
                return {"ok": True, "entries": out}
            for entry in entries:
                try:
                    is_dir = entry.is_dir()
                    size = 0 if is_dir else entry.stat().st_size
                except OSError:
                    continue
                rel = os.path.relpath(entry.path, root).replace(os.sep, "/")
                out.append({"name": entry.name, "path": rel, "dir": is_dir, "size": size})
            # Папки сверху, затем по имени без учёта регистра.
            out.sort(key=lambda item: (not item["dir"], item["name"].lower()))
            return {"ok": True, "entries": out}

        return await asyncio.to_thread(listdir)

    def _abs_in_ws(workspace: str, rel: str) -> Path:
        from server.file_actions import resolve_in_workspace

        ws = (workspace.strip() or str(app.state.settings.workspace))
        return resolve_in_workspace(ws, rel)

    @app.get("/api/files/abspath")
    async def file_abspath(workspace: str = "", path: str = "") -> dict:
        """Абсолютный путь файла рабочей папки в нативном виде (для «копировать»)."""
        try:
            target = await asyncio.to_thread(_abs_in_ws, workspace, path)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "abspath": str(target)}

    @app.get("/api/files/openers")
    async def file_openers(workspace: str = "", path: str = "") -> dict:
        """Программы на ПК, которыми можно открыть файл («Открыть в …»)."""
        from server.file_actions import list_openers

        try:
            target = await asyncio.to_thread(_abs_in_ws, workspace, path)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "openers": []}
        openers = await asyncio.to_thread(list_openers, str(target))
        return {"ok": True, "abspath": str(target), "openers": openers}

    @app.post("/api/files/reveal")
    async def file_reveal(payload: dict) -> dict:
        """Показать файл в системном проводнике (с выделением)."""
        from server.file_actions import reveal_in_explorer

        try:
            target = await asyncio.to_thread(_abs_in_ws, payload.get("workspace", ""), payload.get("path", ""))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            await asyncio.to_thread(reveal_in_explorer, target)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    @app.post("/api/files/open")
    async def file_open(payload: dict) -> dict:
        """Открыть файл/папку: приложением по умолчанию, конкретной программой
        (`exec`), или показать системный диалог «Открыть с помощью» (`exec=__choose__`)."""
        from server.file_actions import open_path

        try:
            target = await asyncio.to_thread(_abs_in_ws, payload.get("workspace", ""), payload.get("path", ""))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        opener = str(payload.get("exec") or "__default__")
        try:
            await asyncio.to_thread(open_path, target, opener)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    @app.get("/api/update/check")
    async def check_update() -> dict:
        """Есть ли новая версия. Источник задаётся в UPDATE_URL."""
        info = await Updater(app.state.settings).check()
        return info.to_dict()

    @app.post("/api/update/install")
    async def install_update() -> dict:
        """Скачивает и ставит обновление, затем перезапускает приложение."""
        updater = Updater(app.state.settings)
        info = await updater.check()
        if not info.installable:
            return {"ok": False, "error": info.error or tr("api.update_unavailable")}

        try:
            archive = await updater.download(info)
            folder = await asyncio.to_thread(updater.unpack, archive)
            await asyncio.to_thread(updater.apply, folder)
        except Exception as exc:  # noqa: BLE001 - причину показываем пользователю
            logger.exception("Установка обновления не удалась")
            return {"ok": False, "error": str(exc)}

        return {"ok": True, "version": info.version}

    @app.get("/api/browse")
    async def browse_folders(path: str = "") -> dict:
        """Проводник по папкам на ПК для выбора рабочей директории."""
        drives = get_system_drives()
        home = str(Path.home())
        default_ws = str(app.state.settings.workspace)

        if not path.strip():
            return {
                "current": "",
                "parent": "",
                "drives": drives,
                "home": home,
                "default_workspace": default_ws,
                "folders": [{"name": d, "path": d} for d in drives],
            }

        def scan() -> dict:
            """Обход каталога — блокирующая операция, выполняется в потоке."""
            target = Path(path).expanduser().resolve()
            if not target.exists() or not target.is_dir():
                return {
                    "error": tr("api.path_missing", path=path),
                    "current": str(target),
                    "parent": str(target.parent) if target.parent != target else "",
                    "folders": [],
                    "drives": drives,
                    "home": home,
                    "default_workspace": default_ws,
                    "writable": False,
                }

            folders = []
            try:
                for item in sorted(target.iterdir(), key=lambda p: p.name.lower()):
                    if item.is_dir() and not item.name.startswith((".", "$")):
                        folders.append({"name": item.name, "path": str(item)})
            except (PermissionError, OSError) as exc:
                return {
                    "error": tr("api.no_access", path=target, error=exc),
                    "current": str(target),
                    "parent": str(target.parent) if target.parent != target else "",
                    "folders": [],
                    "drives": drives,
                    "home": home,
                    "default_workspace": default_ws,
                    "writable": False,
                }

            return {
                "current": str(target),
                "parent": str(target.parent) if target.parent != target else "",
                "drives": drives,
                "home": home,
                "default_workspace": default_ws,
                "folders": folders,
                # Права проверяем сразу: выбрать папку только для чтения можно,
                # но пользователь должен узнать об этом до запуска задачи.
                "writable": os.access(target, os.W_OK),
            }

        return await asyncio.to_thread(scan)

    @app.websocket("/ws")
    async def agent_socket(websocket: WebSocket) -> None:
        # Мост к телефону (Фаза 4): localhost (веб-интерфейс) пускаем без токена,
        # удалённые подключения — только по общему секрету bridge_token.
        if not _bridge_authorized(websocket):
            await websocket.close(code=4401)
            return
        connection = Connection(websocket, app.state.registry)
        await connection.serve()

    @app.websocket("/ws/terminal")
    async def terminal_socket(websocket: WebSocket) -> None:
        await serve_terminal(websocket)

    @app.get("/swarm", response_model=None)
    async def swarm_page() -> FileResponse | JSONResponse:
        """Страница эксперимента «групповой чат агентов»."""
        page = STATIC_DIR / "swarm.html"
        if page.exists():
            return FileResponse(page)
        return JSONResponse({"status": "error", "hint": "static/swarm.html не найден"})

    @app.get("/api/swarm/config")
    async def swarm_config() -> JSONResponse:
        """Стартовая конфигурация редактора команды (команда по умолчанию + инструменты)."""
        return JSONResponse(default_config())

    @app.websocket("/ws/swarm")
    async def swarm_socket(websocket: WebSocket) -> None:
        if not _bridge_authorized(websocket):
            await websocket.close(code=4401)
            return
        await SwarmConnection(websocket).serve()

    return app


app = create_app()
