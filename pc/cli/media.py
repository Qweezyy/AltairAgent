"""Pictures, widgets and files in the terminal.

- An image is drawn right in the conversation from half blocks (▀: the top pixel is the glyph,
  the bottom one its background), so it shows in any truecolor terminal, no special protocol.
- A widget (show_html) is saved as an .html file and offered as a link; /open opens it.
- Files go to the agent as attachments: '@path' mentions, paths dropped into the terminal (a
  drop pastes the path) and an image from the clipboard (Alt+V).
"""

from __future__ import annotations

import os
import re
import shlex
import sys
import time
import webbrowser
from pathlib import Path

from rich.text import Text

#: What a thumbnail may take: columns, and rows (each row holds two pixel rows).
THUMB_COLS = 56
THUMB_ROWS = 18

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def _hex(rgb: tuple[int, ...]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def thumbnail(path: str | Path, cols: int = THUMB_COLS, rows: int = THUMB_ROWS) -> Text | None:
    """The image as half-block text, or None (not an image, unreadable, Pillow missing)."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as src:
            img = src.convert("RGBA")
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    w, h = img.size
    if not w or not h:
        return None
    # A terminal cell is about twice as tall as wide: two pixel rows per text row.
    scale = min(cols / w, (rows * 2) / h, 1.0)
    tw, th = max(1, round(w * scale)), max(2, round(h * scale))
    th += th % 2
    small = img.resize((tw, th), Image.Resampling.LANCZOS)
    bg = Image.new("RGBA", small.size, (24, 24, 30, 255))
    small = Image.alpha_composite(bg, small).convert("RGB")
    px = small.load()
    out = Text()
    for y in range(0, th, 2):
        out.append("     ")
        for x in range(tw):
            top, bottom = px[x, y], px[x, y + 1]
            out.append("▀", style=f"{_hex(top)} on {_hex(bottom)}")
        if y + 2 < th:
            out.append("\n")
    return out


def file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def absolute(path: str, workspace: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else Path(workspace or ".") / p


def save_widget(html: str, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"widget-{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}.html"
    target.write_text(html, encoding="utf-8")
    return target


def open_path(path: Path) -> bool:
    """Opens a file in its usual app (a page in the browser)."""
    try:
        if path.suffix.lower() in (".html", ".htm"):
            return webbrowser.open(file_uri(path))
        if os.name == "nt":
            os.startfile(str(path))  # noqa: S606 - the user asked to open this file
            return True
        import subprocess

        subprocess.Popen(
            ["open" if sys.platform == "darwin" else "xdg-open", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except OSError:
        return False


def clipboard_files(folder: Path) -> list[str]:
    """What the clipboard holds as files: a copied image saved as a .png, or the files copied in
    the file manager. [] when there is neither (or Pillow cannot read the clipboard here)."""
    try:
        from PIL import ImageGrab
    except ImportError:
        return []
    try:
        grabbed = ImageGrab.grabclipboard()
    except (OSError, NotImplementedError, ValueError):
        return []
    if grabbed is None:
        return []
    if isinstance(grabbed, list):
        return [str(p) for p in grabbed if Path(str(p)).exists()]
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"paste-{time.strftime('%Y%m%d-%H%M%S')}.png"
    try:
        grabbed.save(target, "PNG")
    except (OSError, ValueError):
        return []
    return [str(target)]


#: '@path' (a space ends it; '@"path with spaces"' keeps them).
_MENTION = re.compile(r'(?<![\w@])@("([^"]+)"|[^\s"]+)')


def mentioned_files(text: str, workspace: str) -> list[str]:
    """Files and folders the message points at: '@' mentions, and absolute paths pasted in (a
    file dropped on the terminal arrives as its path, often quoted)."""
    found: list[str] = []

    def add(raw: str) -> None:
        raw = raw.strip().strip("'\"").rstrip(".,;:)")
        if not raw:
            return
        p = absolute(raw, workspace)
        try:
            if p.exists() and str(p) not in found:
                found.append(str(p))
        except OSError:
            return

    for m in _MENTION.finditer(text):
        add(m.group(2) or m.group(1))
    try:
        tokens = shlex.split(text, posix=os.name != "nt")
    except ValueError:
        tokens = text.split()
    for token in tokens:
        token = token.strip("'\"")
        if not token.startswith("@") and (os.path.isabs(token) or token.startswith("~")) and len(token) > 3:
            add(token)
    return found
