"""Search in the saved chats (the memory itself: tests/test_memory_notes.py)."""

from __future__ import annotations

import json

from core.chat_search import search_chats
from core.memory import MemoryStore
from core.tools.base import ToolContext
from core.tools.builtin.memory_tools import SearchChatsTool

# ------------------------------------------------------------- поиск по чатам


def _write_session(storage_dir, sid, title, updated, entries):
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / f"{sid}.json").write_text(
        json.dumps(
            {
                "id": sid,
                "title": title,
                "updated_at": updated,
                "messages": [{"role": "user", "content": "x"}],
                "timeline": [{"kind": k, "text": t} for k, t in entries],
            }
        ),
        encoding="utf-8",
    )


def test_search_finds_across_chats(settings):
    d = settings.storage_dir
    _write_session(d, "s1", "Про прокси", 100, [
        ("user", "как настроить прокси для агента"),
        ("answer", "нужно указать HTTP_PROXY в переменных окружения"),
    ])
    _write_session(d, "s2", "Про графики", 200, [
        ("user", "построй график продаж"),
        ("answer", "готово, сохранил диаграмму"),
    ])

    hits = search_chats(d, "прокси")
    assert hits
    assert hits[0].title == "Про прокси"
    assert "прокси" in hits[0].snippet.lower() or "proxy" in hits[0].snippet.lower()


def test_search_scope_current_only(settings):
    d = settings.storage_dir
    _write_session(d, "cur", "Текущий", 100, [("user", "секретное слово арбуз")])
    _write_session(d, "other", "Другой", 100, [("user", "тоже слово арбуз")])

    hits = search_chats(d, "арбуз", current_session_id="cur", scope="current")
    assert len(hits) == 1
    assert hits[0].session_id == "cur"


def test_search_empty_query_returns_nothing(settings):
    _write_session(settings.storage_dir, "s", "T", 1, [("user", "текст")])
    assert search_chats(settings.storage_dir, "") == []


def test_search_ranks_more_matches_higher(settings):
    d = settings.storage_dir
    _write_session(d, "many", "Много совпадений", 100, [("user", "отчёт отчёт отчёт бюджет")])
    _write_session(d, "few", "Мало", 200, [("user", "отчёт один раз")])
    hits = search_chats(d, "отчёт бюджет")
    assert hits[0].session_id == "many"  # два разных слова совпали


async def test_search_chats_tool(settings):
    _write_session(settings.storage_dir, "s1", "История", 100, [
        ("user", "давай обсудим формат экспорта в CSV"),
    ])
    ctx = ToolContext(settings=settings)
    result = await SearchChatsTool().invoke({"query": "экспорт CSV"}, ctx)
    assert result.ok
    assert "История" in result.content



def test_memory_file_is_not_seen_as_a_chat(settings):
    """Регресс: memory.json не должен попадать ни в список чатов, ни в поиск."""
    from core.agent.storage import SessionStore

    MemoryStore(settings.data_dir).remember("Факт памяти про арбуз", "user")
    _write_session(settings.storage_dir, "real", "Настоящий чат", 100, [("user", "арбуз")])

    # Список чатов видит только настоящую сессию.
    listed = SessionStore(settings=settings).list_sessions()
    assert [s["id"] for s in listed] == ["real"]

    # Поиск по чатам не находит факт из памяти (он вне папки сессий).
    hits = search_chats(settings.storage_dir, "арбуз")
    assert all(h.session_id == "real" for h in hits)
