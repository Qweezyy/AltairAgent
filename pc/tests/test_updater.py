"""Checking for and installing updates: GitHub releases signed with the project's key."""

from __future__ import annotations

import base64
import functools
import hashlib
import json
import subprocess
import sys
import time
import zipfile

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import core.updater as updater_module
from core.updater import Updater, build_script, parse_sums, script_command, verify_signature
from core.version import __version__, is_newer, version_tuple

# ------------------------------------------------------------- versions


@pytest.mark.parametrize(
    ("candidate", "current", "newer"),
    [
        ("1.5.0", "1.4.0", True),
        ("1.4.1", "1.4.0", True),
        ("2.0.0", "1.9.9", True),
        ("1.4.0", "1.4.0", False),
        ("1.3.9", "1.4.0", False),
        ("v1.5.0", "1.4.0", True),      # the v of git tags
        ("1.10.0", "1.9.0", True),      # numbers, not strings
    ],
)
def test_version_comparison(candidate, current, newer):
    assert is_newer(candidate, current) is newer


def test_broken_version_does_not_crash():
    assert version_tuple("not a version") == (0,)
    assert is_newer("abc", "1.0.0") is False


# ------------------------------------------------------------- a fake GitHub release


def _key() -> tuple[Ed25519PrivateKey, str]:
    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return key, base64.b64encode(pub).decode()


def _release(monkeypatch, *, version="99.0.0", package=b"PK-package", sign_with=None, signed=True,
             assets=None, frozen=True):
    """Serves a GitHub 'latest release' and its assets; returns the requested URLs."""
    name = f"Altair-{version}-windows-x64.zip"
    sums = f"{hashlib.sha256(package).hexdigest()} *{name}\n{'1' * 64} *Altair-{version}-android.apk\n".encode()
    key, pub = _key()
    monkeypatch.setattr(updater_module, "RELEASE_PUBLIC_KEY", pub)
    signer = sign_with or key
    files = {name: package, "SHA256SUMS.txt": sums}
    if signed:
        files["SHA256SUMS.txt.sig"] = base64.b64encode(signer.sign(sums))
    listed = assets if assets is not None else list(files)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(200, json={
                "tag_name": f"v{version}", "body": "What is new", "html_url": "https://github.com/x/y/releases/tag/v",
                "assets": [{"name": n, "browser_download_url": f"https://dl.example/{n}"} for n in listed],
            })
        fname = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, content=files[fname]) if fname in files else httpx.Response(404)

    monkeypatch.setattr(updater_module.httpx, "AsyncClient",
                        functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(updater_module, "_ASSET_SUFFIX", "-windows-x64.zip")
    monkeypatch.setattr(Updater, "is_frozen", property(lambda self: frozen))
    return seen


async def test_by_default_updates_come_from_the_signed_github_release(settings, monkeypatch):
    settings.update_url = ""                       # an old .env with an empty UPDATE_URL
    seen = _release(monkeypatch)
    info = await Updater(settings).check()
    assert "api.github.com/repos/Qweezyy/AltairAgent/releases/latest" in seen[0]
    assert info.available and info.version == "99.0.0" and info.signed and info.installable
    assert info.url.endswith("Altair-99.0.0-windows-x64.zip") and len(info.sha256) == 64
    assert info.notes == "What is new" and info.page

    path = await Updater(settings).download(info)
    assert path.read_bytes() == b"PK-package"


async def test_a_release_signed_with_another_key_is_not_installed(settings, monkeypatch):
    other, _ = _key()
    _release(monkeypatch, sign_with=other)
    info = await Updater(settings).check()
    assert info.available and not info.installable and not info.signed
    assert "signature" in info.error and info.page   # the user can still get it by hand


async def test_an_unsigned_release_is_not_installed(settings, monkeypatch):
    _release(monkeypatch, signed=False)
    info = await Updater(settings).check()
    assert info.available and not info.installable and "not signed" in info.error


async def test_a_package_that_does_not_match_the_signed_sums_is_refused(settings, monkeypatch):
    _release(monkeypatch)
    info = await Updater(settings).check()
    info.sha256 = "0" * 64                          # a replaced file
    with pytest.raises(ValueError, match="checksum"):
        await Updater(settings).download(info)


async def test_the_same_version_is_not_an_update(settings, monkeypatch):
    _release(monkeypatch, version=__version__)
    info = await Updater(settings).check()
    assert not info.available and not info.installable and not info.error


async def test_from_the_sources_it_is_shown_but_not_installed(settings, monkeypatch):
    _release(monkeypatch, frozen=False)
    info = await Updater(settings).check()
    assert info.available and not info.installable


async def test_checks_can_be_turned_off(settings):
    settings.update_url = "off"
    info = await Updater(settings).check()
    assert not info.available and "off" in info.error


def test_signature_and_sums_helpers():
    key, pub = _key()
    msg = b"hello"
    assert verify_signature(msg, base64.b64encode(key.sign(msg)).decode(), pub)
    assert not verify_signature(b"hellO", base64.b64encode(key.sign(msg)).decode(), pub)
    assert not verify_signature(msg, "not base64!", pub)
    assert parse_sums("ab" * 32 + " *a.zip\n" + "cd" * 32 + "  b.apk\njunk") == {"a.zip": "ab" * 32, "b.apk": "cd" * 32}


# ------------------------------------------------------------- a team's own manifest


async def test_manifest_from_a_local_file(settings, tmp_path):
    """Updates can be handed out through a shared folder, without the internet."""
    manifest = tmp_path / "update.json"
    manifest.write_text(json.dumps({"version": "99.0.0", "url": "pack.zip", "notes": "Lots new"}), encoding="utf-8")
    settings.update_url = str(manifest)
    info = await Updater(settings).check()
    assert info.available and info.version == "99.0.0" and info.notes == "Lots new"
    assert not info.installable, "running from the sources"


async def test_missing_manifest_is_reported(settings, tmp_path):
    settings.update_url = str(tmp_path / "missing.json")
    info = await Updater(settings).check()
    assert not info.available and info.error


async def test_manifest_without_version_is_rejected(settings, tmp_path):
    manifest = tmp_path / "update.json"
    manifest.write_text(json.dumps({"url": "x.zip"}), encoding="utf-8")
    settings.update_url = str(manifest)
    info = await Updater(settings).check()
    assert not info.available and "version" in info.error


# ------------------------------------------------------------- unpacking and the swap


def test_unpack_rejects_paths_outside_the_archive(settings, tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as bundle:
        bundle.writestr("../../grab.txt", "data")
    with pytest.raises(ValueError, match="unsafe"):
        Updater(settings).unpack(evil)


def test_unpack_returns_the_app_folder_and_checks_it_is_the_app(settings, tmp_path):
    package = tmp_path / "pack.zip"
    with zipfile.ZipFile(package, "w") as bundle:
        bundle.writestr("LocalAIAgent/Altair.exe", "x")
        bundle.writestr("LocalAIAgent/_internal/lib.py", "y")
    folder = Updater(settings).unpack(package)
    assert folder.name == "LocalAIAgent" and (folder / "Altair.exe").exists()

    junk = tmp_path / "junk.zip"
    with zipfile.ZipFile(junk, "w") as bundle:
        bundle.writestr("readme.txt", "not an app")
    with pytest.raises(ValueError, match="does not look like the app"):
        Updater(settings).unpack(junk)


def test_apply_refuses_outside_the_built_app(settings, tmp_path):
    with pytest.raises(RuntimeError, match="built app"):
        Updater(settings).apply(tmp_path)


@pytest.mark.skipif(sys.platform != "win32", reason="the swap script is for Windows")
def test_the_swap_script_waits_for_the_app_then_replaces_it(tmp_path):
    install, new = tmp_path / "Altair", tmp_path / "new"
    (install / "_internal").mkdir(parents=True)
    (install / "_internal" / "stale_module.py").write_text("old")
    (install / "_internal" / "core.py").write_text("old")
    (install / "Altair.exe").write_text("old shell")
    (install / "my-notes.txt").write_text("the user's own file")
    (new / "_internal").mkdir(parents=True)
    (new / "_internal" / "core.py").write_text("new")
    (new / "Altair.exe").write_text("new shell")
    marker = tmp_path / "started.txt"
    starter = tmp_path / "start.cmd"
    starter.write_text(f'@echo started> "{marker}"\r\n', encoding="utf-8")

    # A process of "the app" that is still running: the swap must wait for it.
    app = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"])
    script = tmp_path / "swap.ps1"
    log = tmp_path / "swap.log"
    script.write_text(build_script(new, install, starter, [app.pid, 999_999], log), encoding="utf-8-sig")
    runner = subprocess.Popen(script_command(script), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    time.sleep(1.5)
    assert (install / "Altair.exe").read_text() == "old shell"   # not while the app runs
    app.wait()
    assert runner.wait(timeout=30) == 0
    for _ in range(50):
        if marker.exists():
            break
        time.sleep(0.1)

    assert (install / "Altair.exe").read_text() == "new shell"
    assert (install / "_internal" / "core.py").read_text() == "new"
    assert not (install / "_internal" / "stale_module.py").exists()   # _internal is mirrored
    assert (install / "my-notes.txt").read_text() == "the user's own file"  # the rest is kept
    assert marker.exists(), log.read_text(encoding="utf-8", errors="replace")
    assert not new.exists()
