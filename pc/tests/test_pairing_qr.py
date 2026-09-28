"""The pairing QR code is readable by a phone camera off the screen.

The old code had no viewBox while the page CSS forced it to 180px, so the browser cropped it
to a corner and no scanner could read it ("the phone does not scan"). This renders the real
SVG with the app's own CSS in Chromium, degrades the shot like a camera pointed at a monitor
and decodes it with ZXing (the family the Android app scans with).
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from core.pairing import build_pair_link, qr_svg

LINK = build_pair_link("http://192.168.8.40:8137", "x" * 32, "D:/AI_Agent/some/nested/project")
CSS = (Path(__file__).parent.parent / "static" / "redesign.layout.css").read_text(encoding="utf-8")


def test_the_svg_scales_instead_of_being_cropped():
    svg = qr_svg(LINK)
    assert svg and 'shape-rendering="crispEdges"' in svg
    size = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    width = re.search(r'width="(\d+)"', svg)
    assert size and width and size.group(1) == width.group(1)  # resizing keeps the whole code
    assert 250 <= int(width.group(1)) <= 330                      # big enough to aim a camera at


async def test_a_camera_shot_of_the_screen_decodes():
    zxingcpp = pytest.importorskip("zxingcpp")
    playwright = pytest.importorskip("playwright.async_api")
    from PIL import Image, ImageFilter

    rules = "\n".join(line for line in CSS.splitlines() if ".pair-qr" in line)
    try:
        async with playwright.async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(device_scale_factor=1)
            await page.set_content(f"<style>{rules}</style><div class='pair-qr-box' id='q'>{qr_svg(LINK)}</div>")
            png = await page.locator("#q").screenshot()
            await browser.close()
    except Exception as exc:  # noqa: BLE001 - no headless Chromium here
        pytest.skip(f"no headless Chromium: {exc}")

    shot = Image.open(io.BytesIO(png)).convert("L")
    readable = 0
    for shrink, blur in [(1.0, 0), (0.6, 0.8), (0.45, 0.8), (0.6, 1.4)]:
        im = shot.resize((int(shot.width * shrink), int(shot.height * shrink)), Image.BILINEAR)
        im = im.filter(ImageFilter.GaussianBlur(blur)) if blur else im
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=60)
        if any(r.text == LINK for r in zxingcpp.read_barcodes(Image.open(buf))):
            readable += 1
    assert readable == 4


def test_a_chats_scratch_folder_stays_out_of_the_link(settings):
    from core.pairing import pair_info

    scratch = settings.app_dir / "storage" / "chat_files" / "abc123"
    scratch.mkdir(parents=True)
    info = pair_info(str(scratch), settings, 8137, ["192.168.1.5"])
    assert "&w=" not in info["link"] and info["workspace"] == ""
    chosen = pair_info(str(settings.workspace), settings, 8137, ["192.168.1.5"])
    assert "&w=" in chosen["link"]
