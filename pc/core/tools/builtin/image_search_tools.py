"""Поиск картинок в интернете как явный инструмент агента.

В дополнение к инлайн-маркерам ![подпись](<img:запрос>) в ответе: этим инструментом
агент может ЯВНО подобрать/проверить картинки (посмотреть альтернативы, взять
конкретный URL, чтобы скачать через download_file или показать).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.i18n import tr
from core.research.images import find_images
from core.tools.base import Tool, ToolContext, ToolResult


class FindImagesArgs(BaseModel):
    query: str = Field(description="Что искать: конкретный запрос с контекстом, например «RTX 4090 видеокарта»")
    limit: int = Field(default=4, ge=1, le=10, description="Сколько кандидатов вернуть")


class FindImagesTool(Tool):
    name = "find_images"
    description = (
        "Ищет релевантные картинки в интернете (Wikipedia/Wikimedia, при заданном SearXNG — "
        "и его image-поиск), отсеивает логотипы/иконки/мусор и возвращает список URL с "
        "подписями. Для наглядности проще вставлять картинки прямо в текст ответа маркером "
        "![подпись](<img:запрос>); этот инструмент нужен, когда надо выбрать из нескольких "
        "вариантов, проверить наличие картинки или взять URL для download_file/show_image."
    )
    Args = FindImagesArgs
    category = "network"
    dangerous = False

    def auto_verdict(self, args: FindImagesArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        return "allow"  # чтение из репутабельных источников, ничего не меняет

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.images", query=args.query)

    async def run(self, args: FindImagesArgs, ctx: ToolContext) -> ToolResult:
        found = await find_images(args.query, settings=ctx.settings, limit=args.limit)
        if not found:
            return ToolResult(
                content=(
                    f"По запросу «{args.query}» подходящих картинок не нашлось. "
                    "Попробуй уточнить запрос (добавь контекст: название игры/серии/категории)."
                )
            )
        lines = [f"Найдено картинок: {len(found)}"]
        for i, img in enumerate(found, 1):
            title = img.get("title", "")
            page = img.get("page", "")
            lines.append(f"{i}. {img['url']}" + (f"  — {title}" if title else "") + (f"  ({page})" if page else ""))
        lines.append(
            "\nЧтобы показать картинку в ответе, вставь маркер в текст: "
            f"![{found[0].get('title') or args.query}](<img:{args.query}>)"
        )
        return ToolResult(content="\n".join(lines))
