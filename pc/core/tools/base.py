"""Базовый класс инструмента.

Контракт (соблюдайте буквально — ядро на него опирается):

    class MyArgs(BaseModel):
        path: str = Field(description="Путь к файлу")

    class MyTool(Tool):
        name = "my_tool"
        description = "Что делает и когда вызывать."
        Args = MyArgs
        dangerous = False          # True -> потребуется подтверждение пользователя
        timeout = 30.0             # секунды; None = без ограничения

        async def run(self, args: MyArgs, ctx: ToolContext) -> str:
            return "результат"

Всё остальное (валидация аргументов, таймаут, ловля исключений, обрезка
вывода, подтверждение) делает `invoke()` — не дублируйте это в `run()`.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError

from core.errors import PermissionDenied, ToolError, ToolInputError
from core.events import Emitter, noop_emitter
from core.i18n import tr
from core.logging_setup import get_logger
from core.security.approval import ApprovalRequest, Approver, always_allow
from core.security.permissions import block_reason, decide, needs_classifier
from core.security.risk import assess, collect_text, rank
from core.security.system_change import system_change
from core.settings import Settings, get_settings

logger = get_logger("tools")


class EmptyArgs(BaseModel):
    """Схема для инструментов без аргументов."""


@dataclass(slots=True)
class ToolContext:
    """Всё, что инструмент может получить от окружения.

    Не тащите в инструменты глобальные объекты — расширяйте контекст.
    """

    settings: Settings = field(default_factory=get_settings)
    emitter: Emitter = noop_emitter
    approver: Approver = always_allow
    run_id: str = "local"
    #: Хранилище для обмена данными между инструментами внутри одного запуска.
    scratch: dict[str, Any] = field(default_factory=dict)
    #: Снимки файлов для отката правок. None -> снимки не делаются (тесты, CLI).
    checkpoints: Any = None
    #: Долгосрочная память (факты между чатами). None -> инструмент создаст свою.
    memory: Any = None
    #: Текущая сессия диалога — нужна инструментам контекста (context_info/compress/
    #: drop), которым надо видеть и править историю. None -> вне прогона (тесты/CLI).
    session: Any = None
    #: The run's tool registry — tool_search looks up deferred tools in it.
    registry: Any = None


@dataclass(slots=True)
class ToolResult:
    """Результат инструмента. `content` — это то, что увидит модель."""

    content: str
    ok: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def fail(cls, message: str, **metadata: Any) -> ToolResult:
        return cls(content=f"ОШИБКА: {message}", ok=False, metadata=metadata)


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Разворачивает $defs/$ref в плоскую JSON-схему.

    Часть провайдеров (и мелкие модели) плохо переваривают $ref, поэтому
    схему инструмента отдаём максимально простой.

    Заодно выбрасываются аннотации "title" — они только занимают место в промпте.
    Но только аннотации: внутри "properties" ключ "title" — это ИМЯ аргумента.
    Однажды его вырезали вместе с аннотациями, аргумент пропадал из схемы, а
    "required" ссылался на несуществующее поле — Gemini на такую схему отвечает
    ошибкой 400, а остальные провайдеры молча теряли аргумент.
    """
    defs = schema.pop("$defs", {})

    def walk(node: Any, *, is_properties: bool = False) -> Any:
        if isinstance(node, dict):
            if is_properties:
                # Здесь ключи — имена аргументов, а не ключевые слова схемы.
                return {name: walk(value) for name, value in node.items()}

            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                target = defs.get(ref.split("/")[-1], {})
                extra = {key: value for key, value in node.items() if key != "$ref"}
                return {**walk(target), **walk(extra)}

            result: dict[str, Any] = {}
            for key, value in node.items():
                if key == "title" and isinstance(value, str):
                    continue
                result[key] = walk(value, is_properties=(key == "properties"))
            return result
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    cleaned = walk(schema)
    cleaned.setdefault("type", "object")
    cleaned.setdefault("properties", {})
    return cleaned


def truncate_output(text: str, limit: int) -> str:
    """Truncates long output, keeping the head and the tail (errors usually sit at the end)."""
    if limit <= 0 or len(text) <= limit:
        return text
    head = int(limit * 0.6)
    tail = limit - head
    cut = len(text) - limit
    return (
        f"{text[:head]}\n\n... [{cut} of {len(text)} chars truncated in the middle. Narrow the "
        f"query (a filter, a pattern, a line range) or read the rest in parts.] ...\n\n{text[-tail:]}"
    )



def args_summary(values: dict[str, Any], limit: int = 240) -> str:
    """Short human view of tool arguments: key: value, long text cut, lists counted."""
    parts: list[str] = []
    for key, value in values.items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, str):
            text = value if len(value) <= 80 else value[:77] + "…"
        elif isinstance(value, (list, tuple)) and len(value) > 3:
            text = f"[{len(value)}]"
        else:
            text = json.dumps(value, ensure_ascii=False)
            text = text if len(text) <= 80 else text[:77] + "…"
        parts.append(f"{key}: {text}")
    summary = "; ".join(parts)
    return summary if len(summary) <= limit else summary[: limit - 1] + "…"


class Tool(ABC):
    """Базовый инструмент агента."""

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    Args: ClassVar[type[BaseModel]] = EmptyArgs

    #: True -> действие меняет систему, спросим пользователя (см. APPROVAL_MODE).
    dangerous: ClassVar[bool] = False
    #: Категория для режимов разрешений:
    #:   read    — только смотрит (чтение файлов, поиск);
    #:   edit    — меняет файлы в рабочей папке;
    #:   execute — запускает код или команды;
    #:   network — обращается наружу или шлёт данные.
    category: ClassVar[str] = "read"
    #: Лимит времени выполнения, секунды. None = без лимита.
    timeout: ClassVar[float | None] = 60.0
    #: Персональный лимит вывода. None -> settings.tool_output_limit.
    max_output_chars: ClassVar[int | None] = None
    #: Необратимое действие (удаление, безвозвратная запись). Такое всегда
    #: спрашивают подтверждение, кроме режима «Без подтверждений» (bypass).
    irreversible: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if inspect.isabstract(cls):
            return
        if not cls.name:
            raise TypeError(f"{cls.__name__}: обязателен атрибут `name`.")
        if not cls.description:
            raise TypeError(f"{cls.__name__}: обязателен атрибут `description`.")
        # Опасный инструмент без явной категории не должен молча стать
        # безопасным «чтением» — иначе режимы разрешений пропустят его без
        # единого вопроса. Считаем такой инструмент правкой файлов.
        if cls.dangerous and cls.category == "read":
            cls.category = "edit"

    # --- реализуется наследниками -----------------------------------

    @abstractmethod
    async def run(self, args: Any, ctx: ToolContext) -> str | ToolResult:
        """Полезная работа инструмента. Кидайте ToolError для ожидаемых ошибок."""

    # --- инфраструктура (менять не нужно) ---------------------------

    def schema(self) -> dict[str, Any]:
        """JSON-схема инструмента в формате OpenAI/OpenRouter tools."""
        params = _inline_refs(self.Args.model_json_schema())
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description.strip(),
                "parameters": params,
            },
        }

    def auto_verdict(self, args: BaseModel, ctx: ToolContext) -> str:
        """Решение в режиме «Авто»: "allow" или "ask".

        Правило по умолчанию: правки внутри рабочей папки безопасны (песочница
        уже не пустит инструмент наружу), а запуск кода и изменения снаружи —
        спрашиваем. Инструмент может переопределить метод и решать точнее:
        например, разрешить только команды чтения.
        """
        if self.category in ("read", "edit"):
            return "allow"
        return "ask"

    def approval_reason(self, args: BaseModel) -> str:
        """What the approval card says. Tools should override this with the concrete action
        ("Create the chart file x.html"); this fallback at least names the kind of effect
        instead of claiming every tool "will change your system"."""
        key = {"edit": "appr.generic.edit", "execute": "appr.generic.execute",
               "network": "appr.generic.network"}.get(self.category, "appr.generic")
        return tr(key, name=self.name, args=args_summary(args.model_dump()))

    def parse_args(self, raw: dict[str, Any] | str | None) -> BaseModel:
        """Валидирует аргументы модели. Кидает ToolInputError с понятным текстом."""
        data: Any = raw
        if isinstance(raw, str):
            raw = raw.strip()
            if not raw:
                data = {}
            else:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ToolInputError(
                        f"Аргументы для '{self.name}' — невалидный JSON: {exc}. "
                        "Повтори вызов с корректным JSON."
                    ) from exc
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ToolInputError(f"Аргументы для '{self.name}' должны быть объектом JSON.")

        try:
            return self.Args.model_validate(data)
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or 'аргумент'}: {err['msg']}"
                for err in exc.errors()
            )
            raise ToolInputError(
                f"Неверные аргументы для '{self.name}': {details}. "
                f"Ожидаемая схема: {json.dumps(self.schema()['function']['parameters'], ensure_ascii=False)}"
            ) from exc

    async def invoke(self, raw_args: dict[str, Any] | str | None, ctx: ToolContext) -> ToolResult:
        """Полный безопасный вызов инструмента. Никогда не пробрасывает исключения,
        кроме asyncio.CancelledError (остановка задачи пользователем)."""
        try:
            args = self.parse_args(raw_args)

            verdict = decide(self.category, ctx.settings.approval_mode)
            if verdict == "ask" and needs_classifier(ctx.settings.approval_mode):
                verdict = self.auto_verdict(args, ctx)
            # Если в этом запуске уже читалось содержимое с признаками
            # промпт-инъекции, действие наружу могло быть подсказано атакой —
            # спрашиваем пользователя, даже если режим «Авто» разрешил бы само.
            injection_guard = (
                verdict == "allow"
                and (needs_classifier(ctx.settings.approval_mode) or ctx.settings.approval_mode == "autopilot")
                and self.category in ("execute", "network")
                and bool(ctx.scratch.get("injection_flags"))
            )
            if injection_guard:
                verdict = "ask"
            # Прогрессивная автономия: необратимое действие всегда требует
            # подтверждения, даже если режим (Авто/Правки без вопросов) разрешил
            # бы его сам. Исключение — «Без подтверждений»: это явный выбор.
            if self.irreversible and verdict == "allow" and ctx.settings.approval_mode != "bypass":
                verdict = "ask"

            # Независимый верификатор шага: детерминированная оценка риска по
            # РЕАЛЬНЫМ аргументам, не доверяя классификации модели. Разрушительные
            # паттерны сверяем только с командами/сетью (не с содержимым файлов).
            scan = collect_text(args.model_dump()) if self.category in ("execute", "network") else ""
            risk = assess(category=self.category, irreversible=self.irreversible, scan_text=scan)
            if risk.blocked:
                # Катастрофическое — не выполняем НИКОГДА, даже с подтверждением.
                raise PermissionDenied(
                    f"'{self.name}' заблокирован политикой безопасности "
                    f"({', '.join(risk.reasons)}). Если это действительно нужно — "
                    "попроси пользователя выполнить это вручную."
                )
            # Высокий риск требует подтверждения, даже если режим разрешил бы сам
            # (кроме явного «Без подтверждений»). Управляется settings.risk_gate.
            if (
                ctx.settings.risk_gate
                and verdict == "allow"
                and rank(risk.tier) >= rank("high")
                and ctx.settings.approval_mode != "bypass"
            ):
                verdict = "ask"

            # "Autopilot": everything goes, except reconfiguring the machine itself.
            system_why = system_change(scan) if ctx.settings.approval_mode == "autopilot" and scan else ""
            if verdict == "allow" and system_why:
                verdict = "ask"

            if verdict == "block":
                raise PermissionDenied(
                    f"'{self.name}' недоступен. {block_reason(self.category, ctx.settings.approval_mode)}"
                )
            if verdict == "ask":
                approved = await self._ask_approval(
                    args, ctx, injection_guard=injection_guard, risk=risk
                )
                if not approved:
                    raise PermissionDenied(
                        f"Пользователь отклонил вызов '{self.name}'. "
                        "Предложи другой способ или спроси уточнения."
                    )

            coro = self.run(args, ctx)
            if self.timeout:
                result = await asyncio.wait_for(coro, timeout=self.timeout)
            else:
                result = await coro

        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return ToolResult.fail(
                f"'{self.name}' превысил лимит {self.timeout} с и был остановлен.",
                timeout=True,
            )
        except ToolError as exc:
            return ToolResult.fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - инструмент не должен ронять цикл
            return ToolResult.fail(f"{type(exc).__name__}: {exc}")

        if isinstance(result, ToolResult):
            out = result
        else:
            out = ToolResult(content=str(result))

        limit = self.max_output_chars or ctx.settings.tool_output_limit
        out.content = truncate_output(out.content, limit)
        return out

    async def _ask_approval(
        self,
        args: BaseModel,
        ctx: ToolContext,
        *,
        injection_guard: bool = False,
        risk: Any = None,
    ) -> bool:
        reason = self.approval_reason(args)
        if injection_guard:
            # Пользователь должен понимать, почему спрашиваем в режиме «Авто».
            reason = tr("appr.injection") + "\n" + reason
        request = ApprovalRequest(
            name=self.name,
            args=args.model_dump(mode="json"),
            reason=reason,
            category=self.category,
            tier=getattr(risk, "tier", "low"),
            reasons=list(getattr(risk, "reasons", []) or []),
        )
        try:
            return bool(await ctx.approver(request))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - отказ безопаснее, чем падение
            # Логируем обязательно: молчаливый except здесь однажды уже спрятал
            # поломку, из-за которой все подтверждения отклонялись сами собой,
            # а пользователь даже не видел запроса.
            logger.exception("Сбой в обработчике подтверждения для '%s'", self.name)
            return False

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Tool {self.name}>"
