"""F4: теневой git и откат файловых эффектов shell-команд."""

from __future__ import annotations

import subprocess

import pytest

from core.checkpoints import CheckpointStore
from core.shadow_git import ShadowGit, _parse_name_status
from core.tools.base import ToolContext
from core.tools.builtin.shell import ExecuteCommandTool


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True, timeout=10)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


needs_git = pytest.mark.skipif(not _git_available(), reason="git не установлен")


def test_parse_name_status():
    raw = "M\0a.txt\0A\0new.txt\0D\0gone.txt\0"
    assert _parse_name_status(raw) == [("M", "a.txt"), ("A", "new.txt"), ("D", "gone.txt")]


# ------------------------------------------------------- CheckpointStore.record_prior


def test_record_prior_restores_created_file(settings, tmp_path):
    store = CheckpointStore(settings.data_dir, "s1", settings.workspace)
    created = settings.workspace / "gen.txt"
    created.write_text("создано командой", encoding="utf-8")
    # existed=False → откат удаляет созданное.
    store.record_prior("gen.txt", "command", None, existed=False)
    store.restore_latest("gen.txt")
    assert not created.exists()


def test_record_prior_restores_modified_file(settings):
    store = CheckpointStore(settings.data_dir, "s1", settings.workspace)
    target = settings.workspace / "conf.ini"
    target.write_text("НОВОЕ содержимое", encoding="utf-8")
    store.record_prior("conf.ini", "command", "СТАРОЕ содержимое".encode(), existed=True)
    store.restore_latest("conf.ini")
    assert target.read_text(encoding="utf-8") == "СТАРОЕ содержимое"


# ----------------------------------------------------------------- ShadowGit


@needs_git
def test_shadow_git_roundtrip(settings):
    ws = settings.workspace
    (ws / "keep.txt").write_text("original", encoding="utf-8")
    shadow = ShadowGit("s2", ws, settings.data_dir)
    before = shadow.snapshot()

    (ws / "made.txt").write_text("новый", encoding="utf-8")
    (ws / "keep.txt").write_text("changed", encoding="utf-8")

    # changed_since сравнивает sha с HEAD, поэтому сначала фиксируем «после».
    shadow.snapshot()
    status = {path: st for st, path in shadow.changed_since(before)}
    assert status.get("made.txt") == "A"
    assert status.get("keep.txt") == "M"
    # Прежнее содержимое доступно для изменённого, отсутствует для созданного.
    assert shadow.show(before, "keep.txt") == b"original"
    assert shadow.show(before, "made.txt") is None


# ------------------------------------------- интеграция: команда → чекпоинт → откат


@needs_git
@pytest.mark.asyncio
async def test_execute_command_registers_rollback(settings):
    settings.approval_mode = "bypass"
    store = CheckpointStore(settings.data_dir, "run1", settings.workspace)

    events: list = []

    async def emitter(ev):
        events.append(ev)

    ctx = ToolContext(settings=settings, run_id="run1", checkpoints=store, emitter=emitter)

    # Команда, создающая файл (кроссплатформенно через python; ascii — чтобы не
    # зависеть от кодировки консоли Windows).
    py = "import pathlib; pathlib.Path('artifact.txt').write_text('from-command')"
    cmd = f'python -c "{py}"'
    res = await ExecuteCommandTool().run(ExecuteCommandTool.Args(command=cmd), ctx)
    assert res.ok

    created = settings.workspace / "artifact.txt"
    assert created.read_text(encoding="utf-8") == "from-command"
    # Зарегистрирован чекпоинт на созданный файл.
    assert any(getattr(e, "path", "") == "artifact.txt" for e in events)

    # Откат удаляет созданное командой.
    store.restore_latest("artifact.txt")
    assert not created.exists()
