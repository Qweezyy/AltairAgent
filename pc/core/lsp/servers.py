"""Реестр language server'ов и менеджер живых сессий.

pyright для Python идёт в комплекте (зависимость проекта). Серверы для других
языков используются, только если установлены и доступны в PATH, — иначе инструмент
честно сообщает, что сервер нужно поставить.
"""

from __future__ import annotations

import asyncio
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

from core.logging_setup import get_logger
from core.lsp.client import LspClient, LspError

logger = get_logger("lsp.servers")


@dataclass(frozen=True)
class ServerSpec:
    language_id: str
    #: Человекочитаемое имя и подсказка по установке, если сервера нет.
    label: str
    install_hint: str


#: Суффикс файла -> какой сервер и как называется его исполняемый файл в PATH.
_BY_SUFFIX: dict[str, tuple[str, ServerSpec]] = {
    ".py": ("pyright-langserver", ServerSpec("python", "pyright", "pip install pyright")),
    ".pyi": ("pyright-langserver", ServerSpec("python", "pyright", "pip install pyright")),
    ".ts": (
        "typescript-language-server",
        ServerSpec("typescript", "typescript-language-server", "npm i -g typescript-language-server typescript"),
    ),
    ".tsx": (
        "typescript-language-server",
        ServerSpec("typescriptreact", "typescript-language-server", "npm i -g typescript-language-server typescript"),
    ),
    ".js": (
        "typescript-language-server",
        ServerSpec("javascript", "typescript-language-server", "npm i -g typescript-language-server typescript"),
    ),
    ".go": ("gopls", ServerSpec("go", "gopls", "go install golang.org/x/tools/gopls@latest")),
    ".rs": ("rust-analyzer", ServerSpec("rust", "rust-analyzer", "rustup component add rust-analyzer")),
}


class ServerUnavailable(RuntimeError):
    """Для языка файла нет установленного сервера."""


def _pyright_langserver_argv() -> list[str] | None:
    """Команда запуска pyright-langserver.

    В обычной установке это консольный скрипт в PATH. В собранном exe его нет —
    тогда строим команду из самого пакета pyright (node + langserver.index.js).
    """
    exe = shutil.which("pyright-langserver")
    if exe:
        return [exe, "--stdio"]
    try:
        from pyright import node as pnode
        from pyright._utils import install_pyright

        pkg = install_pyright((), quiet=True)
        js = pkg / "langserver.index.js"
        strategy = pnode._resolve_strategy("node")  # noqa: SLF001 - иначе node не найти
        node_path = getattr(strategy, "path", None)
        if node_path and js.exists():
            return [str(node_path), str(js), "--stdio"]
    except Exception as exc:  # noqa: BLE001 - любой сбой = сервер недоступен
        logger.info("pyright-langserver не собрать из пакета: %s", exc)
    return None


def _server_argv(binary: str) -> list[str] | None:
    if binary == "pyright-langserver":
        return _pyright_langserver_argv()
    exe = shutil.which(binary)
    if exe:
        # У большинства LSP-серверов stdio-режим по умолчанию; tsserver требует флаг.
        if binary == "typescript-language-server":
            return [exe, "--stdio"]
        return [exe]
    return None


def resolve_spec(path: Path) -> tuple[list[str], ServerSpec]:
    """Возвращает (команда запуска, спецификация) для файла или бросает."""
    entry = _BY_SUFFIX.get(path.suffix.lower())
    if entry is None:
        raise ServerUnavailable(
            f"Для «{path.suffix}» нет настроенного language server. "
            f"Поддерживаются: {', '.join(sorted(_BY_SUFFIX))}."
        )
    binary, spec = entry
    argv = _server_argv(binary)
    if argv is None:
        raise ServerUnavailable(
            f"Language server для {spec.label} не найден. Установите: {spec.install_hint}"
        )
    return argv, spec


class LspManager:
    """Держит по одному живому серверу на пару (язык, корень проекта)."""

    def __init__(self) -> None:
        self._clients: dict[tuple[str, str], LspClient] = {}
        self._lock = asyncio.Lock()
        self._sync_lock = threading.Lock()

    async def client_for(self, path: Path, workspace: Path) -> tuple[LspClient, ServerSpec]:
        argv, spec = resolve_spec(path)
        key = (spec.language_id, str(workspace.resolve()))  # noqa: ASYNC240 - дешёвый путь
        async with self._lock:
            client = self._clients.get(key)
            if client is None:
                client = LspClient(argv, workspace, spec.language_id)
                await client.start(asyncio.get_running_loop())
                self._clients[key] = client
            return client, spec

    def shutdown(self) -> None:
        with self._sync_lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            try:
                client.close()
            except LspError:
                pass


_manager: LspManager | None = None


def get_lsp_manager() -> LspManager:
    global _manager
    if _manager is None:
        _manager = LspManager()
    return _manager
