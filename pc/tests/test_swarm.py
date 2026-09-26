"""Тесты группового чата агентов (core/swarm).

Проверяем три вещи:
  * общий чат корректно нумерует и отдаёт сообщения;
  * инструмент chat_send пишет от лица «вшитого» автора;
  * оркестратор гоняет раунды round-robin и останавливается, когда за целый
    раунд никто не написал в чат.
"""

from __future__ import annotations

import asyncio

import pytest

from core.events import SwarmFinished, SwarmMessage
from core.llm.base import AssistantTurn
from core.swarm.chat import GroupChat
from core.swarm.member import MemberSpec, build_default_team, load_team
from core.swarm.orchestrator import CLIENT_NAME, Swarm
from core.swarm.tools import ChatReadTool, ChatSendTool
from core.tools.base import ToolContext
from tests.fakes import EventCollector, ScriptedLLM, tool_call


def test_group_chat_sequences_and_since() -> None:
    chat = GroupChat()
    a = chat.post("Alpha", "первое")
    b = chat.post("Beta", "второе")
    assert (a.seq, b.seq) == (1, 2)
    assert [m.text for m in chat.since(1)] == ["второе"]
    assert chat.since(0) == chat.all()
    assert chat.last_seq() == 2
    with pytest.raises(ValueError):
        chat.post("Alpha", "   ")


@pytest.mark.asyncio
async def test_chat_send_binds_author(settings) -> None:
    chat = GroupChat()
    ctx = ToolContext(settings=settings)
    send = ChatSendTool(chat, "Кодер")
    read = ChatReadTool(chat, "Кодер")

    result = await send.invoke({"message": "готов интерфейс"}, ctx)
    assert result.ok
    assert chat.all()[0].author == "Кодер"  # автор нельзя подделать — он «вшит»

    transcript = await read.invoke({}, ctx)
    assert "готов интерфейс" in transcript.content
    assert "(вы)" in transcript.content  # свои реплики помечены


def test_default_team_capabilities_are_complementary() -> None:
    team = {m.name: m for m in build_default_team()}
    assert set(team) == {"Аналитик", "Кодер", "Тестировщик"}
    # Кодер умеет править, но не проверять; тестировщик — наоборот. Никто сам не закроет.
    assert "write_file" in team["Кодер"].tools
    assert "run_tests" not in team["Кодер"].tools
    assert "run_tests" in team["Тестировщик"].tools
    assert "write_file" not in team["Тестировщик"].tools


def test_load_team_from_json(tmp_path) -> None:
    path = tmp_path / "team.json"
    path.write_text(
        '[{"name":"A","role":"r","charter":"c","tools":["read_file"]},'
        '{"name":"B","charter":"c2"}]',
        encoding="utf-8",
    )
    members = load_team(path)
    assert [m.name for m in members] == ["A", "B"]
    assert members[0].tools == ["read_file"]
    assert members[1].role == "участник"  # значение по умолчанию


@pytest.mark.asyncio
async def test_swarm_runs_concurrently_and_converges(settings) -> None:
    # Два участника работают ОДНОВРЕМЕННО. Каждый один раз пишет в чат, затем
    # ему нечего добавить -> когда оба «уснули», команда сходится.
    # Свой ScriptedLLM на участника: так тест не зависит от порядка чередования.
    members = [
        MemberSpec(name="Alpha", role="первый", charter="Пиши в чат.", tools=[]),
        MemberSpec(name="Beta", role="второй", charter="Пиши в чат.", tools=[]),
    ]
    scripts = {
        "Alpha": [
            AssistantTurn(tool_calls=[tool_call("chat_send", message="Alpha на связи")]),
            AssistantTurn(content="ход завершён"),
        ],
        "Beta": [
            AssistantTurn(tool_calls=[tool_call("chat_send", message="Beta на связи")]),
            AssistantTurn(content="ход завершён"),
        ],
    }
    collector = EventCollector()
    swarm = Swarm(
        "собрать привет-мир",
        members,
        settings=settings,
        llm_factory=lambda spec: ScriptedLLM(list(scripts[spec.name])),
        emitter=collector,
        max_rounds=5,
    )
    result = await asyncio.wait_for(swarm.run(), timeout=10)

    authors = [m.author for m in result.messages]
    assert authors[0] == CLIENT_NAME  # задача от заказчика — первое сообщение
    assert "Alpha на связи" in [m.text for m in result.messages]
    assert "Beta на связи" in [m.text for m in result.messages]
    # Сошлись, потому что обоим стало нечего сказать.
    assert "нечего" in result.stopped_reason or "высказались" in result.stopped_reason

    chat_events = [e for e in collector.events if isinstance(e, SwarmMessage)]
    assert {e.author for e in chat_events} >= {CLIENT_NAME, "Alpha", "Beta"}
    finished = [e for e in collector.events if isinstance(e, SwarmFinished)]
    assert len(finished) == 1


def test_swarm_ws_default_config_and_parsing() -> None:
    from server.swarm_ws import SwarmConnection, available_tools, default_config

    cfg = default_config()
    assert cfg["team"] and cfg["tools"]
    # Инструменты чата не показываем в редакторе — они выдаются всем автоматически.
    assert all(not t["name"].startswith("chat_") for t in available_tools())

    conn = SwarmConnection.__new__(SwarmConnection)  # без сокета, только парсер
    members = conn._parse_members(
        [{"name": "A", "role": "r", "charter": "c", "tools": ["read_file"]}, {"name": "B", "charter": "c2"}]
    )
    assert [m.name for m in members] == ["A", "B"]
    with pytest.raises(ValueError):
        conn._parse_members([{"name": "", "charter": "c"}])  # пустое имя
    with pytest.raises(ValueError):
        conn._parse_members([{"name": "A", "charter": ""}])  # пустая зона


@pytest.mark.asyncio
async def test_swarm_rejects_bad_config(settings) -> None:
    with pytest.raises(ValueError):
        Swarm("", [MemberSpec("A", "r", "c"), MemberSpec("B", "r", "c")], settings=settings)
    with pytest.raises(ValueError):
        Swarm("задача", [MemberSpec("A", "r", "c")], settings=settings)  # нужен минимум двое
    with pytest.raises(ValueError):
        Swarm("задача", [MemberSpec("A", "r", "c"), MemberSpec("A", "r", "c")], settings=settings)
