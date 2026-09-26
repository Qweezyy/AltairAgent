"""Инструменты визуальной верификации вёрстки (Vision-in-the-loop).

Два инструмента:
  * screenshot_ui — снять скриншот (страница/элемент/область, разные вьюпорты),
    сохранить артефактом и показать в «Превью»;
  * audit_ui — снять скриншот И отправить его в мультимодальную модель, вернув
    агенту список визуальных дефектов с предложениями правок.

Типичный цикл вёрстки: правка CSS → audit_ui → правка по замечаниям → audit_ui,
пока не «Готово к показу», и только потом показывать результат пользователю.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from core.events import ArtifactCreated, ShowImage
from core.i18n import tr
from core.security.paths import resolve_path, safe_relpath
from core.tools.base import Tool, ToolContext, ToolResult
from core.vision import VIEWPORTS, capture_screenshot

#: Расширение → MIME для передачи картинки модели.
_IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}

#: Инструкция для аудита вёрстки — уходит основной модели вместе со скриншотом.
_AUDIT_PROMPT = (
    "Это скриншот интерфейса ({viewport}). Проверь вёрстку: выравнивание и отступы, "
    "читаемость и контраст текста, переполнение и обрезку контента, наложения элементов, "
    "«сломанные»/не загрузившиеся картинки, горизонтальный скролл, консистентность цветов "
    "и кнопок, адаптивность под этот размер. Верни СПИСОК конкретных дефектов (где и что не "
    "так) с правкой для каждого (какой CSS/HTML менять) и вердикт «Готово к показу» или "
    "«Нужны правки». Если всё хорошо — так и скажи, не выдумывай проблемы."
)


def _queue_vision(ctx: ToolContext, data: bytes, mime: str, prompt: str) -> None:
    """Кладёт картинку в очередь показа основной модели: раннер вольёт её в диалог
    как обычное вложение пользователя (мультимодальный формат), тем же путём, что и
    прикреплённые файлы. Так изображение видит ТА ЖЕ модель, что ведёт диалог."""
    import base64

    data_url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    part = {"type": "image_url", "image_url": {"url": data_url}}
    ctx.scratch.setdefault("_vision_pending", []).append({"text": prompt, "parts": [part]})


def _is_local_target(target: str) -> bool:
    """Локальная ли цель (свой dev-сервер или файл) — тогда approval не нужен."""
    parsed = urlparse(target.strip())
    if parsed.scheme in ("", "file"):
        return True
    if parsed.scheme in ("http", "https"):
        host = parsed.hostname or ""
        return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
    return False


class _CaptureArgs(BaseModel):
    target: str = Field(
        description="URL (например http://localhost:5173) или путь к HTML-файлу в рабочей папке"
    )
    selector: str | None = Field(
        default=None, description="CSS-селектор элемента для точечного скриншота (например «#header»)"
    )
    full_page: bool = Field(default=True, description="Снять страницу целиком, а не только видимую область")
    viewport: str = Field(
        default="desktop", description="Размер экрана: desktop (1280×800), tablet (768×1024), mobile (375×667)"
    )


async def _grab(args: _CaptureArgs, ctx: ToolContext) -> bytes:
    viewport = args.viewport if args.viewport in VIEWPORTS else "desktop"
    return await capture_screenshot(
        args.target,
        full_page=args.full_page,
        selector=args.selector,
        viewport=viewport,
        settings=ctx.settings,
    )


def _save_artifact(png: bytes, ctx: ToolContext, label: str) -> str:
    """Сохраняет PNG в рабочую папку и регистрирует артефакт для «Превью».

    Возвращает относительный путь. Складываем в подпапку `.screenshots`, чтобы не
    засорять корень проекта.
    """
    name = f".screenshots/{label}-{int(time.time())}.png"
    destination = resolve_path(name, settings=ctx.settings)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(png)
    # Прямые слэши: путь уходит в артефакт и превращается в URL превью, где
    # обратный слэш Windows сломал бы адрес.
    return safe_relpath(destination, ctx.settings).replace("\\", "/")


async def _emit_artifact(relative: str, size: int, ctx: ToolContext) -> None:
    await ctx.emitter(
        ArtifactCreated(path=relative, name=relative.split("/")[-1], kind="image", size_bytes=size)
    )


class ScreenshotUITool(Tool):
    name = "screenshot_ui"
    description = (
        "Делает скриншот веб-страницы или локального HTML-файла и показывает его в панели «Превью». "
        "Умеет снимать страницу целиком, конкретный элемент по CSS-селектору и в разных размерах "
        "экрана (desktop/tablet/mobile). Для визуального контроля вёрстки без анализа моделью."
    )
    Args = _CaptureArgs
    category = "network"
    dangerous = True
    timeout = None

    def approval_reason(self, args: _CaptureArgs) -> str:  # type: ignore[override]
        return tr("appr.screenshot", target=args.target)

    def auto_verdict(self, args: _CaptureArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        return "allow" if _is_local_target(args.target) else "ask"

    async def run(self, args: _CaptureArgs, ctx: ToolContext) -> ToolResult:
        png = await _grab(args, ctx)
        relative = _save_artifact(png, ctx, "shot")
        await _emit_artifact(relative, len(png), ctx)
        return ToolResult(
            content=(
                f"Скриншот сохранён: {relative} ({len(png) // 1024} КБ, {args.viewport}). "
                "Открылся в панели «Превью»."
            )
        )


class ShowImageArgs(BaseModel):
    path: str = Field(description="Путь к изображению в рабочей папке, которое показать пользователю")
    caption: str = Field(default="", description="Короткая подпись под картинкой (что на ней)")


class ShowImageTool(Tool):
    name = "show_image"
    description = (
        "Показывает конкретное изображение ПРЯМО в ленте ответа пользователю (не в панели «Превью»). "
        "Используй, когда сам решил показать результат — например готовый скриншот интерфейса — "
        "с короткой подписью. Скриншоты не выводятся автоматически: в чат попадает только то, "
        "что ты покажешь этим инструментом."
    )
    Args = ShowImageArgs
    category = "read"
    timeout = None

    async def run(self, args: ShowImageArgs, ctx: ToolContext) -> ToolResult:
        path = resolve_path(args.path, settings=ctx.settings, must_exist=True, must_be_file=True)
        if path.suffix.lower() not in _IMAGE_MIME and path.suffix.lower() != ".svg":
            return ToolResult.fail(f"'{args.path}' не похоже на изображение.")
        relative = safe_relpath(path, ctx.settings).replace("\\", "/")
        await ctx.emitter(
            ShowImage(path=relative, name=path.name, caption=args.caption.strip())
        )
        return ToolResult(
            content=f"Показал изображение «{relative}» пользователю в ленте ответа."
        )


class ViewImageArgs(BaseModel):
    path: str = Field(description="Путь к изображению в рабочей папке (png/jpg/gif/webp/bmp)")
    question: str = Field(
        default="", description="Что именно спросить о картинке (пусто — подробное описание)"
    )


class ViewImageTool(Tool):
    name = "view_image"
    description = (
        "«Смотрит» на изображение в рабочей папке через мультимодальную модель и возвращает "
        "его текстовое описание/ответ на вопрос: диаграммы, фото ошибок, макеты, скриншоты. "
        "Так read_file не спотыкается о бинарные картинки — их разбирает эта модель."
    )
    Args = ViewImageArgs
    category = "read"
    timeout = None

    async def run(self, args: ViewImageArgs, ctx: ToolContext) -> ToolResult:
        path = resolve_path(args.path, settings=ctx.settings, must_exist=True, must_be_file=True)
        mime = _IMAGE_MIME.get(path.suffix.lower())
        if mime is None:
            return ToolResult.fail(
                f"'{args.path}' не похоже на изображение (поддерживаются "
                f"{', '.join(sorted(_IMAGE_MIME))})."
            )
        data = path.read_bytes()
        relative = safe_relpath(path, ctx.settings).replace("\\", "/")
        await _emit_artifact(relative, len(data), ctx)
        prompt = args.question.strip() or f"Опиши подробно, что на изображении ({relative})."
        _queue_vision(ctx, data, mime, prompt)
        return ToolResult(content=(
            f"Изображение {relative} приложено к диалогу — оно придёт следующим "
            "сообщением. Посмотри его и ответь по существу."
        ))


class AuditUIArgs(_CaptureArgs):
    focus: str = Field(
        default="", description="На что обратить особое внимание (например «проверь мобильную вёрстку шапки»)"
    )


class AuditUITool(Tool):
    name = "audit_ui"
    description = (
        "Делает скриншот интерфейса и отправляет его в мультимодальную модель для визуального аудита: "
        "возвращает список дефектов вёрстки (отступы, контраст, переполнение, наложения, битые картинки) "
        "с предложениями правок и вердиктом «Готово к показу / Нужны правки». Используй после вёрстки UI "
        "и повторяй после правок, пока не станет чисто, — только потом показывай результат пользователю."
    )
    Args = AuditUIArgs
    category = "network"
    dangerous = True
    timeout = None

    def approval_reason(self, args: AuditUIArgs) -> str:  # type: ignore[override]
        return tr("appr.audit", target=args.target)

    def auto_verdict(self, args: AuditUIArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        return "allow" if _is_local_target(args.target) else "ask"

    async def run(self, args: AuditUIArgs, ctx: ToolContext) -> ToolResult:
        png = await _grab(args, ctx)
        relative = _save_artifact(png, ctx, "audit")
        await _emit_artifact(relative, len(png), ctx)

        viewport = args.viewport if args.viewport in VIEWPORTS else "desktop"
        prompt = _AUDIT_PROMPT.format(viewport=viewport)
        if args.focus.strip():
            prompt += f"\nОсобое внимание: {args.focus.strip()}"
        _queue_vision(ctx, png, "image/png", prompt)
        return ToolResult(content=(
            f"Скриншот {relative} сделан и приложен к диалогу — он придёт следующим "
            "сообщением. Оцени вёрстку сам и, если есть дефекты, исправь их."
        ))
