"""Инструменты работы с секретами: запросить у пользователя, посмотреть список.

ВАЖНО про безопасность: агент НИКОГДА не получает значение секрета. Он лишь
просит пользователя ввести его (`request_secret`) и видит список имён
(`list_secrets`). Значение живёт в `.env` рабочей папки, попадает в окружение
запускаемого кода — но не в переписку с моделью. Поэтому агент обращается к
секрету по имени: `os.environ['NAME']`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.events import SecretRequested
from core.secrets_store import SecretError, list_secrets, validate_name
from core.tools.base import EmptyArgs, Tool, ToolContext, ToolResult


class RequestSecretArgs(BaseModel):
    name: str = Field(description="Имя секрета как переменной окружения, например OPENAI_API_KEY")
    purpose: str = Field(default="", description="Зачем нужен секрет — покажется пользователю")


class RequestSecretTool(Tool):
    name = "request_secret"
    description = (
        "Просит пользователя ввести секрет (API-ключ, пароль, токен) — откроется безопасная форма "
        "ввода. Значение сохранится в .env рабочей папки; ТЫ его не увидишь. В коде обращайся к "
        "секрету по имени через переменную окружения (os.environ['NAME'] / process.env.NAME). "
        "Пользователь может ввести секрет сразу или позже — не жди, продолжай работу."
    )
    Args = RequestSecretArgs
    category = "read"  # ничего не меняет сам по себе, только просит пользователя
    timeout = 15.0

    async def run(self, args: RequestSecretArgs, ctx: ToolContext) -> ToolResult:
        try:
            name = validate_name(args.name)
        except SecretError as exc:
            return ToolResult.fail(str(exc))

        await ctx.emitter(SecretRequested(name=name, purpose=args.purpose.strip()))
        already = any(s.name == name for s in list_secrets(ctx.settings.workspace))
        status = "он уже задан — пользователь может обновить" if already else "пользователь введёт его в панели «Секреты»"
        return ToolResult(
            content=(
                f"Запрошен секрет {name} ({status}). Введённое значение тебе не показывается. "
                f"В коде используй его через переменную окружения {name} — "
                "при запуске оно подставится из .env рабочей папки. "
                "Можешь продолжать: пользователь введёт секрет сейчас или позже."
            )
        )


class ListSecretsTool(Tool):
    name = "list_secrets"
    description = (
        "Показывает, какие секреты уже заданы (только имена и замаскированные значения — полные "
        "значения недоступны). Проверь перед запуском кода, который требует ключ: если нужного "
        "секрета нет — запроси его через request_secret."
    )
    Args = EmptyArgs
    category = "read"

    async def run(self, args: EmptyArgs, ctx: ToolContext) -> ToolResult:
        secrets = list_secrets(ctx.settings.workspace)
        if not secrets:
            return ToolResult(content="Секретов пока нет. Нужен ключ — запроси через request_secret.")
        lines = ["Заданные секреты (значения скрыты):"]
        lines += [f"  {s.name} = {s.masked}" for s in secrets]
        return ToolResult(content="\n".join(lines))
