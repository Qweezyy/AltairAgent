"""Тесты следа прогона на диске — основа resume после падения/перезапуска.

Оставшийся маркер ``running`` = процесс умер посреди задачи (штатный выход
всегда снимает маркер). См. core/agent/run_state.py.
"""

from __future__ import annotations

from pathlib import Path

from core.agent.run_state import RunStateStore


def test_begin_marks_running_and_interrupted(tmp_path: Path) -> None:
    store = RunStateStore(tmp_path)
    store.begin("s1", "r1", "почини баг", "test/model")
    rec = store.interrupted("s1")
    assert rec and rec["status"] == "running"
    assert rec["run_id"] == "r1"
    assert rec["task"] == "почини баг"
    assert rec["step"] == 0


def test_clear_removes_marker(tmp_path: Path) -> None:
    store = RunStateStore(tmp_path)
    store.begin("s1", "r1", "задача")
    store.clear("s1")
    assert store.interrupted("s1") is None
    assert store.read("s1") is None


def test_clear_is_idempotent(tmp_path: Path) -> None:
    store = RunStateStore(tmp_path)
    store.clear("нет-такого")  # не должно падать, если маркера нет
    assert store.read("нет-такого") is None


def test_update_advances_step(tmp_path: Path) -> None:
    store = RunStateStore(tmp_path)
    store.begin("s1", "r1", "задача")
    store.update("s1", 5)
    rec = store.interrupted("s1")
    assert rec and rec["step"] == 5


def test_update_noop_without_marker(tmp_path: Path) -> None:
    store = RunStateStore(tmp_path)
    store.update("ghost", 3)  # нет активного прогона — тихо игнорируем
    assert store.read("ghost") is None


def test_list_interrupted_only_running(tmp_path: Path) -> None:
    store = RunStateStore(tmp_path)
    store.begin("a", "r1", "t1")
    store.begin("b", "r2", "t2")
    store.clear("a")  # a завершился штатно
    ids = {r["session_id"] for r in store.list_interrupted()}
    assert ids == {"b"}


def test_corrupt_marker_is_ignored(tmp_path: Path) -> None:
    store = RunStateStore(tmp_path)
    (store.dir / "s1.json").write_text("{битый json", encoding="utf-8")
    assert store.read("s1") is None
    assert store.interrupted("s1") is None
    assert store.list_interrupted() == []
