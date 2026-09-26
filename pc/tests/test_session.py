from __future__ import annotations

from pathlib import Path

from core.agent.session import Session, estimate_tokens
from core.agent.storage import SessionStore
from core.llm.base import AssistantTurn, ToolCall


def build_session(pairs: int) -> Session:
    session = Session()
    session.set_system_prompt("system")
    for index in range(pairs):
        session.add_user("вопрос " + "x" * 500)
        session.add_assistant_turn(
            AssistantTurn(tool_calls=[ToolCall(id=f"c{index}", name="ping", arguments="{}")])
        )
        session.add_tool_result(f"c{index}", "ping", "ответ " + "y" * 500)
        session.add_assistant_turn(AssistantTurn(content="ответ " + "z" * 500))
    return session


def test_system_prompt_is_single_and_first():
    session = Session()
    session.set_system_prompt("первый")
    session.add_user("привет")
    session.set_system_prompt("второй")

    assert session.messages[0] == {"role": "system", "content": "второй"}
    assert sum(1 for m in session.messages if m["role"] == "system") == 1


def test_trim_keeps_system_prompt_and_recent():
    session = build_session(10)
    before = len(session.messages)

    removed = session.trim(budget_tokens=400)

    assert removed > 0
    assert len(session.messages) < before
    assert session.messages[0]["role"] == "system"
    assert session.token_estimate() < before * 100


def test_trim_never_leaves_orphan_tool_messages():
    session = build_session(10)
    session.trim(budget_tokens=300)

    for index, message in enumerate(session.messages):
        if message["role"] == "tool":
            previous = session.messages[index - 1]
            assert previous["role"] in {"assistant", "tool"}
            if previous["role"] == "assistant":
                assert previous.get("tool_calls")


def test_reset_restores_system_prompt():
    session = build_session(2)
    session.reset()
    assert len(session.messages) == 1
    assert session.messages[0]["role"] == "system"


def test_estimate_tokens_grows_with_text():
    small = estimate_tokens([{"role": "user", "content": "abc"}])
    big = estimate_tokens([{"role": "user", "content": "abc" * 1000}])
    assert big > small


def test_empty_assistant_turn_is_not_stored():
    session = Session()
    session.add_assistant_turn(AssistantTurn(content=""))
    assert session.messages == []


def test_session_serialization_roundtrip():
    session = Session(
        id="test12345",
        title="Исследование проекта",
        workspace="D:/Projects/TestApp",
        model="deepseek/deepseek-v4-flash-0731",
    )
    session.add_user("Привет, найди баг")
    session.set_plan([{"title": "Шаг 1", "status": "completed"}])
    session.add_artifact({"path": "reports/bug.md", "name": "bug.md", "kind": "markdown", "size_bytes": 120})

    data = session.to_dict()
    restored = Session.from_dict(data)

    assert restored.id == "test12345"
    assert restored.title == "Исследование проекта"
    assert restored.workspace == "D:/Projects/TestApp"
    assert len(restored.messages) == 1
    assert len(restored.plan_steps) == 1
    assert len(restored.artifacts) == 1


def test_session_store_save_load_list_delete(tmp_path: Path):
    store = SessionStore(base_dir=tmp_path)
    session = Session(id="sess_abc", title="Тестовый чат", workspace=str(tmp_path))
    session.add_user("Первое сообщение")

    store.save(session)

    loaded = store.load("sess_abc")
    assert loaded is not None
    assert loaded.id == "sess_abc"
    assert loaded.title == "Тестовый чат"
    assert len(loaded.messages) == 1

    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == "sess_abc"

    deleted = store.delete("sess_abc")
    assert deleted is True
    assert store.load("sess_abc") is None
    # Мягкое удаление: чат не стёрт, а в корзине — его видно и можно вернуть.
    assert not store.list_sessions()
    trashed = store.list_trashed()
    assert [t["id"] for t in trashed] == ["sess_abc"]
    assert trashed[0]["message_count"] == 1


def test_session_delete_is_recoverable(tmp_path: Path):
    store = SessionStore(base_dir=tmp_path)
    session = Session(id="важный", title="Важный чат", workspace=str(tmp_path))
    session.add_user("не потеряй меня")
    store.save(session)

    assert store.delete("важный") is True
    assert store.load("важный") is None          # из активного списка ушёл
    assert store.restore("важный") is True         # но восстанавливается из корзины
    back = store.load("важный")
    assert back is not None and back.title == "Важный чат"
    assert not store.list_trashed()                # после возврата корзина пуста
    # Повторное восстановление того, чего нет, — безопасно (False, без падения).
    assert store.restore("несуществующий") is False


# ------------------------------------------------- повтор задачи (rewind)


def _turn(session, text):
    """Имитирует один прогон: запрос -> ответ, и в messages, и в ленте."""
    from core.llm.base import AssistantTurn

    session.add_user(text)
    session.append_timeline({"kind": "user", "text": text})
    session.add_assistant_turn(AssistantTurn(content=f"ответ на «{text}»"))
    session.append_timeline({"kind": "answer", "text": f"ответ на «{text}»"})


def test_rewind_drops_the_turn_and_everything_after():
    session = Session()
    session.set_system_prompt("system")
    _turn(session, "первый")
    _turn(session, "второй")
    _turn(session, "третий")

    assert session.rewind_to_user_turn(1) is True

    # В истории остались: system + первый (user+assistant). Второй и третий ушли.
    users = [m for m in session.messages if m.get("role") == "user"]
    assert [m["content"] for m in users] == ["первый"]
    assert all("второй" not in str(m.get("content")) for m in session.messages)

    # Лента обрезана так же.
    user_entries = [e for e in session.timeline if e["kind"] == "user"]
    assert [e["text"] for e in user_entries] == ["первый"]


def test_rewind_to_first_turn_clears_dialog_body():
    session = Session()
    session.set_system_prompt("system")
    _turn(session, "единственный")

    assert session.rewind_to_user_turn(0) is True
    assert all(m.get("role") == "system" for m in session.messages)
    assert session.timeline == []


def test_rewind_unknown_turn_is_noop():
    session = Session()
    _turn(session, "один")
    assert session.rewind_to_user_turn(5) is False
    assert len([m for m in session.messages if m.get("role") == "user"]) == 1


# ------------------------------------------------- компакция контекста


def test_overflow_count_respects_pairs_and_recency():
    session = build_session(10)
    tail = len(session.messages) - session._start_index()
    count = session.overflow_count(budget_tokens=300, keep_recent=6)
    # Что-то надо убрать, но не всё: у хвоста остаётся около keep_recent.
    assert 0 < count < tail
    # Последние сообщения не попадают в число удаляемых.
    remaining = tail - count
    assert remaining >= 5  # группа assistant->tool может перескочить на 1


def test_replace_prefix_inserts_summary_note():
    session = build_session(8)
    count = session.overflow_count(budget_tokens=300)
    old = session.peek_prefix(count)
    assert len(old) == count

    session.replace_prefix(count, "[Ранее: краткое резюме беседы]")
    assert session.messages[0]["role"] == "system"  # промпт
    assert "краткое резюме" in session.messages[1]["content"]
    # После вставки заметки orphan-tool не появилось.
    for i, m in enumerate(session.messages):
        if m["role"] == "tool":
            assert session.messages[i - 1]["role"] in {"assistant", "tool"}
