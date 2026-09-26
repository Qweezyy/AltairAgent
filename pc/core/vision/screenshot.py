"""Снятие скриншотов страниц и элементов через общий headless-Chromium.

Переиспользует браузер из `core.research.browser` (ленивый общий Chromium), но,
в отличие от `render_page`, здесь картинки и шрифты НЕ блокируются — для аудита
вёрстки нужен реальный визуальный результат, а не голый текст.

Цель — три режима захвата из плана: страница целиком (`full_page`), конкретный
элемент по селектору (кадрируется по его bounding box) и произвольный прямоугольник
(`clip`), плюс мульти-вьюпорт (Desktop / Tablet / Mobile) для проверки адаптивности.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.errors import ToolError
from core.logging_setup import get_logger
from core.research.browser import get_browser
from core.security.paths import resolve_path
from core.settings import Settings, get_settings

logger = get_logger("vision.screenshot")

#: Ключевые разрешения из плана. desktop берём 1280×800 (а не 1920×1080): в
#: панель превью 4K-скриншот всё равно ужимается, а вес base64 для vision-модели
#: растёт квадратично.
VIEWPORTS: dict[str, tuple[int, int]] = {
    "desktop": (1280, 800),
    "tablet": (768, 1024),
    "mobile": (375, 667),
}


def _resolve_target(target: str, settings: Settings) -> str:
    """Превращает цель в URL для браузера.

    http(s):// и file:// — как есть; всё остальное считаем путём внутри рабочей
    папки и отдаём как file://. Так агент может снять и локальный dev-сервер
    (`http://localhost:5173`), и собранный HTML-файл, не покидая песочницы.
    """
    target = target.strip()
    scheme = urlparse(target).scheme
    if scheme in ("http", "https", "file"):
        return target
    path = resolve_path(target, settings=settings, must_exist=True)
    return Path(path).as_uri()


async def capture_screenshot(
    target: str,
    *,
    full_page: bool = True,
    selector: str | None = None,
    clip: dict[str, float] | None = None,
    viewport: str = "desktop",
    timeout: float = 30.0,
    settings: Settings | None = None,
) -> bytes:
    """Возвращает PNG-байты скриншота.

    Приоритет режимов: `selector` → элемент, иначе `clip` → область, иначе вся
    страница (`full_page`) или видимая область.
    """
    settings = settings or get_settings()
    url = _resolve_target(target, settings)
    width, height = VIEWPORTS.get(viewport, VIEWPORTS["desktop"])

    browser = await get_browser()
    context = await browser.new_context(
        viewport={"width": width, "height": height},
        device_scale_factor=1,
        locale="ru-RU",
    )
    try:
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        except Exception as exc:  # noqa: BLE001 - таймаут/недоступность ожидаемы
            raise ToolError(f"Не удалось открыть «{target}»: {exc}") from exc

        # Даём странице дорисоваться: шрифты, картинки, отложенные запросы.
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:  # noqa: BLE001 - вечный поллинг это норма
            pass

        if selector:
            element = await page.query_selector(selector)
            if element is None:
                raise ToolError(f"Элемент «{selector}» не найден на странице.")
            shot: Any = await element.screenshot()
        elif clip:
            shot = await page.screenshot(clip=_valid_clip(clip))
        else:
            shot = await page.screenshot(full_page=full_page)
        return shot
    finally:
        await context.close()


def _valid_clip(clip: dict[str, float]) -> dict[str, float]:
    try:
        rect = {k: float(clip[k]) for k in ("x", "y", "width", "height")}
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError("clip должен содержать числа x, y, width, height.") from exc
    if rect["width"] <= 0 or rect["height"] <= 0:
        raise ToolError("Ширина и высота clip должны быть положительными.")
    return rect
