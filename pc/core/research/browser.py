"""Headless-браузер для страниц, которые собираются JavaScript'ом.

Зачем: `fetch_url` качает исходный HTML. У современных сайтов там пусто —
текст появляется только после выполнения скриптов. Такие страницы открываем
настоящим Chromium через Playwright.

Браузер запускается лениво и переиспользуется: старт занимает секунды, и
поднимать его на каждую ссылку — самый дорогой способ читать интернет.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.errors import ToolError
from core.logging_setup import get_logger

logger = get_logger("research.browser")

#: Ресурсы, которые не влияют на текст, но занимают почти весь трафик.
BLOCKED_RESOURCES = {"image", "media", "font"}

_browser: Any = None
_playwright: Any = None
_lock = asyncio.Lock()


@dataclass(slots=True)
class RenderedPage:
    """Страница после выполнения скриптов."""

    url: str
    title: str
    text: str
    #: Ссылки со страницы: (текст, абсолютный адрес).
    links: list[tuple[str, str]]
    screenshot: bytes | None = None


def _install_hint(exc: Exception) -> ToolError:
    return ToolError(
        "Headless-браузер недоступен. Установите его один раз командой:\n"
        "    pip install playwright\n"
        "    python -m playwright install chromium\n"
        f"Исходная ошибка: {exc}"
    )


def _configure_browser_path() -> None:
    """Направляет frozen-сборку к браузеру Playwright пользователя."""
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    user_browsers = Path.home() / "AppData" / "Local" / "ms-playwright"
    if user_browsers.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(user_browsers)


async def get_browser() -> Any:
    """Общий экземпляр Chromium. Поднимается при первом обращении."""
    global _browser, _playwright

    async with _lock:
        if _browser is not None and _browser.is_connected():
            return _browser

        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise _install_hint(exc) from exc

        try:
            _configure_browser_path()
            _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(
                headless=True,
                args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"],
            )
        except Exception as exc:  # noqa: BLE001 - чаще всего браузер просто не скачан
            _browser = None
            raise _install_hint(exc) from exc

        logger.info("Headless-браузер запущен")
        return _browser


async def close_browser() -> None:
    """Закрывает браузер (вызывается при остановке приложения)."""
    global _browser, _playwright
    async with _lock:
        if _browser is not None:
            try:
                await _browser.close()
            except Exception:  # noqa: BLE001 - на выходе падать не из-за чего
                logger.debug("Ошибка закрытия браузера", exc_info=True)
        if _playwright is not None:
            try:
                await _playwright.stop()
            except Exception:  # noqa: BLE001
                logger.debug("Ошибка остановки playwright", exc_info=True)
        _browser = None
        _playwright = None


async def render_page(
    url: str,
    *,
    wait_for: str | None = None,
    timeout: float = 30.0,
    screenshot: bool = False,
    scroll: bool = True,
) -> RenderedPage:
    """Открывает страницу, ждёт отрисовки и возвращает её содержимое."""
    browser = await get_browser()
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        locale="ru-RU",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    )

    try:
        page = await context.new_page()
        if not screenshot:
            # Картинки и шрифты не нужны для текста, а грузятся дольше всего.
            await page.route(
                "**/*",
                lambda route: asyncio.ensure_future(
                    route.abort()
                    if route.request.resource_type in BLOCKED_RESOURCES
                    else route.continue_()
                ),
            )

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        except Exception as exc:  # noqa: BLE001 - таймаут навигации ожидаем
            raise ToolError(f"Не удалось открыть '{url}': {exc}") from exc

        if wait_for:
            try:
                await page.wait_for_selector(wait_for, timeout=timeout * 1000)
            except Exception as exc:  # noqa: BLE001
                raise ToolError(
                    f"Элемент '{wait_for}' не появился за {timeout:.0f} с. "
                    "Возможно, селектор неверный или контент за авторизацией."
                ) from exc
        else:
            # Немного ждём сеть: часть текста приходит запросами после загрузки.
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:  # noqa: BLE001 - бесконечные опросы это нормально
                pass

        if scroll:
            await _scroll_to_bottom(page)

        title = await page.title()
        text = await page.evaluate(_EXTRACT_TEXT_JS)
        links = await page.evaluate(_EXTRACT_LINKS_JS)
        shot = await page.screenshot(full_page=False) if screenshot else None

        return RenderedPage(
            url=page.url,
            title=title or url,
            text=(text or "").strip(),
            links=[(item["text"], item["href"]) for item in links],
            screenshot=shot,
        )
    finally:
        await context.close()


async def _scroll_to_bottom(page: Any, steps: int = 5) -> None:
    """Прокручивает страницу: ленивая подгрузка иначе не сработает."""
    for _ in range(steps):
        try:
            at_bottom = await page.evaluate(
                "() => { const before = window.scrollY;"
                " window.scrollBy(0, window.innerHeight * 0.9);"
                " return window.scrollY === before; }"
            )
        except Exception:  # noqa: BLE001 - страница могла закрыться сама
            return
        if at_bottom:
            return
        await asyncio.sleep(0.35)


#: Достаём именно содержательный текст, а не меню с подвалом.
_EXTRACT_TEXT_JS = """
() => {
  document.querySelectorAll('script,style,noscript,nav,header,footer,aside,iframe')
    .forEach((el) => el.remove());
  const main = document.querySelector('article, main, [role="main"]') || document.body;
  const parts = [];
  main.querySelectorAll('h1,h2,h3,h4,p,li,pre,blockquote,td,dd').forEach((el) => {
    const text = (el.innerText || '').trim();
    if (!text) return;
    if (el.tagName[0] === 'H') parts.push('\\n' + '#'.repeat(+el.tagName[1]) + ' ' + text);
    else if (el.tagName === 'LI') parts.push('- ' + text);
    else if (el.tagName === 'PRE') parts.push('```\\n' + text + '\\n```');
    else parts.push(text);
  });
  return parts.length ? parts.join('\\n') : (main.innerText || '').trim();
}
"""

_EXTRACT_LINKS_JS = """
() => Array.from(document.querySelectorAll('a[href]'))
  .map((a) => ({ text: (a.innerText || '').trim().slice(0, 120), href: a.href }))
  .filter((item) => item.href.startsWith('http') && item.text)
  .slice(0, 80)
"""
