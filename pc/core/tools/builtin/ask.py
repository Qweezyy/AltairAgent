"""Вопросы пользователю с вариантами ответа.

Зачем инструмент, если агент и так умеет писать текст: свободный вопрос в чате
требует печатать ответ и легко понимается неоднозначно. Готовые варианты
превращают уточнение в один клик и не дают разночтений — агент точно знает,
что выбрано.

Три типа вопросов покрывают почти всё, что приходится уточнять:
  * `single`   — выбрать один вариант (какой стек, какой формат);
  * `multiple` — отметить несколько (какие разделы делать);
  * `ranking`  — расставить по важности (что делать в первую очередь).

Правила для агента заданы в описании инструмента: помечать рекомендованное и
пояснять каждый вариант, иначе выбор превращается в угадайку.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from core.errors import ToolError
from core.events import QuestionAsked
from core.tools.base import Tool, ToolContext

#: Больше шести вариантов человек уже не сравнивает, а пролистывает.
MAX_OPTIONS = 6
#: За один раз группируем НЕЗАВИСИМЫЕ вопросы. Ветвящиеся (ответ на один меняет
#: следующие) задаём в отдельных раундах — см. описание инструмента.
MAX_QUESTIONS = 6


class AskOption(BaseModel):
    label: str = Field(description="Short option label (2-5 words)")
    description: str = Field(
        default="",
        description="What happens if this option is chosen, in 1-2 sentences",
    )
    recommended: bool = Field(
        default=False, description="Mark as the recommended option (at most one per question)"
    )


class AskQuestion(BaseModel):
    question: str = Field(description="The question, as a full sentence")
    kind: Literal["single", "multiple", "ranking"] = Field(
        default="single",
        description="single: pick one; multiple: pick several; ranking: order by importance",
    )
    options: list[AskOption] = Field(description=f"2 to {MAX_OPTIONS} answer options")

    @field_validator("options")
    @classmethod
    def _check_options(cls, value: list[AskOption]) -> list[AskOption]:
        if len(value) < 2:
            raise ValueError("нужно минимум два варианта, иначе выбор бессмысленен")
        if len(value) > MAX_OPTIONS:
            raise ValueError(f"не больше {MAX_OPTIONS} вариантов — длинный список не читают")
        if sum(option.recommended for option in value) > 1:
            raise ValueError("рекомендованным может быть только один вариант")
        return value


class AskArgs(BaseModel):
    questions: list[AskQuestion] = Field(
        description=f"1 to {MAX_QUESTIONS} questions, of any kinds"
    )

    @field_validator("questions")
    @classmethod
    def _check_questions(cls, value: list[AskQuestion]) -> list[AskQuestion]:
        if not value:
            raise ValueError("нужен хотя бы один вопрос")
        if len(value) > MAX_QUESTIONS:
            raise ValueError(f"не больше {MAX_QUESTIONS} вопросов за раз")
        return value


class AskTool(Tool):
    name = "ask"
    description = (
        "Asks the user clarifying questions with one-click answer options. Use it when the task "
        "allows materially different solutions and a wrong guess is costly: the stack, the output "
        "format, priorities. Group only independent questions in one call (up to 6); when an answer "
        "changes what to ask next, ask that first and follow up in another round. Skip questions "
        "with an obvious sensible answer. Mark the recommended option and explain each option."
    )
    Args = AskArgs
    category = "read"  # ничего не меняет, только спрашивает
    timeout = None  # ждём человека столько, сколько нужно

    async def run(self, args: AskArgs, ctx: ToolContext) -> str:
        answers = ctx.scratch.setdefault("_ask_answers", {})
        request_id = uuid.uuid4().hex[:10]
        waiter: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        answers[request_id] = waiter

        await ctx.emitter(
            QuestionAsked(
                request_id=request_id,
                questions=[question.model_dump() for question in args.questions],
            )
        )

        try:
            replies = await waiter
        except asyncio.CancelledError:
            answers.pop(request_id, None)
            raise
        finally:
            answers.pop(request_id, None)

        if not replies:
            raise ToolError(
                "The user did not answer. Decide yourself, by common sense, "
                "and say which assumption you made."
            )
        return format_answers(args.questions, replies)


def format_answers(questions: list[AskQuestion], replies: dict) -> str:
    """The answer for the model: what was asked and what was picked."""
    lines: list[str] = ["The user's answers:"]

    for index, question in enumerate(questions):
        chosen = replies.get(str(index)) or replies.get(index) or []
        if isinstance(chosen, str):
            chosen = [chosen]

        lines.append(f"\n{index + 1}. {question.question}")
        if not chosen:
            lines.append("   (no answer)")
            continue

        if question.kind == "ranking":
            for place, label in enumerate(chosen, start=1):
                lines.append(f"   {place}. {label}")
        else:
            for label in chosen:
                lines.append(f"   - {label}")

    lines.append("\nAct on these choices and do not ask the same again.")
    return "\n".join(lines)
