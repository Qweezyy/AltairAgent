"""Вопросы агента: типы, валидация и ожидание ответа."""

from __future__ import annotations

import asyncio

import pytest

from core.errors import ToolError
from core.events import QuestionAsked
from core.tools.builtin.ask import AskArgs, AskQuestion, AskTool, format_answers

SIMPLE = {
    "questions": [
        {
            "question": "Какой стек использовать?",
            "kind": "single",
            "options": [
                {"label": "FastAPI", "description": "Быстрее для API", "recommended": True},
                {"label": "Django", "description": "Больше готового из коробки"},
            ],
        }
    ]
}


# ------------------------------------------------------------- валидация


def test_options_must_have_alternatives():
    with pytest.raises(ValueError, match="минимум два"):
        AskQuestion(question="?", options=[{"label": "Один"}])


def test_too_many_options_rejected():
    options = [{"label": f"Вариант {i}"} for i in range(7)]
    with pytest.raises(ValueError, match="не больше 6"):
        AskQuestion(question="?", options=options)


def test_only_one_option_can_be_recommended():
    with pytest.raises(ValueError, match="только один"):
        AskQuestion(
            question="?",
            options=[
                {"label": "А", "recommended": True},
                {"label": "Б", "recommended": True},
            ],
        )


def test_at_least_one_question_required():
    with pytest.raises(ValueError, match="хотя бы один"):
        AskArgs(questions=[])


def test_all_three_kinds_are_supported():
    for kind in ("single", "multiple", "ranking"):
        question = AskQuestion(
            question="?", kind=kind, options=[{"label": "А"}, {"label": "Б"}]
        )
        assert question.kind == kind


def test_schema_describes_kinds_and_recommendation():
    schema = AskTool().schema()["function"]["parameters"]
    text = str(schema)
    assert "single" in text and "multiple" in text and "ranking" in text
    assert "recommended" in text
    assert "recommended" in AskTool.description.lower()


# ---------------------------------------------------------- формат ответа


def test_answers_are_formatted_for_the_model():
    questions = [
        AskQuestion(question="Стек?", kind="single", options=[{"label": "FastAPI"}, {"label": "Django"}]),
        AskQuestion(
            question="Что важнее?",
            kind="ranking",
            options=[{"label": "Скорость"}, {"label": "Простота"}],
        ),
    ]
    text = format_answers(questions, {"0": ["FastAPI"], "1": ["Простота", "Скорость"]})

    assert "Стек?" in text and "- FastAPI" in text
    assert "1. Простота" in text and "2. Скорость" in text
    assert "do not ask the same again" in text


def test_unanswered_question_is_marked():
    questions = [AskQuestion(question="Стек?", options=[{"label": "А"}, {"label": "Б"}])]
    assert "(no answer)" in format_answers(questions, {})


# --------------------------------------------------------- работа инструмента


async def test_question_reaches_ui_and_waits_for_answer(ctx):
    events = []

    async def emitter(event):
        events.append(event)
        # Отвечаем так же, как это сделает интерфейс.
        if isinstance(event, QuestionAsked):
            waiter = ctx.scratch["_ask_answers"][event.request_id]
            waiter.set_result({"0": ["FastAPI"]})

    ctx.emitter = emitter
    result = await AskTool().invoke(SIMPLE, ctx)

    assert result.ok
    assert "FastAPI" in result.content
    asked = [e for e in events if isinstance(e, QuestionAsked)]
    assert len(asked) == 1
    assert asked[0].questions[0]["options"][0]["recommended"] is True


async def test_agent_is_told_to_decide_when_user_skips(ctx):
    async def emitter(event):
        if isinstance(event, QuestionAsked):
            ctx.scratch["_ask_answers"][event.request_id].set_result({})

    ctx.emitter = emitter
    result = await AskTool().invoke(SIMPLE, ctx)

    assert not result.ok
    assert "Decide yourself" in result.content


async def test_waiting_can_be_cancelled(ctx):
    """Остановка задачи не должна оставлять зависшее ожидание."""

    async def emitter(event):
        return None

    ctx.emitter = emitter
    task = asyncio.create_task(AskTool().invoke(SIMPLE, ctx))
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_ask_never_requires_approval(ctx):
    """Вопрос ничего не меняет — спрашивать разрешения на вопрос абсурдно."""
    ctx.settings.approval_mode = "manual"
    asked = []

    async def approver(request):
        asked.append(request.name)
        return True

    async def emitter(event):
        if isinstance(event, QuestionAsked):
            ctx.scratch["_ask_answers"][event.request_id].set_result({"0": ["FastAPI"]})

    ctx.approver = approver
    ctx.emitter = emitter
    await AskTool().invoke(SIMPLE, ctx)

    assert asked == []
    assert AskTool.category == "read"


async def test_broken_arguments_explain_the_rule(ctx):
    result = await AskTool().invoke({"questions": [{"question": "?", "options": []}]}, ctx)
    assert not result.ok
    assert "минимум два" in result.content


def test_tool_error_is_importable():
    assert issubclass(ToolError, Exception)
