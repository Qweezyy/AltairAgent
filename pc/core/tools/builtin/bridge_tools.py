"""Инструменты моста: ПК-агент просит что-то у телефона во время прогона.

Когда телефон — координатор, а ПК — исполнитель (`run`), модель ПК посреди
задачи может обнаружить, что ей не хватает файла, фото или решения пользователя.
Эти инструменты шлют телефону обратный запрос по тому же сокету и ждут ответ.

Доступ к соединению инструмент получает через `ctx.scratch["_bridge"]` — так же,
как `ask` получает канал ответов через scratch. Если телефона на том конце нет
(обычный веб-интерфейс), инструмент честно отказывает, а не висит до таймаута.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from core.i18n import tr
from core.tools.base import Tool, ToolContext, ToolResult

#: Дольше трёх минут держать пользователя телефона в ожидании бессмысленно.
BRIDGE_TIMEOUT = 180.0

_NO_PHONE = (
    "Телефон не подключён к мосту — обратные запросы недоступны. "
    "Сделай задачу средствами ПК или попроси файл у пользователя иначе."
)


def _connection(ctx: ToolContext) -> Any | None:
    """Соединение с телефоном, если оно есть и это действительно телефон."""
    conn = ctx.scratch.get("_bridge")
    if conn is None or not getattr(conn, "phone_connected", False):
        return None
    return conn


class HintArgs(BaseModel):
    hint: str = Field(
        default="",
        description="Короткое пояснение пользователю: что именно нужно и зачем",
    )


class PhoneRequestFileTool(Tool):
    name = "phone_request_file"
    description = (
        "Просит пользователя телефона выбрать и прислать файл. Работает только когда "
        "задачу делегировал телефон (мост). Файл прилетает в папку inbox/ рабочей "
        "директории — путь к нему возвращается. Используй, когда для задачи нужен "
        "документ/картинка, которых нет на ПК."
    )
    Args = HintArgs
    category = "network"
    timeout = None  # ждём человека столько, сколько нужно

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.phone_file", hint=args.hint or "—")

    async def run(self, args: HintArgs, ctx: ToolContext) -> str | ToolResult:
        conn = _connection(ctx)
        if conn is None:
            return ToolResult.fail(_NO_PHONE)
        reply = await conn.request_from_phone(
            {"type": "need_file", "hint": args.hint}, timeout=BRIDGE_TIMEOUT
        )
        if reply.get("type") == "need_file.cancel" or reply.get("cancelled"):
            return "Пользователь не прислал файл (отказ или таймаут). Продолжай без него."
        path = str(reply.get("path") or "").strip()
        if not path:
            return "Телефон ответил, но путь к файлу пуст. Файла нет — продолжай без него."
        return f"Файл получен от телефона и лежит в рабочей папке: {path}"


class PhoneRequestPhotoTool(Tool):
    name = "phone_request_photo"
    description = (
        "Просит пользователя телефона сделать фото камерой и прислать его. Работает "
        "только при делегировании с телефона (мост). Снимок прилетает в inbox/ — путь "
        "возвращается. Используй, когда задаче нужна свежая фотография (чек, объект, экран)."
    )
    Args = HintArgs
    category = "network"
    timeout = None

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.phone_photo", hint=args.hint or "—")

    async def run(self, args: HintArgs, ctx: ToolContext) -> str | ToolResult:
        conn = _connection(ctx)
        if conn is None:
            return ToolResult.fail(_NO_PHONE)
        reply = await conn.request_from_phone(
            {"type": "need_photo", "hint": args.hint}, timeout=BRIDGE_TIMEOUT
        )
        if reply.get("type") == "need_file.cancel" or reply.get("cancelled"):
            return "Пользователь не прислал фото (отказ или таймаут). Продолжай без него."
        path = str(reply.get("path") or "").strip()
        if not path:
            return "Телефон ответил, но путь к фото пуст. Снимка нет — продолжай без него."
        return f"Фото получено от телефона и лежит в рабочей папке: {path}"


class PhoneAskArgs(BaseModel):
    question: str = Field(description="Вопрос пользователю телефона, полным предложением")
    options: list[str] = Field(
        default_factory=list,
        description="Готовые варианты ответа (необязательно). Без них — свободный ответ.",
    )


class PhoneAskUserTool(Tool):
    name = "phone_ask_user"
    description = (
        "Задаёт вопрос пользователю телефона и ждёт ответ. Работает только при "
        "делегировании с телефона (мост). Используй для решений, которые нельзя принять "
        "самому: подтвердить опасное действие, выбрать вариант. Дай варианты в options, "
        "если выбор дискретный."
    )
    Args = PhoneAskArgs
    category = "network"
    timeout = None

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.phone_ask", question=args.question)

    async def run(self, args: PhoneAskArgs, ctx: ToolContext) -> str | ToolResult:
        conn = _connection(ctx)
        if conn is None:
            return ToolResult.fail(_NO_PHONE)
        answers = await conn.ask_phone(
            args.question, options=args.options or None, timeout=BRIDGE_TIMEOUT
        )
        chosen = _flatten_answers(answers)
        if not chosen:
            return (
                "Пользователь телефона не ответил (отказ или таймаут). "
                "Прими разумное решение сам и скажи, какое допущение принял."
            )
        return "Ответ пользователя телефона: " + "; ".join(chosen)


class PhoneCapabilityArgs(BaseModel):
    capability: str = Field(
        description="Возможность телефона: location, camera, sensors, notify_user, clipboard, share…"
    )
    task: str = Field(description="Что именно телефон должен сделать этой возможностью")


class PhoneCapabilityTool(Tool):
    name = "phone_capability"
    description = (
        "Просит телефон выполнить то, что недоступно ПК: узнать геолокацию, снять "
        "показания датчиков, показать пользователю уведомление, прочитать буфер обмена. "
        "Работает только при делегировании с телефона (мост). Верни короткую задачу и "
        "имя возможности — телефон выполнит её и вернёт результат."
    )
    Args = PhoneCapabilityArgs
    category = "network"
    timeout = None

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.phone_cap", capability=args.capability, task=args.task or "")

    async def run(self, args: PhoneCapabilityArgs, ctx: ToolContext) -> str | ToolResult:
        conn = _connection(ctx)
        if conn is None:
            return ToolResult.fail(_NO_PHONE)
        reply = await conn.request_from_phone(
            {"type": "need_capability", "capability": args.capability, "task": args.task},
            timeout=BRIDGE_TIMEOUT,
        )
        if reply.get("cancelled") or reply.get("ok") is False:
            return (
                f"Телефон не смог выполнить «{args.capability}» "
                "(отказ, недоступно или таймаут). Продолжай без этого."
            )
        output = reply.get("output")
        if output is None:
            output = _flatten_answers(reply.get("answers") or {})
            output = "; ".join(output) if output else ""
        return f"Телефон выполнил «{args.capability}». Результат: {output or 'без данных'}"


def _flatten_answers(answers: dict[str, Any]) -> list[str]:
    """Достаёт выбранные значения из формата телефона {"0":{"selected":[...]}}.

    Терпимо относится и к плоскому виду {"0":["Да"]} / {"0":"Да"}.
    """
    chosen: list[str] = []
    for value in (answers or {}).values():
        if isinstance(value, dict):
            picked = value.get("selected") or value.get("values") or value.get("value")
        else:
            picked = value
        if isinstance(picked, (list, tuple)):
            chosen.extend(str(item) for item in picked if str(item).strip())
        elif picked is not None and str(picked).strip():
            chosen.append(str(picked))
    return chosen
