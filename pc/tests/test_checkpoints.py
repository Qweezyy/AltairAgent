"""Снимки файлов и откат правок."""

from __future__ import annotations

import pytest

from core.checkpoints import Checkpoint, CheckpointStore
from core.tools.base import ToolContext
from core.tools.builtin.files import DeletePathTool, EditFileTool, WriteFileTool


@pytest.fixture
def store(settings):
    return CheckpointStore(settings.storage_dir, "sess1", settings.workspace)


# ------------------------------------------------------------- хранилище


def test_edit_can_be_rolled_back(store, settings):
    target = settings.workspace / "code.py"
    target.write_text("версия 1", encoding="utf-8")

    store.snapshot(target, "code.py", "edit")
    target.write_text("версия 2", encoding="utf-8")

    message = store.restore_latest("code.py")
    assert target.read_text(encoding="utf-8") == "версия 1"
    assert "code.py" in message


def test_undo_stack_goes_deeper_with_each_restore(store, settings):
    """Несколько правок подряд откатываются по одной, от новой к старой."""
    target = settings.workspace / "doc.txt"
    target.write_text("A", encoding="utf-8")

    store.snapshot(target, "doc.txt", "edit")
    target.write_text("B", encoding="utf-8")
    store.snapshot(target, "doc.txt", "edit")
    target.write_text("C", encoding="utf-8")

    store.restore_latest("doc.txt")
    assert target.read_text(encoding="utf-8") == "B"
    store.restore_latest("doc.txt")
    assert target.read_text(encoding="utf-8") == "A"


def test_undo_of_creation_deletes_the_file(store, settings):
    """Файла не было — откат его создания означает удалить созданное."""
    target = settings.workspace / "new.txt"
    store.snapshot(target, "new.txt", "write")  # файла ещё нет
    target.write_text("создан агентом", encoding="utf-8")

    message = store.restore_latest("new.txt")
    assert not target.exists()
    assert "удал" in message.lower()


def test_undo_of_deletion_restores_the_file(store, settings):
    target = settings.workspace / "gone.txt"
    target.write_text("важные данные", encoding="utf-8")

    store.snapshot(target, "gone.txt", "delete")
    target.unlink()

    store.restore_latest("gone.txt")
    assert target.read_text(encoding="utf-8") == "важные данные"


def test_restore_without_snapshot_is_reported(store):
    with pytest.raises(LookupError, match="Нет сохранённых"):
        store.restore_latest("нет-такого.txt")


def test_binary_file_survives_snapshot(store, settings):
    """Снимок хранит байты, а не текст — картинка откатывается без порчи."""
    target = settings.workspace / "logo.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff\xfe")

    store.snapshot(target, "logo.png", "write")
    target.write_bytes(b"corrupted")

    store.restore_latest("logo.png")
    assert target.read_bytes() == b"\x89PNG\r\n\x1a\n\x00\xff\xfe"


def test_snapshot_survives_new_store_instance(settings):
    """Снимок пишется на диск — откат работает и в новом сеансе."""
    target = settings.workspace / "persist.txt"
    target.write_text("оригинал", encoding="utf-8")

    first = CheckpointStore(settings.storage_dir, "sess-x", settings.workspace)
    first.snapshot(target, "persist.txt", "edit")
    target.write_text("изменено", encoding="utf-8")

    second = CheckpointStore(settings.storage_dir, "sess-x", settings.workspace)
    second.restore_latest("persist.txt")
    assert target.read_text(encoding="utf-8") == "оригинал"


def test_huge_file_is_marked_unrecoverable(store, settings, monkeypatch):
    import core.checkpoints as cp

    monkeypatch.setattr(cp, "MAX_BLOB_BYTES", 10)
    target = settings.workspace / "big.txt"
    target.write_text("x" * 100, encoding="utf-8")

    checkpoint = store.snapshot(target, "big.txt", "write")
    assert not checkpoint.recoverable
    with pytest.raises(RuntimeError, match="слишком больш"):
        store.restore_latest("big.txt")


def test_restore_refuses_paths_outside_workspace(store, settings):
    """Снимок с путём наружу не должен позволить писать вне песочницы."""
    store._records.append(
        Checkpoint(id="x", path="../evil.txt", op="write", existed=False, recoverable=True, size=0, ts=0)
    )
    with pytest.raises(RuntimeError, match="вне рабочей"):
        store.restore_latest("../evil.txt")


# ---------------------------------------------------- интеграция с инструментами


async def test_write_file_makes_a_checkpoint(settings):
    from core.events import CheckpointCreated

    events = []

    async def emitter(event):
        events.append(event)

    store = CheckpointStore(settings.storage_dir, "run1", settings.workspace)
    ctx = ToolContext(settings=settings, emitter=emitter, checkpoints=store)

    (settings.workspace / "app.py").write_text("старое", encoding="utf-8")
    await WriteFileTool().invoke({"path": "app.py", "content": "новое"}, ctx)

    assert any(isinstance(e, CheckpointCreated) and e.path == "app.py" for e in events)
    # А откат вернёт прежнее содержимое.
    store.restore_latest("app.py")
    assert (settings.workspace / "app.py").read_text(encoding="utf-8") == "старое"


async def test_edit_file_makes_a_checkpoint(settings):
    store = CheckpointStore(settings.storage_dir, "run2", settings.workspace)
    ctx = ToolContext(settings=settings, checkpoints=store)

    target = settings.workspace / "mod.py"
    target.write_text("привет мир", encoding="utf-8")
    await EditFileTool().invoke({"path": "mod.py", "old_text": "мир", "new_text": "код"}, ctx)
    assert target.read_text(encoding="utf-8") == "привет код"

    store.restore_latest("mod.py")
    assert target.read_text(encoding="utf-8") == "привет мир"


async def test_delete_then_undo_via_tool(settings):
    store = CheckpointStore(settings.storage_dir, "run3", settings.workspace)
    ctx = ToolContext(settings=settings, checkpoints=store, approver=_always_yes)

    target = settings.workspace / "temp.txt"
    target.write_text("не потерять", encoding="utf-8")
    settings.approval_mode = "manual"
    await DeletePathTool().invoke({"path": "temp.txt"}, ctx)
    assert not target.exists()

    store.restore_latest("temp.txt")
    assert target.read_text(encoding="utf-8") == "не потерять"


async def test_no_checkpoints_when_store_absent(settings):
    """Без хранилища (CLI, тесты) инструменты работают как раньше."""
    ctx = ToolContext(settings=settings)  # checkpoints=None
    result = await WriteFileTool().invoke({"path": "x.txt", "content": "ok"}, ctx)
    assert result.ok


async def _always_yes(request):
    return True


# ------------------------------------------------- аудит и откат всего прогона


def test_restore_run_reverts_all_files_to_pre_run_state(store, settings):
    """Откат прогона возвращает ВСЕ его файлы к состоянию до прогона."""
    a = settings.workspace / "a.py"
    b = settings.workspace / "b.py"
    a.write_text("A0", encoding="utf-8")
    # b.py не существовал до прогона

    store.set_run("run1")
    store.snapshot(a, "a.py", "edit"); a.write_text("A1", encoding="utf-8")
    store.snapshot(a, "a.py", "edit"); a.write_text("A2", encoding="utf-8")  # два уровня по одному файлу
    store.snapshot(b, "b.py", "write"); b.write_text("B1", encoding="utf-8")  # создан в прогоне

    result = store.restore_run("run1")
    assert a.read_text(encoding="utf-8") == "A0"   # размотано до состояния перед прогоном
    assert not b.exists()                          # созданный в прогоне файл удалён
    assert set(result["restored"]) == {"a.py", "b.py"}


def test_restore_run_only_touches_its_own_run(store, settings):
    a = settings.workspace / "a.py"
    a.write_text("base", encoding="utf-8")
    store.set_run("run1")
    store.snapshot(a, "a.py", "edit"); a.write_text("from-run1", encoding="utf-8")
    store.set_run("run2")
    store.snapshot(a, "a.py", "edit"); a.write_text("from-run2", encoding="utf-8")

    store.restore_run("run2")
    assert a.read_text(encoding="utf-8") == "from-run1"  # откатился только run2
    # run1 всё ещё откатываем
    store.restore_run("run1")
    assert a.read_text(encoding="utf-8") == "base"


def test_run_manifest_lists_touched_files(store, settings):
    a = settings.workspace / "a.py"; a.write_text("x", encoding="utf-8")
    store.set_run("runX")
    store.snapshot(a, "a.py", "edit"); a.write_text("y", encoding="utf-8")
    store.snapshot(a, "a.py", "edit"); a.write_text("z", encoding="utf-8")
    m = store.run_manifest("runX")
    assert m["run_id"] == "runX" and m["count"] == 2 and m["recoverable"] is True
    assert [f["path"] for f in m["files"]] == ["a.py"] and m["files"][0]["count"] == 2


def test_restore_run_unknown_raises(store):
    with pytest.raises(LookupError):
        store.restore_run("нет-такого")


def test_run_id_defaults_empty_for_untagged_snapshots(store, settings):
    a = settings.workspace / "a.py"; a.write_text("x", encoding="utf-8")
    store.snapshot(a, "a.py", "edit")  # без set_run
    assert store.records()[-1].run_id == ""


def test_old_manifest_without_run_id_still_loads(settings):
    """Старые манифесты (без поля run_id) должны читаться без ошибок."""
    import json
    d = settings.storage_dir / "checkpoints" / "sessOld"
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps([
        {"id": "abc", "path": "x.py", "op": "edit", "existed": True, "recoverable": True, "size": 3, "ts": 1.0}
    ]), encoding="utf-8")
    st = CheckpointStore(settings.storage_dir, "sessOld", settings.workspace)
    assert st.records()[0].run_id == ""
