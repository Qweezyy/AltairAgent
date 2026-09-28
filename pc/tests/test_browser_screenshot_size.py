"""A tab without a real size no longer gives a one-pixel screenshot.

With the browser panel closed, the panel's box is 0×0 and tabs the agent opened then got a
1×1 page: every browser_screenshot came back as one pixel (8 of the user's screenshots were).
The panel now keeps a real size for hidden tabs; the backend also re-renders such a tab at a
desktop size for the shot.
"""

from __future__ import annotations

import pytest

from core.browser_session import FALLBACK_SHOT, AgentBrowser, png_size
from core.errors import ToolError

playwright = pytest.importorskip("playwright.async_api")


class _OnePage(AgentBrowser):
    def __init__(self, page) -> None:  # noqa: D107 - only what screenshot_png needs
        self._the_page = page

    async def page(self):
        return self._the_page


@pytest.fixture()
async def page():
    try:
        async with playwright.async_playwright() as p:
            browser = await p.chromium.launch()
            pg = await browser.new_page()
            await pg.set_content("<h1 style='font-size:80px'>Hello</h1><p>page body</p>")
            yield pg
            await browser.close()
    except Exception as exc:  # noqa: BLE001 - no Chromium on this machine
        pytest.skip(f"Chromium is not available: {exc}")


async def test_a_sizeless_tab_is_shot_at_a_desktop_size(page):
    await page.set_viewport_size({"width": 1, "height": 1})   # a tab opened with the panel closed
    assert png_size(await page.screenshot()) == (1, 1)        # what the agent used to get
    png = await _OnePage(page).screenshot_png()
    assert png_size(png) == FALLBACK_SHOT
    # Only for the shot: the desktop size is not left on the page. (Headless Playwright sizes
    # pages by the same override, so here the page falls back to its window, not to 1×1; the
    # embedded tabs have no override of their own and keep their real size.)
    assert await page.evaluate("[innerWidth, innerHeight]") != list(FALLBACK_SHOT)


async def test_a_normal_tab_is_shot_as_it_is(page):
    await page.set_viewport_size({"width": 900, "height": 700})
    assert png_size(await _OnePage(page).screenshot_png()) == (900, 700)


def test_png_size_reads_the_header():
    assert png_size(b"not a png at all, just bytes") == (0, 0)
    header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (640).to_bytes(4, "big") + (480).to_bytes(4, "big")
    assert png_size(header) == (640, 480)


async def test_a_shot_that_stays_empty_says_so():
    class _Blank:
        context = None

        async def screenshot(self, **kw):
            return b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (1).to_bytes(4, "big") + (1).to_bytes(4, "big")

    class _Browser(_OnePage):
        async def _shot_at_size(self, page, full_page):
            return await page.screenshot()

    with pytest.raises(ToolError, match="empty"):
        await _Browser(_Blank()).screenshot_png()
