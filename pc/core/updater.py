"""Checking for and installing updates.

The source (`UPDATE_URL` in `.env`) is, by default, the project's GitHub releases:

  * `github:owner/repo` (the default when empty) — the latest release: its version is the
    tag, the package is the release's Windows zip, and `SHA256SUMS.txt` with its signature
    `SHA256SUMS.txt.sig` vouch for it. The signature is an Ed25519 signature made with the
    project's release key (kept outside the repository, see release_sign.py); the public key
    is below. An unsigned or wrongly signed release is shown, but not installed by itself;
  * a link to a JSON manifest, or a path to one on disk / a network share (updates handed
    out inside a team):

        {"version": "1.5.0", "url": "https://…/Altair-1.5.0.zip", "notes": "…", "sha256": "…"}

  * `off` — never check.

Why the update is not unpacked over itself: the running app holds its files open and
Windows would not let them be replaced. The new version is unpacked beside it, and a short
script waits for the app (the window and the backend) to exit, copies it in and starts it.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx

from core.logging_setup import get_logger
from core.settings import Settings, get_settings
from core.version import __version__, is_newer

logger = get_logger("updater")

#: No bigger than this: the app weighs a hundred-odd megabytes, not gigabytes.
MAX_PACKAGE_BYTES = 500 * 1024 * 1024

#: Where updates come from when UPDATE_URL is empty.
DEFAULT_SOURCE = "github:Qweezyy/AltairAgent"

#: The release key's public half (Ed25519, raw 32 bytes, base64). Releases sign their
#: SHA256SUMS.txt with the private half (release_sign.py).
RELEASE_PUBLIC_KEY = "9B4wuoow2fqWhMsQ0v+OgDTYcdr4Sq8NI5xM8bRQpcs="

#: The Tauri shell's process (main.py sets it from --parent-pid): the update waits for the
#: window to close too, since the window's exe is replaced as well.
SHELL_PID: int | None = None

#: The release asset for this platform.
_ASSET_SUFFIX = {"win32": "-windows-x64.zip"}.get(sys.platform, "")


@dataclass(slots=True)
class UpdateInfo:
    available: bool = False
    current: str = __version__
    version: str = ""
    url: str = ""
    notes: str = ""
    sha256: str = ""
    error: str = ""
    #: Whether it can be installed by itself (a built app, a verified package).
    installable: bool = False
    #: Where to get it by hand (the release page).
    page: str = ""
    #: The release's checksums are signed with the project's key and the signature holds.
    signed: bool = False

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "current": self.current,
            "version": self.version,
            "notes": self.notes,
            "error": self.error,
            "installable": self.installable,
            "page": self.page,
            "signed": self.signed,
        }


def verify_signature(message: bytes, signature_b64: str, public_key_b64: str | None = None) -> bool:
    """Whether `signature_b64` is the release key's Ed25519 signature of `message`."""
    public_key_b64 = public_key_b64 or RELEASE_PUBLIC_KEY
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:  # pragma: no cover - bundled with the app; without it nothing is trusted
        logger.warning("cryptography is not available: update signatures cannot be checked")
        return False
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        key.verify(base64.b64decode(signature_b64.strip()), message)
        return True
    except (InvalidSignature, ValueError):
        return False


def parse_sums(text: str) -> dict[str, str]:
    """`sha256sum` output (`<hash> *<name>` or `<hash>  <name>`) → {name: hash}."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and len(parts[0]) == 64:
            sums[parts[1].lstrip("*").strip()] = parts[0].lower()
    return sums


@dataclass(slots=True)
class Updater:
    settings: Settings = field(default_factory=get_settings)

    @property
    def is_frozen(self) -> bool:
        """Running as the built app, not from the sources."""
        return bool(getattr(sys, "frozen", False))

    @property
    def install_dir(self) -> Path:
        if self.is_frozen:
            return Path(sys.executable).parent
        return self.settings.app_dir

    # ------------------------------------------------------------ checking

    async def check(self) -> UpdateInfo:
        source = self.settings.update_url.strip() or DEFAULT_SOURCE
        if source.lower() in ("off", "none", "no", "false", "0"):
            return UpdateInfo(error="Update checks are off (UPDATE_URL=off).")
        try:
            if source.startswith("github:"):
                info = await self._from_github(source.split(":", 1)[1].strip().strip("/"))
            else:
                info = self._from_manifest(await self._load_manifest(source))
        except Exception as exc:  # noqa: BLE001 - the reason is shown to the user
            logger.warning("update check failed: %s", exc)
            return UpdateInfo(error=f"Could not check for updates: {exc}")
        if not info.version and not info.error:
            info.error = "The update source gives no version."
        info.available = bool(info.version) and is_newer(info.version)
        # Only the built app installs itself: from the sources, update with git.
        info.installable = bool(info.available and info.url and self.is_frozen and not info.error)
        return info

    def _from_manifest(self, manifest: dict) -> UpdateInfo:
        return UpdateInfo(
            version=str(manifest.get("version") or "").strip(),
            url=str(manifest.get("url") or "").strip(),
            notes=str(manifest.get("notes") or "").strip(),
            sha256=str(manifest.get("sha256") or "").strip().lower(),
        )

    async def _from_github(self, repo: str) -> UpdateInfo:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": f"Altair/{__version__}"}
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
            response = await client.get(f"https://api.github.com/repos/{repo}/releases/latest")
            response.raise_for_status()
            release = response.json()
            assets = {a.get("name", ""): a.get("browser_download_url", "") for a in release.get("assets") or []}
            info = UpdateInfo(
                version=str(release.get("tag_name") or "").lstrip("vV").strip(),
                notes=str(release.get("body") or "").strip(),
                page=str(release.get("html_url") or f"https://github.com/{repo}/releases/latest"),
            )
            if not is_newer(info.version or "0"):
                return info
            package = next((n for n in assets if _ASSET_SUFFIX and n.endswith(_ASSET_SUFFIX)), "")
            if not package:
                info.error = "This release has no package for this system: download it from the release page."
                return info
            info.url = assets[package]
            sums_url, sig_url = assets.get("SHA256SUMS.txt"), assets.get("SHA256SUMS.txt.sig")
            if not sums_url or not sig_url:
                info.error = "This release is not signed: install it from the release page."
                return info
            sums = (await client.get(sums_url)).raise_for_status().content
            signature = (await client.get(sig_url)).raise_for_status().text
        if not verify_signature(sums, signature):
            info.error = "The release's signature does not match the app's key: not installing it."
            logger.warning("update %s: bad signature", info.version)
            return info
        info.signed = True
        info.sha256 = parse_sums(sums.decode("utf-8", "replace")).get(package, "")
        if not info.sha256:
            info.error = "The signed checksums do not list the package."
        return info

    async def _load_manifest(self, source: str) -> dict:
        if urlparse(source).scheme in ("http", "https"):
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                response = await client.get(source)
                response.raise_for_status()
                return response.json()

        def read_local() -> dict:
            path = Path(source).expanduser()
            if not path.exists():
                raise FileNotFoundError(f"manifest not found: {path}")
            return json.loads(path.read_text(encoding="utf-8"))

        # A network share may take seconds to answer: off the event loop.
        return await asyncio.to_thread(read_local)

    # ------------------------------------------------------------ installing

    async def download(self, info: UpdateInfo) -> Path:
        """Downloads the package into a temporary folder and checks its checksum."""
        target = Path(tempfile.gettempdir()) / f"Altair-{info.version}-update.zip"

        if urlparse(info.url).scheme in ("http", "https"):
            handle = await asyncio.to_thread(open, target, "wb")
            try:
                async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
                    async with client.stream("GET", info.url) as response:
                        response.raise_for_status()
                        total = 0
                        async for chunk in response.aiter_bytes(65536):
                            total += len(chunk)
                            if total > MAX_PACKAGE_BYTES:
                                raise ValueError("the update package is suspiciously large")
                            await asyncio.to_thread(handle.write, chunk)
            finally:
                await asyncio.to_thread(handle.close)
        else:
            # Copying from a network share can take minutes: off the event loop.
            await asyncio.to_thread(_copy_local, info.url, target)

        if info.sha256:
            digest = await asyncio.to_thread(_sha256, target)
            if digest != info.sha256:
                target.unlink(missing_ok=True)
                raise ValueError("the checksum does not match: the file is damaged or was replaced")
        elif info.signed:
            raise ValueError("the signed release gives no checksum for the package")

        return target

    def unpack(self, archive: Path) -> Path:
        """Unpacks the package and returns the folder of the new version."""
        staging = Path(tempfile.gettempdir()) / f"{archive.stem}-unpacked"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)

        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.namelist():
                # Archives with paths like ../../windows/system32 are refused.
                destination = (staging / member).resolve()
                if not str(destination).startswith(str(staging.resolve())):
                    raise ValueError(f"the archive holds an unsafe path: {member}")
            bundle.extractall(staging)

        # One folder inside: that is the app's root.
        entries = list(staging.iterdir())
        root = entries[0] if len(entries) == 1 and entries[0].is_dir() else staging
        exe = "Altair.exe" if sys.platform == "win32" else "Altair"
        if not (root / exe).exists() and not (root / "LocalAIAgent.exe").exists():
            raise ValueError("the package does not look like the app (no Altair.exe inside)")
        return root

    def apply(self, new_version_dir: Path) -> Path:
        """Hands the swap to a script that waits for the app to exit, copies the new version
        in and starts it. The caller then closes the app (the window closes the backend)."""
        if not self.is_frozen:
            raise RuntimeError("Only the built app updates itself: from the sources, use git pull.")
        if sys.platform != "win32":
            raise RuntimeError("Self-update is only for Windows so far: download the new version.")

        install = self.install_dir
        shell = install / "Altair.exe"
        start = shell if shell.exists() else Path(sys.executable)
        pids = [os.getpid()] + ([SHELL_PID] if SHELL_PID else [])
        script = Path(tempfile.gettempdir()) / "altair_update.ps1"
        log = Path(tempfile.gettempdir()) / "altair_update.log"
        # With a BOM: Windows PowerShell 5.1 reads a script without one in the ANSI code page.
        script.write_text(build_script(new_version_dir, install, start, pids, log), encoding="utf-8-sig")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(  # noqa: S603 - our own script in the temp folder
            script_command(script), creationflags=flags, close_fds=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        logger.info("update prepared: %s → %s; the app restarts when it closes", new_version_dir, install)
        return script


def script_command(script: Path) -> list[str]:
    return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden", "-File", str(script)]


def _ps(text: str | Path) -> str:
    """A PowerShell single-quoted literal."""
    return "'" + str(text).replace("'", "''") + "'"


def build_script(source: Path, install: Path, start: Path, pids: list[int], log: Path) -> str:
    """The swap script (PowerShell: waiting for processes and starting one work the same with
    or without a console, unlike `timeout` and `start` in a batch file). It waits up to 10
    minutes for every process of the app to exit — the backend and the window both hold
    files — then copies the new version in: `_internal` mirrored (no stale modules left), the
    rest of the folder only added to and updated, since the user may keep other files there.
    Then it starts the app and cleans up. If the app never closes, nothing is touched."""
    pid_list = ", ".join(str(int(pid)) for pid in pids) or "0"
    return f"""$ErrorActionPreference = 'Continue'
$log = {_ps(log)}
$src = {_ps(source)}
$dst = {_ps(install)}
"Altair update started $(Get-Date -Format o)" | Out-File -FilePath $log -Encoding utf8
$deadline = (Get-Date).AddMinutes(10)
foreach ($id in @({pid_list})) {{
    while (Get-Process -Id $id -ErrorAction SilentlyContinue) {{
        if ((Get-Date) -gt $deadline) {{
            "the app did not close: the update is not applied" | Out-File -FilePath $log -Append -Encoding utf8
            exit 2
        }}
        Start-Sleep -Milliseconds 300
    }}
}}
robocopy (Join-Path $src '_internal') (Join-Path $dst '_internal') /MIR /R:5 /W:1 /NP /NJH /NJS | Out-File -FilePath $log -Append -Encoding utf8
$ok = $LASTEXITCODE -lt 8
if ($ok) {{
    robocopy $src $dst /E /XD (Join-Path $src '_internal') /R:5 /W:1 /NP /NJH /NJS | Out-File -FilePath $log -Append -Encoding utf8
    $ok = $LASTEXITCODE -lt 8
}}
if ($ok) {{ "done" | Out-File -FilePath $log -Append -Encoding utf8 }} else {{ "copy failed" | Out-File -FilePath $log -Append -Encoding utf8 }}
Start-Process -FilePath {_ps(start)} -WorkingDirectory $dst
if ($ok) {{ Remove-Item -LiteralPath $src -Recurse -Force -ErrorAction SilentlyContinue; exit 0 }}
exit 1
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy_local(source: str, target: Path) -> None:
    """Copies the package from a local or network folder."""
    shutil.copy2(Path(source).expanduser(), target)
