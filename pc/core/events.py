"""События агента — единственный канал связи ядра с интерфейсом.

Ядро НИЧЕГО не знает про WebSocket/консоль: оно только отдаёт события в
`Emitter`. Любой новый UI = новая реализация Emitter.

Добавляете новое событие? Опишите модель здесь, добавьте её в Event и
обработайте в static/app.js — иначе UI просто проигнорирует неизвестный тип
(это безопасно и ничего не ломает).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, Field


class BaseEvent(BaseModel):
    ts: float = Field(default_factory=time.time)


class RunStarted(BaseEvent):
    type: Literal["run.started"] = "run.started"
    run_id: str
    task: str
    model: str


class StepStarted(BaseEvent):
    type: Literal["step.started"] = "step.started"
    step: int
    max_steps: int


class TextDelta(BaseEvent):
    """Кусок финального текста ответа (стриминг)."""

    type: Literal["text.delta"] = "text.delta"
    text: str


class ReasoningDelta(BaseEvent):
    """Кусок «размышлений» модели. НЕ попадает в историю сообщений."""

    type: Literal["reasoning.delta"] = "reasoning.delta"
    text: str


class ToolPending(BaseEvent):
    """Модель начала диктовать вызов инструмента, но ещё не закончила.

    Нужно, чтобы длинная генерация (например, содержимого файла на 500 строк)
    не выглядела зависанием: имя инструмента известно сразу, а объём растёт
    на глазах.
    """

    type: Literal["tool.pending"] = "tool.pending"
    name: str
    chars: int = 0


class ToolStarted(BaseEvent):
    type: Literal["tool.started"] = "tool.started"
    call_id: str
    name: str
    args: dict[str, Any]


class ToolFinished(BaseEvent):
    type: Literal["tool.finished"] = "tool.finished"
    call_id: str
    name: str
    ok: bool
    output: str
    duration_ms: int


class QuestionAsked(BaseEvent):
    """Агент задал уточняющий вопрос с вариантами ответа."""

    type: Literal["question.asked"] = "question.asked"
    request_id: str
    questions: list[dict[str, Any]] = Field(default_factory=list)


class ApprovalRequested(BaseEvent):
    """Запрос подтверждения у пользователя перед опасным действием."""

    type: Literal["approval.requested"] = "approval.requested"
    request_id: str
    name: str
    args: dict[str, Any]
    reason: str


class BrowserHandoff(BaseEvent):
    """Агент просит пользователя вмешаться в общий браузер (капча/2FA/вход)."""

    type: Literal["browser.handoff"] = "browser.handoff"
    request_id: str
    reason: str
    hint: str = ""


class ApprovalResolved(BaseEvent):
    type: Literal["approval.resolved"] = "approval.resolved"
    request_id: str
    approved: bool


class PlanStep(BaseModel):
    title: str = Field(description="Step title")
    status: Literal["pending", "in_progress", "completed", "failed"] = Field(
        default="pending", description="Step status"
    )


class PlanUpdate(BaseEvent):
    """Обновление пошагового плана задачи в стиле Manus AI."""

    type: Literal["plan.updated"] = "plan.updated"
    steps: list[PlanStep] = Field(default_factory=list)


class ArtifactCreated(BaseEvent):
    """Регистрация созданного или обновлённого файла/артефакта."""

    type: Literal["artifact.created"] = "artifact.created"
    path: str = Field(description="Относительный путь к файлу")
    name: str = Field(description="Имя файла")
    kind: str = Field(default="file", description="Тип: code, markdown, html, image, data, file")
    size_bytes: int = Field(default=0)


class SecretRequested(BaseEvent):
    """Агент попросил у пользователя секрет (ключ/пароль) — открыть панель ввода."""

    type: Literal["secret.requested"] = "secret.requested"
    name: str = Field(description="Имя секрета (переменной окружения)")
    purpose: str = Field(default="", description="Зачем он нужен")


class ResearchProgress(BaseEvent):
    """Ход глубокого исследования.

    Отдельное событие, а не лог: исследование идёт минутами, и без него окно
    выглядит зависшим — пользователь не знает, читается сейчас третий источник
    или двенадцатый.
    """

    type: Literal["research.progress"] = "research.progress"
    phase: Literal["plan", "search", "read", "digest", "report"]
    text: str
    done: int = 0
    total: int = 0


class UsageUpdated(BaseEvent):
    """Живой счётчик расхода: сколько токенов и денег потрачено на задачу."""

    type: Literal["usage.updated"] = "usage.updated"
    tokens: int = 0
    usd: float = 0.0
    priced: bool = False
    #: Потолок токенов на задачу (0 — без лимита). Нужен для индикатора.
    budget: int = 0


class CheckpointCreated(BaseEvent):
    """Перед изменением файла сделан снимок — правку можно откатить."""

    type: Literal["checkpoint.created"] = "checkpoint.created"
    id: str
    path: str
    op: str = "write"
    recoverable: bool = True


class CheckpointRestored(BaseEvent):
    """Откат правки файла выполнен."""

    type: Literal["checkpoint.restored"] = "checkpoint.restored"
    path: str
    message: str


class RunFinished(BaseEvent):
    type: Literal["run.finished"] = "run.finished"
    run_id: str
    text: str
    steps: int
    duration_ms: int
    usage: dict[str, int] = Field(default_factory=dict)
    #: Стоимость задачи в долларах (0, если цена модели неизвестна).
    cost_usd: float = 0.0
    #: What the run can claim about checks: {"executed": bool, "passed": bool | None,
    #: "acceptance": "unknown"} — "checks ran", "checks passed" and "the requested behaviour was
    #: accepted" are three different things, and only the first two are checked today.
    checks: dict[str, Any] = Field(default_factory=dict)


class ShowImage(BaseEvent):
    """Агент решил показать картинку ПРЯМО в ленте ответа (не только в «Превью»).

    Отличие от ArtifactCreated: тот кладёт файл в панель артефактов, а это событие
    выводит изображение инлайн в чат — только то, что агент счёл нужным показать.
    """

    type: Literal["show_image"] = "show_image"
    path: str = Field(description="Путь к изображению относительно рабочей папки")
    name: str = ""
    caption: str = ""


class ShowHtml(BaseEvent):
    """Агент показывает инлайн-виджет в ленте: SVG-графику или интерактивный HTML+JS.

    Рендерится в песочнице (iframe srcdoc, sandbox без same-origin) — так скрипты
    виджета изолированы от данных приложения. Аналог ShowImage, но для разметки,
    которую модель сгенерировала сама (диаграммы, мини-калькуляторы, анимации).
    """

    type: Literal["show_html"] = "show_html"
    html: str = Field(description="Самодостаточный HTML-документ (или обёрнутый SVG)")
    caption: str = ""
    #: kind — для подсказки интерфейсу: "graphic" (статичный SVG) | "interactive".
    kind: str = "interactive"


class ShowFile(BaseEvent):
    """Агент прикрепляет готовый файл прямо в ленту ответа (чип со скачиванием).

    Отличие от ArtifactCreated: тот регистрирует файл в боковой панели артефактов,
    а это — инлайн-вложение в самом ответе (как attach_file на телефоне).
    """

    type: Literal["show_file"] = "show_file"
    path: str = Field(description="Путь к файлу относительно рабочей папки")
    name: str = ""
    caption: str = ""
    kind: str = Field(default="file", description="image, video, audio, data, file")
    size_bytes: int = 0


class ContextUsage(BaseEvent):
    """Обновление заполнения контекстного окна — для кольца у строки ввода.

    Тот же формат, что шлёт server/ws.py при подключении; инструменты контекста
    (context_compress/context_drop) эмитят его, чтобы индикатор обновился сразу."""

    type: Literal["context.usage"] = "context.usage"
    tokens: int = 0
    #: True when anchored on the provider's own count (see Session.context_now).
    exact: bool = False


class Reconnecting(BaseEvent):
    """Связь с моделью оборвалась — идёт повторная попытка.

    Без этого события интерфейс во время повторов выглядит зависшим: пользователь
    не знает, что агент не «умер», а переподключается к провайдеру. Показываем
    номер попытки и через сколько будет следующая.
    """

    type: Literal["reconnecting"] = "reconnecting"
    attempt: int
    max_attempts: int
    delay_s: float = 0.0
    reason: str = ""


class RunFailed(BaseEvent):
    type: Literal["run.failed"] = "run.failed"
    run_id: str
    message: str


class RunCancelled(BaseEvent):
    type: Literal["run.cancelled"] = "run.cancelled"
    run_id: str


class LogEvent(BaseEvent):
    type: Literal["log"] = "log"
    level: Literal["debug", "info", "warning", "error"] = "info"
    text: str


# --- Групповой чат агентов (эксперимент, core/swarm) --------------------


class SwarmMessage(BaseEvent):
    """Сообщение в общем чате команды — публичный слой эксперимента.

    В отличие от text.delta (личный поток одного агента), это то, что участник
    осознанно сказал коллегам. Только эти сообщения видны другим агентам.
    """

    type: Literal["swarm.message"] = "swarm.message"
    seq: int
    author: str
    text: str
    #: Роль автора (для подписи/цвета в интерфейсе). Пусто у заказчика/системы.
    role: str = ""


class SwarmRoundStarted(BaseEvent):
    type: Literal["swarm.round"] = "swarm.round"
    round: int
    max_rounds: int


class SwarmTurnStarted(BaseEvent):
    """Начался ход участника — интерфейс подсвечивает, кто сейчас «печатает»."""

    type: Literal["swarm.turn"] = "swarm.turn"
    member: str


class SwarmActivity(BaseEvent):
    """Приватное действие участника (вызов инструмента).

    Коллеги-агенты этого НЕ видят; событие только для человека-наблюдателя, чтобы
    было видно, чем занят каждый участник за своим «столом».
    """

    type: Literal["swarm.activity"] = "swarm.activity"
    member: str
    tool: str
    ok: bool = True
    detail: str = ""


class SwarmFinished(BaseEvent):
    type: Literal["swarm.finished"] = "swarm.finished"
    rounds_run: int
    reason: str
    message_count: int = 0


Event = (
    RunStarted
    | StepStarted
    | TextDelta
    | ReasoningDelta
    | ToolPending
    | ToolStarted
    | ToolFinished
    | QuestionAsked
    | ApprovalRequested
    | ApprovalResolved
    | PlanUpdate
    | ArtifactCreated
    | SecretRequested
    | UsageUpdated
    | CheckpointCreated
    | CheckpointRestored
    | ResearchProgress
    | ShowImage
    | ShowHtml
    | ShowFile
    | ContextUsage
    | Reconnecting
    | RunFinished
    | RunFailed
    | RunCancelled
    | LogEvent
    | SwarmMessage
    | SwarmRoundStarted
    | SwarmTurnStarted
    | SwarmActivity
    | SwarmFinished
)

#: Куда ядро отправляет события. Реализации: server/ws.py, core/ui/console.py.
Emitter = Callable[[Event], Awaitable[None]]


async def noop_emitter(event: Event) -> None:  # noqa: ARG001
    """Заглушка: события выбрасываются. Удобно для тестов и headless-режима."""
    return None
