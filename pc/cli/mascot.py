"""Alti, the mascot, drawn in the terminal.

The same chubby four-point star as the app window's mascot.js — the same outline, the same
warm gradient and gloss, the same eyes per mood — rasterised with Pillow and printed in half
blocks (▀/▄ with truecolor foreground and background), so it looks like the window's Alti in
any truecolor terminal. Without Pillow there is a plain glyph instead.
"""

from __future__ import annotations

import math
from functools import lru_cache

from rich.text import Text

from cli import theme

#: The outline from mascot.js (a 24×24 box, centre 12,12): four cubic Béziers.
_STAR = [
    ((12, 0.6), (13.2, 7.7), (16.3, 10.8), (23.4, 12)),
    ((23.4, 12), (16.3, 13.2), (13.2, 16.3), (12, 23.4)),
    ((12, 23.4), (10.8, 16.3), (7.7, 13.2), (0.6, 12)),
    ((0.6, 12), (7.7, 10.8), (10.8, 7.7), (12, 0.6)),
]

#: The gradient stops of mascot.js (light from the top left).
_FILL = [(0.0, (255, 246, 218)), (0.42, (255, 211, 122)), (0.80, (241, 169, 60)), (1.0, (214, 129, 30))]
_INK = (36, 22, 8, 255)

MOODS = ("idle", "think", "happy", "help", "sad", "sleep")


def _bezier(p0, p1, p2, p3, steps: int = 24) -> list[tuple[float, float]]:
    out = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        out.append(
            (
                u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
                u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1],
            )
        )
    return out


def _outline(scale: float, dx: float = 0.0, dy: float = 0.0, k: float = 1.0) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for seg in _STAR:
        pts += _bezier(*seg)
    return [((12 + (x - 12) * k + dx) * scale, (12 + (y - 12) * k + dy) * scale) for x, y in pts]


def _gradient(t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    for (a, ca), (b, cb) in zip(_FILL, _FILL[1:]):
        if t <= b:
            f = (t - a) / (b - a) if b > a else 0
            return tuple(round(ca[i] + (cb[i] - ca[i]) * f) for i in range(3))  # type: ignore[return-value]
    return _FILL[-1][1]


def _eyes(draw, s: float, mood: str, blink: bool) -> None:
    ex1, ex2, ey, w, h = 9.7, 14.3, 12.4, 1.5, 3.0

    def pill(cx: float, cy: float, ww: float, hh: float) -> None:
        draw.rounded_rectangle(
            [(cx - ww / 2) * s, (cy - hh / 2) * s, (cx + ww / 2) * s, (cy + hh / 2) * s],
            radius=min(ww, hh) / 2 * s,
            fill=_INK,
        )

    def arc(cx: float, up: bool) -> None:
        box = [(cx - 0.9) * s, (12.2 - 0.9) * s, (cx + 0.9) * s, (12.2 + 0.9) * s]
        draw.arc(box, 200 if up else 20, 340 if up else 160, fill=_INK, width=max(1, round(0.55 * s)))

    if mood == "happy":
        arc(ex1, True), arc(ex2, True)
        return
    if mood == "sleep" or blink:
        arc(ex1, False), arc(ex2, False)
        return
    if mood == "sad":
        pill(ex1, ey + 0.5, w, h * 0.8), pill(ex2, ey + 0.5, w, h * 0.8)
        draw.line([8.6 * s, 10.1 * s, 10.5 * s, 9.4 * s], fill=_INK, width=max(1, round(0.4 * s)))
        draw.line([15.4 * s, 10.1 * s, 13.5 * s, 9.4 * s], fill=_INK, width=max(1, round(0.4 * s)))
        return
    lift = -1.4 if mood == "help" else 0.0
    hh = h * 0.7 if mood == "think" else h
    pill(ex1, ey + lift, w, hh), pill(ex2, ey + lift, w, hh)
    for cx in (ex1, ex2):
        r = 0.42 * s
        x, y = (cx + 0.45) * s, (ey + lift - hh / 2 + 0.55) * s
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, 230))


def _star(img, s: float, k: float, dx: float, dy: float, glow: bool) -> None:
    """One star (the body or a satellite): gradient fill, rim, gloss."""
    from PIL import Image, ImageDraw, ImageFilter

    size = img.size
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).polygon(_outline(s, dx, dy, k), fill=255)
    fill = Image.new("RGBA", size)
    px = fill.load()
    cx, cy, r = (
        (12 + dx + (0.36 - 0.5) * 24 * k) * s,
        (12 + dy + (0.30 - 0.5) * 24 * k) * s,
        0.85 * 24 * k * s,
    )
    x0, y0 = int((12 + dx - 12 * k) * s), int((12 + dy - 12 * k) * s)
    x1, y1 = int((12 + dx + 12 * k) * s) + 1, int((12 + dy + 12 * k) * s) + 1
    for y in range(max(0, y0), min(size[1], y1)):
        for x in range(max(0, x0), min(size[0], x1)):
            c = _gradient(math.hypot(x - cx, y - cy) / r)
            px[x, y] = (*c, 255)
    if glow:
        halo = Image.new("RGBA", size, (255, 196, 90, 0))
        halo.putalpha(mask.filter(ImageFilter.GaussianBlur(1.2 * s)).point(lambda v: int(v * 0.35)))
        img.alpha_composite(halo)
    img.paste(fill, (0, 0), mask)
    # The rim: a darker warm edge, like the stroke in mascot.js.
    edge = mask.filter(ImageFilter.FIND_EDGES).point(lambda v: 200 if v > 40 else 0)
    img.paste(Image.new("RGBA", size, (201, 118, 26, 255)), (0, 0), edge)
    # The gloss on the upper left.
    gloss = Image.new("L", size, 0)
    gx, gy = (9.6 + dx - 12 + 12 * (1 - k) + (12 * k - 12 * k)) * s, (8.7 + dy) * s
    if k == 1.0:
        ImageDraw.Draw(gloss).ellipse([gx - 2.7 * s, gy - 1.7 * s, gx + 2.7 * s, gy + 1.7 * s], fill=95)
        gloss = gloss.rotate(24, center=(gx, gy)).filter(ImageFilter.GaussianBlur(0.6 * s))
        img.paste(Image.new("RGBA", size, (255, 255, 255, 255)), (0, 0), gloss)


@lru_cache(maxsize=64)
def sprite(rows: int, mood: str = "idle", satellites: bool = True, blink: bool = False, frame: int = 0):
    """Alti as an RGBA image `rows*2` pixels tall (two pixel rows per text row)."""
    from PIL import Image, ImageDraw

    px = rows * 2
    ss = 4  # supersampling: smooth edges after the downscale
    s = px * ss / 24.0
    img = Image.new("RGBA", (px * ss, px * ss), (0, 0, 0, 0))
    bob = (0.0, -0.6, -0.9, -0.6, 0.0, 0.3)[frame % 6] if mood in ("happy", "think") else 0.0
    if satellites:
        orbit = frame * math.pi / 9
        for ang, k, rad in ((orbit - 0.8, 0.40, 9.6), (orbit + math.pi - 0.6, 0.28, 10.4)):
            _star(img, s, k, math.cos(ang) * rad, math.sin(ang) * rad * 0.9, False)
    small = px < 16
    _star(img, s, 1.0, 0.0, bob, not small)
    if not small:
        eyes = Image.new("RGBA", img.size, (0, 0, 0, 0))
        _eyes(ImageDraw.Draw(eyes), s, mood, blink)
        if bob:
            eyes = eyes.transform(eyes.size, Image.Transform.AFFINE, (1, 0, 0, 0, 1, -bob * s))
        img.alpha_composite(eyes)
    out = img.resize((px, px), Image.Resampling.LANCZOS)
    if small:
        _pixel_eyes(out, mood, blink)
    return out


def _pixel_eyes(img, mood: str, blink: bool) -> None:
    """Eyes for a tiny Alti, set pixel by pixel: drawn big and scaled down they blur away."""
    px = img.load()
    n = img.size[0]
    xl, xr = int(9.7 / 24 * n), int(14.3 / 24 * n + 0.5)
    if xr - xl < 2:
        xr = xl + 2
    y = int(12.4 / 24 * n)
    ink = _INK
    closed = blink or mood in ("happy", "sleep")
    for x in (xl, xr):
        if closed:
            px[x, y] = ink
        elif mood == "sad":
            px[x, y + 1] = ink
        elif mood == "help":
            px[x, y - 1], px[x, y] = ink, ink
        else:
            px[x, y], px[x, min(n - 1, y + 1)] = ink, ink


def _rgb(p) -> str:
    return f"#{p[0]:02x}{p[1]:02x}{p[2]:02x}"


def _dark(p) -> bool:
    return p[0] * 0.3 + p[1] * 0.59 + p[2] * 0.11 < 70


def to_text(img, indent: int = 0, alpha_cut: int = 90) -> Text:
    """An RGBA image in half blocks; transparent pixels show the terminal's own background."""
    px = img.load()
    w, h = img.size
    out = Text()
    for y in range(0, h - (h % 2), 2):
        out.append(" " * indent)
        for x in range(w):
            top, bottom = px[x, y], px[x, y + 1]
            t_on, b_on = top[3] >= alpha_cut, bottom[3] >= alpha_cut
            if t_on and b_on and top[:3] == bottom[:3]:
                out.append("█", style=_rgb(top))
            elif t_on and b_on and _dark(bottom) and not _dark(top):
                # The dark pixel goes in the foreground: a very dark background colour can be
                # dropped on the way (prompt_toolkit's pickers lost Alti's eyes that way).
                out.append("▄", style=f"{_rgb(bottom)} on {_rgb(top)}")
            elif t_on and b_on:
                out.append("▀", style=f"{_rgb(top)} on {_rgb(bottom)}")
            elif t_on:
                out.append("▀", style=_rgb(top))
            elif b_on:
                out.append("▄", style=_rgb(bottom))
            else:
                out.append(" ")
        if y + 2 < h - (h % 2):
            out.append("\n")
    return out


def art(rows: int = 8, mood: str = "idle", satellites: bool = True, indent: int = 0, frame: int = 0) -> Text:
    """Alti as text, `rows` lines tall (a plain star when Pillow is missing)."""
    try:
        return to_text(sprite(rows, mood, satellites, False, frame), indent)
    except ImportError:
        return Text(" " * indent + theme.STAR, style=f"bold {theme.GOLD}")


def lines(rows: int = 8, mood: str = "idle", satellites: bool = True, frame: int = 0) -> list[Text]:
    return art(rows, mood, satellites, 0, frame).split("\n")


#: A hand-set 15×14 Alti for small places (a picker, an error): a vector star scaled down this far
#: turns into a blurred cross, a sprite stays crisp. Two pixel rows per text row → 7 rows tall.
_PIXEL = [
    "       o       ",
    "       y       ",
    "      lyo      ",
    "      lyo      ",
    "     llyyo     ",
    "   llwlyyyoo   ",
    "oyllllyyyyyyood",
    "dyyyyyyyyyyoood",
    "   yyyyyyyoo   ",
    "     yyyyo     ",
    "      yoo      ",
    "      yod      ",
    "       o       ",
    "       d       ",
]
_PALETTE = {
    "l": (255, 241, 196, 255),
    "w": (255, 252, 240, 255),
    "y": (255, 211, 122, 255),
    "o": (241, 169, 60, 255),
    "d": (214, 129, 30, 255),
}
#: Eye pixels per mood (column, row) on the sprite.
_PIXEL_EYES = {
    "idle": [(5, 6), (5, 7), (9, 6), (9, 7)],
    "think": [(5, 7), (9, 7)],
    "help": [(5, 5), (5, 6), (9, 5), (9, 6)],
    "sad": [(5, 7), (9, 7), (4, 5), (10, 5)],
    "happy": [(4, 7), (5, 6), (6, 7), (8, 7), (9, 6), (10, 7)],
    "sleep": [(4, 7), (5, 7), (9, 7), (10, 7)],
}


@lru_cache(maxsize=32)
def pixel_sprite(mood: str = "idle", blink: bool = False, sparkle: int = -1):
    """The hand-set small Alti. `sparkle`: a frame number to light a satellite pixel (-1: none)."""
    from PIL import Image

    img = Image.new("RGBA", (15, 14), (0, 0, 0, 0))
    px = img.load()
    for y, row in enumerate(_PIXEL):
        for x, ch in enumerate(row):
            if ch in _PALETTE:
                px[x, y] = _PALETTE[ch]
    eyes = _PIXEL_EYES["sleep" if blink else mood if mood in _PIXEL_EYES else "idle"]
    for x, y in eyes:
        px[x, y] = _INK
    if sparkle >= 0:
        spots = [(13, 1), (14, 3), (1, 12), (0, 10)]
        x, y = spots[sparkle % len(spots)]
        px[x, y] = (255, 236, 170, 255)
    return img


def small(mood: str = "idle", indent: int = 0, blink: bool = False, sparkle: int = -1) -> Text:
    """The small Alti as 7 lines of text (a plain star when Pillow is missing)."""
    try:
        return to_text(pixel_sprite(mood, blink, sparkle), indent)
    except ImportError:
        return Text(" " * indent + theme.STAR, style=f"bold {theme.GOLD}")
