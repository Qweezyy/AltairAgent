"""Safe downloads for the built-in browser: quarantine first, check, then decide.

Nothing a page downloads goes straight into the user's folders. The file lands in a
quarantine folder owned by the app, gets checked, and only then may be moved:

  * Microsoft Defender scans it (MpCmdRun, custom scan of that one file);
  * the real type is sniffed from the first bytes — an executable that calls itself
    ".pdf" is a classic trick;
  * types that can run code (exe, msi, bat, ps1, js, scr, lnk…) are flagged risky.

A detected threat is deleted at once. A suspicious or risky file can leave quarantine
only with the user's explicit confirmation. A file that is moved on gets the Windows
"Mark of the Web" (Zone.Identifier), so SmartScreen and Office Protected View still
treat it as coming from the internet. Quarantine is cleared after a week.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.fs_atomic import safe_replace
from core.logging_setup import get_logger
from core.utils.proc import no_window_kwargs

logger = get_logger("browser_downloads")

#: Extensions that can execute code when opened on Windows.
RISKY_EXTENSIONS = frozenset({
    "exe", "msi", "msix", "msixbundle", "appx", "appxbundle", "bat", "cmd", "com", "cpl", "scr",
    "pif", "ps1", "psm1", "psd1", "vbs", "vbe", "js", "jse", "wsf", "wsh", "hta", "jar", "lnk",
    "reg", "dll", "sys", "iso", "img", "vhd", "vhdx", "application", "gadget", "msc", "inf",
    "chm", "sct", "url", "xll", "xlam", "docm", "xlsm", "pptm", "apk",
})

#: Magic bytes of executable formats, whatever the file claims to be.
_EXECUTABLE_MAGIC = (b"MZ", b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")

KEEP_DAYS = 7


@dataclass(slots=True)
class Download:
    id: str
    name: str
    url: str
    path: str
    tab: str = ""
    size: int = 0
    sha256: str = ""
    #: downloading | checking | clean | risky | suspicious | threat | unscanned | failed |
    #: moved | deleted
    status: str = "downloading"
    #: Why the status is what it is, for the user and the agent (English, short).
    detail: str = ""
    created: float = field(default_factory=time.time)
    moved_to: str = ""

    @property
    def needs_confirmation(self) -> bool:
        return self.status in ("risky", "suspicious", "unscanned")

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data["needs_confirmation"] = self.needs_confirmation
        return data


def _defender_exe() -> Path | None:
    if os.name != "nt":
        return None
    base = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Windows Defender" / "MpCmdRun.exe"
    return base if base.is_file() else None


def defender_scan(path: Path) -> tuple[str, str]:
    """Scans one file. Returns (verdict, detail): clean | threat | unavailable."""
    exe = _defender_exe()
    if exe is None:
        return "unavailable", "Microsoft Defender is not available on this PC"
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, our own file
            [str(exe), "-Scan", "-ScanType", "3", "-File", str(path), "-DisableRemediation"],
            capture_output=True, text=True, timeout=180, errors="replace", **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "unavailable", f"Defender scan failed to run: {exc}"
    out = (proc.stdout or "") + (proc.stderr or "")
    lowered = out.lower()
    if "found no threats" in lowered:
        return "clean", "Microsoft Defender: no threats"
    if "threat" in lowered and ("found" in lowered or "detected" in lowered):
        lines = [ln.strip() for ln in out.splitlines() if "threat" in ln.lower()]
        # "Threat : Virus:DOS/EICAR_Test_File" names it; "found 1 threats" only counts.
        named = next((ln for ln in lines if ln.lower().startswith("threat") and ":" in ln), "")
        line = named.split(":", 1)[1].strip() if named else (lines[0] if lines else "threat found")
        return "threat", f"Microsoft Defender: {line[:160]}"
    # Exit code 2 is ambiguous (failure or detection) — the text decides; unknown = not scanned.
    return "unavailable", f"Defender gave no verdict (exit {proc.returncode})"


def sniff_executable(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            head = handle.read(4)
    except OSError:
        return False
    return any(head.startswith(magic) for magic in _EXECUTABLE_MAGIC)


def mark_of_the_web(path: Path, url: str) -> None:
    """Writes the Zone.Identifier stream (ZoneId=3, internet) like browsers do."""
    if os.name != "nt":
        return
    try:
        with open(f"{path}:Zone.Identifier", "w", encoding="utf-8") as stream:
            stream.write(f"[ZoneTransfer]\r\nZoneId=3\r\nHostUrl={url or 'about:internet'}\r\n")
    except OSError as exc:  # FAT/exFAT drives have no alternate streams
        logger.info("Mark of the Web not written for %s: %s", path, exc)


def user_downloads_dir() -> Path:
    """The user's real Downloads folder (it can be moved to another drive)."""
    if os.name == "nt":
        try:
            import ctypes

            buf = ctypes.c_wchar_p()
            folder_id = (ctypes.c_ubyte * 16).from_buffer_copy(
                uuid.UUID("374DE290-123F-4565-9164-39C4925E467B").bytes_le  # FOLDERID_Downloads
            )
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(buf)) == 0:
                folder = Path(buf.value or "")
                ctypes.windll.ole32.CoTaskMemFree(buf)
                if folder.is_dir():
                    return folder
        except (OSError, AttributeError, ValueError):
            logger.debug("Known folder lookup failed", exc_info=True)
    return Path.home() / "Downloads"


def _unique(target: Path) -> Path:
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for n in range(1, 1000):
        candidate = target.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
    return target.with_name(f"{stem}-{uuid.uuid4().hex[:6]}{suffix}")


def safe_name(name: str) -> str:
    """A file name that cannot escape its folder or trip Windows reserved names."""
    cleaned = "".join("_" if c in '<>:"/\\|?*' or ord(c) < 32 else c for c in (name or "")).strip(" .")
    if cleaned.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                                          *(f"LPT{i}" for i in range(1, 10))}:
        cleaned = "_" + cleaned
    return cleaned[:180] or "download"


class DownloadStore:
    """Registry of the browser's downloads, persisted next to the quarantine folder."""

    def __init__(self, quarantine: Path) -> None:
        self.quarantine = quarantine
        self.quarantine.mkdir(parents=True, exist_ok=True)
        self._index = self.quarantine / "index.json"
        self._items: dict[str, Download] = self._load()
        self._listeners: list[Any] = []
        self._save_lock = asyncio.Lock()
        for leftover in self.quarantine.glob("index*.tmp"):  # a crash mid-save
            leftover.unlink(missing_ok=True)

    # --- persistence ------------------------------------------------------

    def _load(self) -> dict[str, Download]:
        try:
            raw = json.loads(self._index.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        items: dict[str, Download] = {}
        for entry in raw if isinstance(raw, list) else []:
            try:
                item = Download(**entry)
            except TypeError:
                continue
            items[item.id] = item
        return items

    def _save(self) -> None:
        data = [asdict(d) for d in self._items.values()]
        # A unique temp file per write: two saves in flight must not share one.
        tmp = self._index.with_name(f"index.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        safe_replace(tmp, self._index)

    def on_change(self, listener: Any) -> None:
        self._listeners.append(listener)

    def off_change(self, listener: Any) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    async def _changed(self) -> None:
        async with self._save_lock:
            await asyncio.to_thread(self._save)
        for listener in list(self._listeners):
            try:
                await listener(self.list())
            except Exception:  # noqa: BLE001 — a closed UI socket must not break downloads
                logger.debug("download listener failed", exc_info=True)

    # --- queries ------------------------------------------------------------

    def list(self) -> list[dict[str, Any]]:
        return [d.public() for d in sorted(self._items.values(), key=lambda d: d.created, reverse=True)]

    def get(self, item_id: str) -> Download:
        item = self._items.get(item_id)
        if item is None:
            raise KeyError(item_id)
        return item

    def new_path(self, suggested: str) -> Path:
        folder = self.quarantine / f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / safe_name(suggested)

    # --- lifecycle ------------------------------------------------------------

    async def started(self, url: str, path: Path, tab: str = "") -> Download:
        for item in self._items.values():  # the same file reported twice (browser + shell)
            if item.path == str(path):
                return item
        item = Download(id=uuid.uuid4().hex[:10], name=path.name, url=url, path=str(path), tab=tab)
        self._items[item.id] = item
        await self._changed()
        return item

    async def finished(self, path: Path, ok: bool, url: str = "", tab: str = "") -> Download:
        item = next((d for d in self._items.values() if d.path == str(path)), None)
        if item is None:
            item = await self.started(url, path, tab)
        if not ok or not await asyncio.to_thread(path.is_file):
            item.status, item.detail = "failed", "the download did not complete"
            await self._changed()
            return item
        item.status, item.detail = "checking", "checking the file"
        await self._changed()
        await self.check(item)
        return item

    async def check(self, item: Download) -> None:
        path = Path(item.path)

        def work() -> tuple[int, str, bool, tuple[str, str]]:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
            return path.stat().st_size, digest.hexdigest(), sniff_executable(path), defender_scan(path)

        try:
            size, sha, executable, (verdict, detail) = await asyncio.to_thread(work)
        except OSError as exc:
            item.status, item.detail = "failed", f"could not read the file: {exc}"
            await self._changed()
            return
        item.size, item.sha256 = size, sha
        ext = path.suffix.lower().lstrip(".")
        if verdict == "threat":
            await asyncio.to_thread(self._remove_file, path)
            item.status, item.detail = "threat", detail + " — the file was deleted"
        elif executable and ext not in RISKY_EXTENSIONS:
            item.status = "suspicious"
            item.detail = f"it is a program, but pretends to be .{ext or 'unknown'}"
        elif ext in RISKY_EXTENSIONS:
            item.status = "risky"
            item.detail = f".{ext} files can run code" + ("" if verdict == "clean" else f"; {detail}")
        elif verdict == "clean":
            item.status, item.detail = "clean", detail
        else:
            item.status, item.detail = "unscanned", detail
        await self._changed()

    # --- actions ------------------------------------------------------------

    async def move(self, item_id: str, destination: Path, *, confirmed: bool) -> Download:
        item = self.get(item_id)
        if item.status in ("threat", "deleted", "moved", "failed", "downloading", "checking"):
            raise ValueError(f"cannot move a download that is '{item.status}'")
        if item.needs_confirmation and not confirmed:
            raise PermissionError(f"'{item.name}' is {item.status} ({item.detail}); the user must confirm the move")
        source = Path(item.path)

        def work() -> Path:
            destination.mkdir(parents=True, exist_ok=True)
            target = _unique(destination / item.name)
            shutil.move(str(source), str(target))
            mark_of_the_web(target, item.url)
            self._remove_empty(source.parent)
            return target

        target = await asyncio.to_thread(work)
        item.status, item.moved_to, item.path = "moved", str(target), str(target)
        await self._changed()
        return item

    async def delete(self, item_id: str) -> Download:
        item = self.get(item_id)
        if item.status != "moved":  # moved files belong to the user now
            await asyncio.to_thread(self._remove_file, Path(item.path))
        item.status = "deleted" if item.status != "moved" else item.status
        await self._changed()
        return item

    async def forget_old(self, days: int = KEEP_DAYS) -> int:
        cutoff = time.time() - days * 86400
        old = [d for d in self._items.values() if d.created < cutoff]
        for item in old:
            if item.status not in ("moved",):
                await asyncio.to_thread(self._remove_file, Path(item.path))
            self._items.pop(item.id, None)
        if old:
            await self._changed()
        return len(old)

    def _remove_file(self, path: Path) -> None:
        try:
            if self.quarantine in path.parents:
                path.unlink(missing_ok=True)
                self._remove_empty(path.parent)
        except OSError:
            logger.warning("Could not delete quarantined file %s", path, exc_info=True)

    def _remove_empty(self, folder: Path) -> None:
        if folder != self.quarantine and self.quarantine in folder.parents:
            try:
                folder.rmdir()
            except OSError:
                logger.debug("quarantine folder not empty: %s", folder)
