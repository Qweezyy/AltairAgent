"""Точка входа локального ИИ-агента.

Режимы:
    python main.py                  — нативное Windows-приложение (Desktop Window) + сервер
    python main.py --server         — только веб-сервер (http://127.0.0.1:8000)
    python main.py --cli "задача"   — выполнить одну задачу в консоли и выйти
    python main.py --repl           — диалог в консоли
    python main.py --swarm "задача" — эксперимент: групповой чат равноправных агентов
    python main.py --check          — диагностика конфигурации и инструментов
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from core.logging_setup import get_logger, setup_logging
from core.settings import get_settings

logger = get_logger("main")


def _configure_event_loop() -> None:
    """На Windows подпроцессы (shell, python, MCP) работают только на Proactor."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        # No child process should flash a console window on the user's screen.
        from core.utils.proc import install_no_window_default

        install_no_window_default()


def _ensure_std_streams() -> None:
    """Gives a windowed build real stdout/stderr.

    PyInstaller's --windowed exe starts with sys.stdout/sys.stderr = None. When the Tauri
    shell (itself a GUI process) launches the backend, uvicorn's startup calls
    sys.stdout.isatty() and crashes — and a windowed PyInstaller app reports that with a
    modal "Unhandled exception" dialog, so the backend just hangs and the window never
    loads. Anything written here goes nowhere; the log file keeps the record.
    """
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115 — lives for the process


def _configure_console() -> None:
    """Консоль Windows по умолчанию не UTF-8 — иначе кириллица превращается в мусор."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


# --------------------------------------------------------------- сервер


#: The running server, so a graceful stop can be asked from another thread.
_SERVER = None
#: A graceful stop that has not finished by then is cut short.
_GRACEFUL_EXIT_SEC = 8.0
_exit_once = threading.Lock()


def run_server(host: str, port: int) -> None:
    import uvicorn

    from server.app import app

    global _SERVER
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", ws_ping_interval=30,
                            timeout_graceful_shutdown=3)
    _SERVER = uvicorn.Server(config)
    _SERVER.run()


def _graceful_exit(reason: str) -> None:
    """Stops the server the normal way: the app's shutdown stops the chats' runs as "the app
    closed" (their waits stay scheduled) and stores every chat. A kill would lose the chats'
    last seconds and leave the waits looking like a crash. Cut short if it takes too long."""
    if not _exit_once.acquire(blocking=False):
        return
    logger.info("%s: stopping the backend.", reason)
    if _SERVER is not None:
        # The main thread leaves run_server() when the server has stopped and ends the
        # process; this thread only makes sure it ends even if something hangs.
        _SERVER.should_exit = True
        time.sleep(_GRACEFUL_EXIT_SEC)
    os._exit(0)


def _stop_when_stdin_closes() -> None:
    """The Tauri shell holds the write end of our stdin: it closes it to ask for a graceful
    exit (and it closes by itself when the shell dies). Only with --stop-on-stdin-eof: a
    backend started otherwise may have no stdin at all, which reads as closed at once."""

    def watch() -> None:
        try:
            while os.read(0, 1024):
                pass
        except OSError as exc:
            logger.warning("stdin is not readable (%s): the shell's graceful stop is off", exc)
            return
        _graceful_exit("The shell asked to close")

    # A child inheriting this busy stdin hangs on Windows: children get their own (DEVNULL).
    from core.utils.proc import install_devnull_stdin_default

    install_devnull_stdin_default()
    threading.Thread(target=watch, name="stdin-watch", daemon=True).start()


def _launch_native_windows_app(url: str) -> bool:
    """Запускает веб-интерфейс в отдельном нативном окне Windows (App Mode)."""
    candidates = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
        shutil.which("msedge"),
        shutil.which("chrome"),
    ]
    for exe in candidates:
        if exe and os.path.exists(exe):
            try:
                subprocess.Popen(
                    [exe, f"--app={url}", "--window-size=1340,860", "--name=LocalAIAgent"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except OSError:
                continue
    return False


def _find_tauri_shell() -> str | None:
    """Путь к нативной оболочке Tauri (Altair), если она собрана/лежит рядом.

    Prod: собранный `Altair.exe` кладётся рядом с бэкендом. Dev: берём бинарь из
    `desktop/src-tauri/target/{release,debug}` (release сначала — он без отладки).
    """
    if sys.platform == "win32":
        prod_name, dev_names = "Altair.exe", ("Altair.exe", "app.exe")
    else:
        prod_name, dev_names = "Altair", ("Altair", "app")

    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).parent
        candidates += [here / prod_name, here.parent / prod_name]
    else:
        target = Path(__file__).resolve().parent / "desktop" / "src-tauri" / "target"
        for profile in ("release", "debug"):
            candidates += [target / profile / name for name in dev_names]

    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def _wait_for_server(host: str, port: int, timeout: float = 45.0) -> bool:
    """Ждём, пока сервер начнёт слушать порт, и только потом открываем окно.

    Холодный старт собранного бандла (импорт playwright/pyright/tree-sitter и т.д.)
    заметно дольше секунды, а окно/Edge App Mode не переоткрывает страницу после
    ERR_CONNECTION_REFUSED — поэтому важно дождаться реальной готовности, а не спать
    фиксированное время. Возвращает True, если порт открылся до таймаута.
    """
    connect_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((connect_host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def run_desktop(host: str, port: int, bind_host: str | None = None) -> None:
    """Запуск настоящего Windows-приложения в отдельном окне.

    `host` — адрес для окна/браузера (всегда достижимый, обычно 127.0.0.1).
    `bind_host` — на чём слушает сервер (0.0.0.0 при доступе по сети для телефона).
    """
    bind_host = bind_host or host
    url = f"http://{host}:{port}"

    # 1. Нативная оболочка Tauri (Altair): своя рамка/кнопки окна в расцветке
    #    приложения, наша иконка, весь текущий UI. Оболочка сама поднимает
    #    бэкенд (`--server`) на свободном порту — свой сервер тут НЕ стартуем.
    shell = _find_tauri_shell()
    if shell:
        try:
            subprocess.Popen([shell], cwd=str(Path(shell).parent)).wait()
            return
        except OSError as exc:
            logger.warning("Не удалось запустить оболочку Tauri (%s) — запасной режим", exc)

    # 2. Запасной режим (оболочка не собрана): окно Edge/Chrome в режиме App
    #    поверх нашего сервера. Здесь бэкенд поднимаем сами.
    thread = threading.Thread(target=run_server, args=(bind_host, port), daemon=True)
    thread.start()
    _wait_for_server(host, port)  # ждём реальной готовности порта, не фикс. сон

    launched = _launch_native_windows_app(url)
    if not launched:
        webbrowser.open(url)

    # Держим процесс живым
    try:
        while thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        pass


# --------------------------------------------------------------- консоль


async def _build_console_agent(model: str | None, workspace: str | None = None):
    """Готовит агента для консольных режимов. Возвращает (runner, cleanup)."""
    from core.agent.runner import AgentRunner
    from core.events import (
        Event,
        ReasoningDelta,
        RunFailed,
        TextDelta,
        ToolFinished,
        ToolStarted,
    )
    from core.llm import build_llm_client
    from core.mcp.manager import MCPManager
    from core.security.console import console_approver
    from core.tools import build_default_registry
    from core.tools.builtin.web import close_http_client

    settings = get_settings()
    if workspace:
        # Рабочая папка для одной консольной задачи (аналог выбора папки в UI).
        settings = settings.for_workspace(workspace)
        print(f"Рабочая папка: {settings.workspace}")
    registry = build_default_registry()
    mcp = MCPManager(settings)

    try:
        for tool in await mcp.start():
            registry.add(tool)
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось инициализировать MCP")

    llm = build_llm_client(model=model)

    async def console_emitter(event: Event) -> None:
        if isinstance(event, TextDelta):
            sys.stdout.write(event.text)
            sys.stdout.flush()
        elif isinstance(event, ReasoningDelta):
            pass  # в консоли не шумим рассуждениями
        elif isinstance(event, ToolStarted):
            print(f"\n[tool -> {event.name}]", flush=True)
        elif isinstance(event, ToolFinished):
            mark = "ok" if event.ok else "error"
            print(f"[tool <- {event.name}: {mark} ({event.duration_ms} ms)]", flush=True)
        elif isinstance(event, RunFailed):
            print(f"\n[ОШИБКА: {event.message}]", file=sys.stderr, flush=True)

    runner = AgentRunner(
        llm=llm,
        registry=registry,
        settings=settings,
        emitter=console_emitter,
        approver=console_approver,
    )

    async def cleanup() -> None:
        await llm.aclose()
        await mcp.stop()
        await close_http_client()

    return runner, cleanup


async def run_cli(task: str, model: str | None, workspace: str | None = None) -> int:
    runner, cleanup = await _build_console_agent(model, workspace)
    try:
        print(f"Задача: {task}\n---")
        result = await runner.run(task)
        print("\n---")
        print(f"Готово за {result.duration_ms} мс ({result.steps} шагов).")
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка при выполнении задачи")
        print(f"\nФатальная ошибка: {exc}", file=sys.stderr)
        return 1
    finally:
        await cleanup()


async def run_repl(model: str | None, workspace: str | None = None) -> None:
    runner, cleanup = await _build_console_agent(model, workspace)
    print("Altair (консольный режим). Для выхода введите 'exit' или 'quit'.\n")
    try:
        while True:
            try:
                # input() блокирующий: в async-контексте только через поток
                task = (await asyncio.to_thread(input, "Агент> ")).strip()
            except (EOFError, KeyboardInterrupt):
                print("\nВыход.")
                break
            if not task:
                continue
            if task.lower() in {"exit", "quit", "q", "выход"}:
                break
            if task.lower() == "clear":
                runner.session.reset()
                print("[Контекст диалога очищен]")
                continue
            print("---")
            await runner.run(task)
            print("\n---")
    finally:
        await cleanup()


# ----------------------------------------------------------- групповой чат


async def run_swarm(
    task: str,
    *,
    model: str | None = None,
    workspace: str | None = None,
    team_path: str | None = None,
    rounds: int = 6,
) -> int:
    """Эксперимент «групповой чат агентов»: несколько равноправных агентов решают
    одну задачу ОДНОВРЕМЕННО, общаясь только через общий чат."""
    from core.events import (
        Event,
        LogEvent,
        SwarmActivity,
        SwarmFinished,
        SwarmMessage,
        SwarmTurnStarted,
    )
    from core.security.console import console_approver
    from core.swarm import Swarm, build_default_team, load_team

    settings = get_settings()
    if workspace:
        settings = settings.for_workspace(workspace)
    print(f"Рабочая папка: {settings.workspace}")

    try:
        members = load_team(team_path) if team_path else build_default_team()
    except (OSError, ValueError) as exc:
        print(f"Не удалось загрузить команду: {exc}", file=sys.stderr)
        return 1

    print(f"Команда ({len(members)}): " + ", ".join(f"{m.name} [{m.role}]" for m in members))
    print(f"Задача: {task}\n" + "=" * 60)

    async def console_emitter(event: Event) -> None:
        # В консоли эксперимента показываем «социальный» слой: чат и действия.
        # Участники работают параллельно, поэтому строки естественно чередуются.
        if isinstance(event, SwarmMessage):
            print(f"\n💬 {event.author}: {event.text}", flush=True)
        elif isinstance(event, SwarmTurnStarted):
            print(f"  · {event.member} начал ход…", flush=True)
        elif isinstance(event, SwarmActivity):
            if event.tool == "ждёт коллег":
                print(f"  · {event.member} ждёт коллег", flush=True)
            else:
                mark = "ok" if event.ok else "ошибка"
                tail = f" — {event.detail}" if event.detail else ""
                print(f"     [{event.member}] {event.tool}: {mark}{tail}", flush=True)
        elif isinstance(event, SwarmFinished):
            print(f"\n🏁 {event.reason}", flush=True)
        elif isinstance(event, LogEvent) and event.level in ("warning", "error"):
            print(f"[{event.level}] {event.text}", flush=True)

    swarm = Swarm(
        task,
        members,
        settings=settings,
        model=model,
        emitter=console_emitter,
        approver=console_approver,
        max_rounds=rounds,
    )
    try:
        result = await swarm.run()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка в групповом чате")
        print(f"\nФатальная ошибка: {exc}", file=sys.stderr)
        return 1

    print("=" * 60)
    print(f"Итог: {result.stopped_reason}. Раундов: {result.rounds_run}, "
          f"сообщений: {len(result.messages)}.")
    return 0


# ---------------------------------------------------------------- check


async def run_check() -> int:
    """Диагностика: проверяет ключ, пути, инструменты, MCP."""
    from core.mcp.manager import MCPManager
    from core.skills.manager import SkillManager
    from core.tools import build_default_registry
    from core.tools.builtin.web import close_http_client

    settings = get_settings()
    registry = build_default_registry()
    skills = SkillManager(settings)
    mcp = MCPManager(settings)

    print("=== Конфигурация ===")
    print(f"  workspace:      {settings.workspace}")
    print(f"  модель:         {settings.default_model}")
    print(f"  base_url:       {settings.llm_base_url}")
    print(f"  ключ задан:     {'да' if settings.openrouter_api_key else 'НЕТ'}")
    print(f"  подтверждения:  {settings.approval_mode}")
    print(f"  лимит шагов:    {settings.max_steps}")

    problems = settings.problems()
    if problems:
        print("\n=== Предупреждения ===")
        for issue in problems:
            print(f"  ! {issue}")

    print(f"\n=== Встроенные инструменты ({len(registry)}) ===")
    for tool in sorted(registry.all(), key=lambda t: t.name):
        danger = "!" if tool.dangerous else " "
        print(f"  {danger} {tool.name}")

    skill_list = skills.list_skills()
    print(f"\n=== Навыки ({len(skill_list)}) ===")
    for sk in skill_list:
        print(f"  - {sk.name}: {sk.description[:80]}")

    try:
        tools = await mcp.start()
        print(f"\n=== MCP ({len(mcp.status())} серверов, {len(tools)} инструментов) ===")
        for tool in tools:
            print(f"  + {tool.name}")
    except Exception as exc:  # noqa: BLE001
        print(f"\n=== MCP: ошибка инициализации ({exc}) ===")
    finally:
        await mcp.stop()
        await close_http_client()

    print("\nГотово.")
    return 0 if not problems else 1


# --------------------------------------------------------------- main


def _exit_with_parent(parent_pid: int) -> None:
    """Exits this backend when the process that started it goes away.

    The Tauri shell kills the backend on a normal exit, but not when it crashes or is
    killed. An orphaned backend keeps port 8137, the next launch falls back to a random
    port — a different origin — and the UI loses its saved state (theme, language,
    finished onboarding). Watching the parent closes that gap.
    """

    def watch() -> None:
        if sys.platform == "win32":
            import ctypes

            synchronize = 0x00100000
            handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, parent_pid)
            if not handle:
                return  # parent already gone or not accessible: do not guess
            ctypes.windll.kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
        else:
            while True:
                try:
                    os.kill(parent_pid, 0)
                except OSError:
                    break
                time.sleep(2)
        _graceful_exit(f"The shell (PID {parent_pid}) has exited")

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


def _prepare_user_data() -> None:
    """Первый запуск собранного приложения: создаём .env и раскладываем навыки.

    Внутри пакета файлы только для чтения и пропадают при обновлении, поэтому
    .env и встроенные навыки копируются в папку данных пользователя.
    """
    if not getattr(sys, "frozen", False):
        return

    settings = get_settings()
    exe_dir = Path(sys.executable).parent

    # 1. .env — только если ещё нет (не затираем ключ пользователя).
    target = settings.app_dir / ".env"
    if not target.exists():
        sample = exe_dir / "_internal" / ".env.example"
        if not sample.exists():
            sample = exe_dir / ".env.example"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if sample.exists():
                shutil.copy2(sample, target)
            else:
                target.write_text("OPENROUTER_API_KEY=\n", encoding="utf-8")
            logger.info("Создан файл настроек: %s", target)
        except OSError as exc:  # pragma: no cover
            logger.warning("Не удалось создать .env: %s", exc)

    # 2. Навыки — проверяем КАЖДЫЙ запуск, а не только при первом. Иначе новые
    #    навыки из обновления не дойдут до тех, у кого .env уже создан.
    #    Существующие не трогаем (не затираем правки пользователя).
    _copy_bundled_skills(exe_dir, settings.skills_dir)


def _copy_bundled_skills(exe_dir: Path, target_dir: Path) -> int:
    """Копирует встроенные навыки, которых ещё нет у пользователя. Возвращает
    число добавленных."""
    bundled = exe_dir / "_internal" / "skills"
    if not bundled.is_dir():
        bundled = exe_dir / "skills"
    if not bundled.is_dir():
        return 0

    added = 0
    for source in bundled.iterdir():
        destination = target_dir / source.name
        if source.is_dir() and (source / "SKILL.md").exists() and not destination.exists():
            try:
                shutil.copytree(source, destination)
                added += 1
            except OSError as exc:  # pragma: no cover
                logger.warning("Не удалось скопировать навык %s: %s", source.name, exc)
    if added:
        logger.info("Добавлено встроенных навыков: %d", added)
    return added


def main() -> int:
    _ensure_std_streams()
    _configure_event_loop()
    _configure_console()
    _prepare_user_data()
    setup_logging()

    parser = argparse.ArgumentParser(description="Altair — локальный ИИ-агент")
    parser.add_argument("--server", action="store_true", help="Запустить только веб-сервер")
    parser.add_argument("--cli", type=str, metavar="TASK", help="Выполнить одну задачу и выйти")
    parser.add_argument("--repl", action="store_true", help="Интерактивный диалог в терминале")
    parser.add_argument(
        "--swarm", type=str, metavar="TASK",
        help="Эксперимент: групповой чат равноправных агентов над одной задачей",
    )
    parser.add_argument("--team", type=str, default=None, help="JSON-файл с описанием команды для --swarm")
    parser.add_argument(
        "--rounds", type=int, default=6,
        help="Макс. ходов на одного участника для --swarm (по умолчанию 6); работают одновременно",
    )
    parser.add_argument("--check", action="store_true", help="Проверить окружение и выйти")
    parser.add_argument("--model", type=str, default=None, help="Переопределить модель")
    parser.add_argument("--workspace", type=str, default=None, help="Рабочая папка проекта для этой задачи")
    parser.add_argument("--host", type=str, default=None, help="Хост (по умолчанию из .env)")
    parser.add_argument("--port", type=int, default=None, help="Порт (по умолчанию из .env)")
    parser.add_argument(
        "--parent-pid", type=int, default=None, help="Завершиться вместе с этим процессом (оболочка Tauri)"
    )
    parser.add_argument("--stop-on-stdin-eof", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--browser-cdp-port", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--browser-net-port", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--browser-dir", type=str, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.parent_pid:
        _exit_with_parent(args.parent_pid)
        from core import updater

        updater.SHELL_PID = args.parent_pid  # the update waits for the window's process too
    if args.stop_on_stdin_eof:
        _stop_when_stdin_closes()
    if args.browser_net_port:
        # The shell baked this port into the browser's PAC script (core/browser_net.py).
        from core.browser_net import configure as configure_net

        configure_net(args.browser_net_port)
    if args.browser_cdp_port and args.browser_dir:
        # The Tauri shell hosts the built-in browser: its tabs are WebView2 webviews in
        # the app window, reachable over this DevTools port (core/browser_session.py).
        from core.browser_session import configure_embedded

        configure_embedded(args.browser_cdp_port, Path(args.browser_dir))

    settings = get_settings()
    host = args.host or settings.host
    port = args.port or settings.port

    # The phone bridge does not change what the main server binds: the app window is always
    # on 127.0.0.1, and with "access over the network" on, server/lan_bridge.py adds listeners
    # on the PC's network addresses (live, no restart). The shell passes --host 127.0.0.1, which
    # used to keep the bridge loopback-only even when it was switched on.
    bind_host = host
    from server.lan_bridge import configure as configure_lan

    configure_lan(port)

    if args.check:
        return asyncio.run(run_check())
    if args.cli:
        return asyncio.run(run_cli(args.cli, model=args.model, workspace=args.workspace))
    if args.swarm:
        return asyncio.run(
            run_swarm(
                args.swarm,
                model=args.model,
                workspace=args.workspace,
                team_path=args.team,
                rounds=args.rounds,
            )
        )
    if args.repl:
        asyncio.run(run_repl(model=args.model, workspace=args.workspace))
        return 0
    if args.server:
        run_server(bind_host, port)
        return 0

    run_desktop(host, port, bind_host=bind_host)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
