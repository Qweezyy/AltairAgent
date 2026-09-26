"""Vision Self-Audit: скриншот вёрстки → критика мультимодальной моделью.

Инструмент сам делает одноразовый запрос к ОСНОВНОЙ модели диалога с картинкой в
теле сообщения и возвращает АГЕНТУ текстовую критику. Так картинка не пытается
пройти через `role=tool` (провайдеры её там не принимают): её видит модель внутри
инструмента, а наружу уходит уже разбор. Отдельной vision-модели нет.
"""

from __future__ import annotations

import base64

from core.llm.openai_client import build_llm_client
from core.logging_setup import get_logger
from core.settings import Settings, get_settings

logger = get_logger("vision.audit")

#: Лимит на ответ аудита: список проблем должен быть плотным, а не эссе.
_MAX_TOKENS = 1100

_SYSTEM = (
    "Ты — строгий ревьюер веб-вёрстки и UI. Тебе дают скриншот отрисованного "
    "интерфейса. Твоя задача — найти конкретные визуальные дефекты, а не хвалить. "
    "Проверяй: выравнивание и отступы (сетка, симметрия), читаемость и контраст "
    "текста, переполнение и обрезку контента, наложения элементов, «сломанные» "
    "или не загрузившиеся картинки, горизонтальный скролл, консистентность "
    "цветов и кнопок, адаптивность под данный размер экрана. "
    "Отвечай кратко и по делу на русском."
)

_INSTRUCTION = (
    "Проверь этот скриншот UI ({viewport}). Верни:\n"
    "1) СПИСОК ПРОБЛЕМ — каждая строкой, с указанием, где именно и что не так;\n"
    "2) для каждой проблемы — конкретную правку (какой CSS/HTML менять);\n"
    "3) в конце вердикт: «Готово к показу» или «Нужны правки».\n"
    "Если всё хорошо — так и напиши, не выдумывай проблемы."
)


async def audit_screenshot(
    png_bytes: bytes,
    *,
    viewport: str = "desktop",
    focus: str = "",
    settings: Settings | None = None,
    model: str | None = None,
) -> str:
    """Отправляет PNG ОСНОВНОЙ модели (той же, что ведёт диалог) и возвращает
    текстовую критику вёрстки. Отдельной vision-модели больше нет — если основная
    модель понимает картинки, аудит работает; если нет — вернётся отказ модели."""
    settings = settings or get_settings()
    data_url = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")

    instruction = _INSTRUCTION.format(viewport=viewport)
    if focus.strip():
        instruction += f"\nОсобое внимание: {focus.strip()}"

    messages = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]

    client = build_llm_client(model or settings.default_model, settings)
    try:
        turn = await client.complete(messages, max_tokens=_MAX_TOKENS)
    finally:
        await client.aclose()

    text = (turn.content or "").strip()
    return text or "Модель не вернула разбор скриншота."


_DESCRIBE_SYSTEM = (
    "Ты внимательно смотришь на изображение и описываешь его для инженера, который "
    "его не видит. Будь конкретным и точным: что изображено, текст на картинке "
    "(дословно, если читаемо), схемы, графики, ошибки на скриншотах, цвета, состояние UI. "
    "Не выдумывай того, чего не видно. Отвечай на русском."
)


async def describe_image(
    image_bytes: bytes,
    *,
    question: str = "",
    mime: str = "image/png",
    settings: Settings | None = None,
    model: str | None = None,
) -> str:
    """Отправляет произвольное изображение в vision-модель и возвращает описание/ответ.

    Универсальный аналог `audit_screenshot` для любых картинок (диаграммы, фото
    ошибок, макеты): картинку видит модель ВНУТРИ инструмента, наружу уходит текст,
    т.к. провайдеры не принимают изображение в сообщении роли tool.
    """
    settings = settings or get_settings()
    data_url = f"data:{mime};base64," + base64.b64encode(image_bytes).decode("ascii")

    instruction = question.strip() or "Опиши подробно, что на этом изображении."
    messages = [
        {"role": "system", "content": _DESCRIBE_SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]

    client = build_llm_client(model or settings.default_model, settings)
    try:
        turn = await client.complete(messages, max_tokens=_MAX_TOKENS)
    finally:
        await client.aclose()

    text = (turn.content or "").strip()
    return text or "Модель не вернула описание изображения."
