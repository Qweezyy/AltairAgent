"""Общая обработка внешнего (недоверенного) содержимого для инструментов.

Всё, что пришло из интернета или из чужого документа, проходит здесь: текст
оборачивается рамкой «это данные», проверяется на промпт-инъекции, и при
подозрении предупреждаются и модель, и пользователь. Флаг подозрения кладётся
в scratch — по нему инструментальный слой поднимает планку подтверждения для
опасных действий наружу (см. Tool.invoke).
"""

from __future__ import annotations

from core.events import LogEvent
from core.security.injection import wrap_external
from core.tools.base import ToolContext

#: Ключ в scratch: список причин подозрения на инъекцию за текущий запуск.
INJECTION_FLAGS_KEY = "injection_flags"


async def guard_external(ctx: ToolContext, text: str, source: str = "") -> str:
    """Оборачивает внешний текст как данные и предупреждает о подозрении.

    Возвращает текст, готовый к отдаче модели.
    """
    wrapped, report = wrap_external(text, source)
    if report.flagged:
        ctx.scratch.setdefault(INJECTION_FLAGS_KEY, []).append(f"{source}: {report.summary()}")
        try:
            await ctx.emitter(
                LogEvent(  # type: ignore[call-arg]
                    level="warning",
                    text=(
                        f"⚠️ В содержимом «{source or 'внешний источник'}» обнаружены признаки "
                        f"промпт-инъекции ({report.summary()}). Отношусь к нему как к данным."
                    ),
                )
            )
        except Exception:  # noqa: BLE001 - предупреждение не должно ронять чтение
            pass
    return wrapped
