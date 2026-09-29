"""The user's profile: the name they go by and their avatar.

The name is also told to the agent (the system prompt), so it can address the user by it. Both
are plain files in the data folder: `profile.json` and `avatar.<ext>`.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from core.fs_atomic import atomic_write_text
from core.logging_setup import get_logger

logger = get_logger("profile")

MAX_NAME = 40
MAX_AVATAR_BYTES = 2 * 1024 * 1024
#: Image formats by their first bytes (only what a browser shows and nothing it could run).
_MAGIC = {b"\x89PNG\r\n\x1a\n": "png", b"\xff\xd8\xff": "jpg", b"RIFF": "webp"}


class ProfileStore:
    def __init__(self, data_dir: Path) -> None:
        self.dir = Path(data_dir)
        self.path = self.dir / "profile.json"

    def read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        avatar = self.avatar_path()
        return {"name": str(data.get("name") or ""),
                "avatar": avatar is not None,
                "avatar_v": int(avatar.stat().st_mtime) if avatar else 0}

    def set_name(self, name: str) -> str:
        clean = " ".join(str(name or "").split())[:MAX_NAME]
        self.dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps({"name": clean}, ensure_ascii=False))
        return clean

    def avatar_path(self) -> Path | None:
        for ext in ("png", "jpg", "webp"):
            path = self.dir / f"avatar.{ext}"
            if path.exists():
                return path
        return None

    def set_avatar(self, data_url: str) -> None:
        """A picture as a data URL (the window crops and shrinks it before sending)."""
        _, _, b64 = (data_url or "").partition(",")
        try:
            raw = base64.b64decode(b64, validate=True)
        except ValueError as exc:
            raise ValueError("not an image") from exc
        if len(raw) > MAX_AVATAR_BYTES:
            raise ValueError("the image is larger than 2 MB")
        ext = next((e for magic, e in _MAGIC.items() if raw.startswith(magic)), None)
        if ext is None or (ext == "webp" and raw[8:12] != b"WEBP"):
            raise ValueError("only PNG, JPEG or WebP images")
        self.clear_avatar()
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"avatar.{ext}").write_bytes(raw)

    def clear_avatar(self) -> None:
        for ext in ("png", "jpg", "webp"):
            (self.dir / f"avatar.{ext}").unlink(missing_ok=True)

    def prompt_section(self) -> str:
        name = self.read()["name"]
        return f"<user>\nThe user's name: {name}. Address them by it when it fits.\n</user>" if name else ""
