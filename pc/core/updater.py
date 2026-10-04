"""Checking for and installing updates.

The source (`UPDATE_URL` in `.env`) is, by default, the project's GitHub releases:

  * `github:owner/repo` (the default when empty) — the latest release: its version is the
    tag, the package is the release's zip for this system (`…-windows-x64.zip`,
    `…-linux-x64.zip`, `…-macos-arm64.zip`), and `SHA256SUMS.txt` with its signature
    `SHA256SUMS.txt.sig` vouch for it. The signature is an Ed25519 signature made with the
    project's release key (kept outside the repository, see release_sign.py); the public key
    is below. An unsigned or wrongly signed release is shown, but not installed by itself;
  * a link to a JSON manifest, or a path to one on disk / a network share (updates handed
    out inside a team):

        {"version": "1.5.0", "url": "https://…/Altair-1.5.0.zip", "notes": "…", "sha256": "…"}

  * `off` — never check.

Why the update is not unpacked over itself: the running app holds its files open and
Windows would not let them be replaced. The new version is unpacked beside it, and a short
script (PowerShell on Windows, sh elsewhere) waits for the app — the window and the backend —
to exit, copies it in and starts it.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable
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

#: A download that sends nothing for this long is taken as stalled: a new connection
#: continues it from where it stopped (seen: one connection froze at 16 of 148 MB while a
#: fresh one ran at 9 MB/s).
STALL_SECONDS = 20.0
#: How many times a stalled or dropped download is continued before giving up.
MAX_RESUMES = 8

#: Called with (stage, done_bytes, total_bytes, resumes) while an update is fetched.
Progress = Callable[[str, int, int, int], None]

#: Where updates come from when UPDATE_URL is empty.
DEFAULT_SOURCE = "github:Qweezyy/AltairAgent"

#: The release key's public half (Ed25519, raw 32 bytes, base64). Releases sign their
#: SHA256SUMS.txt with the private half (release_sign.py).
RELEASE_PUBLIC_KEY = "9B4wuoow2fqWhMsQ0v+OgDTYcdr4Sq8NI5xM8bRQpcs="

#: The Tauri shell's process (main.py sets it from --parent-pid): the update waits for the
#: window to close too, since the window's exe is replaced as well.
SHELL_PID: int | None = None

def asset_suffix(system: str = sys.platform, machine: str | None = None) -> str:
    """The end of this system's package name in a release, as the build workflow names them."""
    name = {"win32": "windows", "linux": "linux", "darwin": "macos"}.get(system)
    if not name:
        return ""
    machine = (machine if machine is not None else platform.machine()).lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    return f"-{name}-{arch}.zip"


#: The release asset for this platform.
_ASSET_SUFFIX = asset_suffix()


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

    async def download(self, info: UpdateInfo, on_progress: Progress | None = None) -> Path:
        """Downloads the package into a temporary folder and checks its checksum.

        A connection that stalls or drops is continued from where it stopped (HTTP Range), so a
        frozen connection costs seconds instead of a download that never ends."""
        target = Path(tempfile.gettempdir()) / f"Altair-{info.version}-update.zip"
        report = on_progress or (lambda *_: None)

        if urlparse(info.url).scheme in ("http", "https"):
            await self._fetch(info.url, target, report)
        else:
            # Copying from a network share can take minutes: off the event loop.
            report("downloading", 0, 0, 0)
            await asyncio.to_thread(_copy_local, info.url, target)

        report("verifying", 0, 0, 0)
        if info.sha256:
            digest = await asyncio.to_thread(_sha256, target)
            if digest != info.sha256:
                target.unlink(missing_ok=True)
                raise ValueError("the checksum does not match: the file is damaged or was replaced")
        elif info.signed:
            raise ValueError("the signed release gives no checksum for the package")

        return target

    async def _fetch(self, url: str, target: Path, report: Progress) -> None:
        done, total, resumes = 0, 0, 0
        timeout = httpx.Timeout(connect=20.0, read=STALL_SECONDS, write=20.0, pool=20.0)
        handle = await asyncio.to_thread(open, target, "wb")
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                while True:
                    headers = {"Range": f"bytes={done}-"} if done else {}
                    try:
                        async with client.stream("GET", url, headers=headers) as response:
                            response.raise_for_status()
                            if done and response.status_code != 206:
                                # The server ignored the range: start over from the first byte.
                                done = 0
                                await asyncio.to_thread(handle.seek, 0)
                                await asyncio.to_thread(handle.truncate)
                            length = int(response.headers.get("content-length") or 0)
                            total = done + length if length else total
                            if total > MAX_PACKAGE_BYTES:
                                raise ValueError("the update package is suspiciously large")
                            report("downloading", done, total, resumes)
                            async for chunk in response.aiter_bytes(256 * 1024):
                                done += len(chunk)
                                if done > MAX_PACKAGE_BYTES:
                                    raise ValueError("the update package is suspiciously large")
                                await asyncio.to_thread(handle.write, chunk)
                                report("downloading", done, total, resumes)
                        if not total or done >= total:
                            return
                        raise httpx.RemoteProtocolError("the connection closed before the end")
                    except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                        resumes += 1
                        if resumes > MAX_RESUMES:
                            raise ValueError(f"the download kept stalling ({exc.__class__.__name__}); "
                                             "check the connection and try again") from exc
                        logger.warning("update download stalled at %d of %d bytes (%s): continuing",
                                       done, total, exc.__class__.__name__)
                        report("downloading", done, total, resumes)
                        await asyncio.sleep(min(2 * resumes, 10))
        finally:
            await asyncio.to_thread(handle.close)

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
            if os.name != "nt":
                # zipfile drops the Unix mode: without it the app and its libraries can't run.
                for member in bundle.infolist():
                    mode = (member.external_attr >> 16) & 0o7777
                    if mode and not member.is_dir():
                        os.chmod(staging / member.filename, mode)

        # One folder inside: that is the app's root.
        entries = list(staging.iterdir())
        root = entries[0] if len(entries) == 1 and entries[0].is_dir() else staging
        names = ("Altair.exe", "LocalAIAgent.exe") if sys.platform == "win32" else ("Altair", "LocalAIAgent")
        if not any((root / name).exists() for name in names):
            raise ValueError(f"the package does not look like the app (no {names[0]} inside)")
        return root

    def apply(self, new_version_dir: Path) -> Path:
        """Hands the swap to a script that waits for the app to exit, copies the new version
        in and starts it. The caller then closes the app (the window closes the backend)."""
        if not self.is_frozen:
            raise RuntimeError("Only the built app updates itself: from the sources, use git pull.")
        install = self.install_dir
        shell = install / ("Altair.exe" if sys.platform == "win32" else "Altair")
        start = shell if shell.exists() else Path(sys.executable)
        pids = [os.getpid()] + ([SHELL_PID] if SHELL_PID else [])
        log = Path(tempfile.gettempdir()) / "altair_update.log"
        if sys.platform == "win32":
            script = Path(tempfile.gettempdir()) / "altair_update.ps1"
            # With a BOM: Windows PowerShell 5.1 reads a script without one in the ANSI code page.
            script.write_text(build_script(new_version_dir, install, start, pids, log), encoding="utf-8-sig")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen(  # noqa: S603 - our own script in the temp folder
                script_command(script), creationflags=flags, close_fds=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            script = Path(tempfile.gettempdir()) / "altair_update.sh"
            script.write_text(build_sh_script(new_version_dir, install, start, pids, log), encoding="utf-8")
            # Its own session: the script must outlive the app it is waiting for.
            subprocess.Popen(  # noqa: S603 - our own script in the temp folder
                ["/bin/sh", str(script)], start_new_session=True, close_fds=True,
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
    Every file of the package is copied, whatever robocopy thinks of it: by default it skips a
    file with the same size and time, and the robocopy of Windows 10 1809+ / 11 calls one whose
    NTFS change time differs "modified" and skips that too — /IS /IT /IM include both. A skipped
    file would leave the app half old, half new. A robocopy too old for /IM rejects the switch
    (exit 16): then the copy runs again without it. Then the script starts the app and cleans
    up. If the app never closes, nothing is touched."""
    # Nothing to wait for is @(): a stand-in 0 would be the Idle process, always "running".
    pid_list = ", ".join(str(int(pid)) for pid in pids)
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
function Copy-All([string[]]$what) {{
    robocopy @what /IS /IT /IM /R:5 /W:1 /NP /NJH /NJS | Out-File -FilePath $log -Append -Encoding utf8
    if ($LASTEXITCODE -eq 16) {{
        robocopy @what /IS /IT /R:5 /W:1 /NP /NJH /NJS | Out-File -FilePath $log -Append -Encoding utf8
    }}
    return $LASTEXITCODE -lt 8
}}
$ok = Copy-All @((Join-Path $src '_internal'), (Join-Path $dst '_internal'), '/MIR')
if ($ok) {{
    $ok = Copy-All @($src, $dst, '/E', '/XD', (Join-Path $src '_internal'))
}}
if ($ok) {{ "done" | Out-File -FilePath $log -Append -Encoding utf8 }} else {{ "copy failed" | Out-File -FilePath $log -Append -Encoding utf8 }}
Start-Process -FilePath {_ps(start)} -WorkingDirectory $dst
if ($ok) {{ Remove-Item -LiteralPath $src -Recurse -Force -ErrorAction SilentlyContinue; exit 0 }}
exit 1
"""


def _sh(text: str | Path) -> str:
    """A POSIX shell single-quoted literal."""
    return "'" + str(text).replace("'", "'\\''") + "'"


def build_sh_script(source: Path, install: Path, start: Path, pids: list[int], log: Path) -> str:
    """The swap script for Linux and macOS, doing what the PowerShell one does: wait up to 10
    minutes for the app's processes to exit, then mirror `_internal` (the old one is set aside
    and put back if the copy fails), add and update the rest of the folder, start the app."""
    pid_list = " ".join(str(int(pid)) for pid in pids)
    return f"""#!/bin/sh
log={_sh(log)}
src={_sh(source)}
dst={_sh(install)}
echo "Altair update started $(date)" > "$log"
deadline=$(( $(date +%s) + 600 ))
for id in {pid_list}; do
  while kill -0 "$id" 2>/dev/null; do
    if [ "$(date +%s)" -gt "$deadline" ]; then
      echo "the app did not close: the update is not applied" >> "$log"
      exit 2
    fi
    sleep 0.3
  done
done
rm -rf "$dst/_internal.old"
if [ -d "$dst/_internal" ]; then mv "$dst/_internal" "$dst/_internal.old"; fi
if cp -R "$src/." "$dst/" >> "$log" 2>&1; then
  ok=1; rm -rf "$dst/_internal.old"; echo "done" >> "$log"
else
  ok=0; echo "copy failed" >> "$log"
  if [ -d "$dst/_internal.old" ]; then rm -rf "$dst/_internal"; mv "$dst/_internal.old" "$dst/_internal"; fi
fi
cd "$dst" && nohup {_sh(start)} > /dev/null 2>&1 &
if [ "$ok" = 1 ]; then rm -rf "$src"; exit 0; fi
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
