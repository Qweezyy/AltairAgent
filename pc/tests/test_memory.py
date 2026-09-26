"""Долгосрочная память и поиск по чатам."""

from __future__ import annotations

import json

import pytest

from core.chat_search import search_chats
from core.memory import MemoryStore
from core.tools.base import ToolContext
from core.tools.builtin.memory_tools import RecallTool, RememberTool, SearchChatsTool

# --------------------------------------------------------------- память


@pytest.fixture
def memory(settings):
    return MemoryStore(settings.data_dir)


def test_remember_and_recall(memory):
    memory.remember("Пользователь предпочитает строгую типизацию", "preference")
    memory.remember("Проект на FastAPI и pydantic", "project")

    found = memory.recall("типизация")
    assert any("типизац" in f.text for f in found)


def test_duplicates_are_not_stored(memory):
    memory.remember("Город пользователя — Москва", "user")
    memory.remember("город пользователя — москва", "user")  # тот же факт иначе
    assert len(memory.all()) == 1


def test_empty_fact_is_ignored(memory):
    assert memory.remember("   ", "fact") is None
    assert memory.all() == []


def test_user_and_preference_always_relevant(memory):
    memory.remember("Зовут Саша", "user")
    memory.remember("Любит короткие ответы", "preference")
    memory.remember("Проект про парсинг PDF", "project")

    # Задача никак не пересекается с проектом — но user/preference всё равно тут.
    facts = memory.relevant_for("посчитай интеграл")
    texts = " ".join(f.text for f in facts)
    assert "Саша" in texts
    assert "короткие" in texts
    assert "PDF" not in texts  # проектный факт не по теме — не подставился


def test_project_fact_injected_on_topic_match(memory):
    memory.remember("В проекте отчёты по НДС считаются помесячно", "project")
    facts = memory.relevant_for("подготовь отчёт по НДС за март")
    assert any("НДС" in f.text for f in facts)


def test_prompt_section_lists_relevant_memory(memory):
    memory.remember("Работает на Windows", "user")
    section = memory.prompt_section("любая задача")
    assert "<memory>" in section
    assert "Windows" in section


def test_forget_and_clear(memory):
    fact = memory.remember("Временный факт", "fact")
    assert memory.forget(fact.id) is True
    assert memory.forget("нет-такого") is False

    memory.remember("A", "fact")
    memory.remember("B", "fact")
    assert memory.clear() == 2
    assert memory.all() == []


def test_memory_survives_reload(settings):
    first = MemoryStore(settings.data_dir)
    first.remember("Стек: Python 3.12", "project")

    second = MemoryStore(settings.data_dir)
    assert any("3.12" in f.text for f in second.all())


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


# ------------------------------------------------------- инструменты


async def test_remember_tool_saves(settings):
    store = MemoryStore(settings.data_dir)
    ctx = ToolContext(settings=settings, memory=store)
    result = await RememberTool().invoke(
        {"text": "Пользователь работает в вечернее время", "category": "user"}, ctx
    )
    assert result.ok
    assert any("вечернее" in f.text for f in store.all())


async def test_recall_tool_reports_nothing_gracefully(settings):
    ctx = ToolContext(settings=settings, memory=MemoryStore(settings.data_dir))
    result = await RecallTool().invoke({"query": "ничего такого нет"}, ctx)
    assert result.ok
    assert "ничего" in result.content.lower()


async def test_search_chats_tool(settings):
    _write_session(settings.storage_dir, "s1", "История", 100, [
        ("user", "давай обсудим формат экспорта в CSV"),
    ])
    ctx = ToolContext(settings=settings)
    result = await SearchChatsTool().invoke({"query": "экспорт CSV"}, ctx)
    assert result.ok
    assert "История" in result.content


# ---------------------------------------------- интеграция с промптом агента


async def test_remembered_fact_appears_in_next_session_prompt(settings):
    """Факт, сохранённый в одном чате, подставляется в промпт другого."""
    from core.agent.runner import AgentRunner
    from core.agent.session import Session
    from core.llm.base import AssistantTurn
    from core.tools import build_default_registry
    from tests.fakes import ScriptedLLM

    # Чат 1: агент запоминает предпочтение.
    store = MemoryStore(settings.data_dir)
    store.remember("Пользователь любит ответы с примерами кода", "preference")

    # Чат 2: новая сессия — факт должен оказаться в системном промпте.
    llm = ScriptedLLM([AssistantTurn(content="ок")])
    runner = AgentRunner(
        llm=llm, registry=build_default_registry(), settings=settings, session=Session()
    )
    await runner.run("любой новый вопрос")

    system_prompt = llm.calls[0]["messages"][0]["content"]
    assert "<memory>" in system_prompt
    assert "примерами кода" in system_prompt


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
