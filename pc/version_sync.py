"""Stamp the product version from <repo>/VERSION into the files that cannot read it themselves.

VERSION is the single source of truth. Python reads it at runtime (core/version.py), the
Android build reads it in Gradle and tauri.conf.json points at desktop/package.json. What is
left — package.json, the shell's Cargo.toml and pyproject.toml, plus the lock files that repeat
the version (Cargo.lock for the shell package, package-lock.json) — are static formats with no
way to include another file, so the build rewrites them from VERSION (build_app.py calls this
first) and tests/test_version_sync.py fails if any of them drifts.

Run by hand after bumping VERSION:  python version_sync.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PC = Path(__file__).resolve().parent
REPO = PC.parent
VERSION_FILE = REPO / "VERSION"
PACKAGE_JSON = PC / "desktop" / "package.json"
CARGO_TOML = PC / "desktop" / "src-tauri" / "Cargo.toml"
PYPROJECT = PC / "pyproject.toml"
CARGO_LOCK = PC / "desktop" / "src-tauri" / "Cargo.lock"
PACKAGE_LOCK = PC / "desktop" / "package-lock.json"

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
# The `version = "…"` line of the first table ([package] / [project]) — not dependency versions.
_TOML_VERSION = re.compile(r'(\A.*?^\[(?:package|project)\][^\[]*?^version\s*=\s*")([^"]*)(")', re.S | re.M)


_TOML_NAME = re.compile(r'^\[package\][^\[]*?^name\s*=\s*"([^"]+)"', re.S | re.M)


def _cargo_lock_entry(package: str) -> re.Pattern[str]:
    """The shell's own entry in Cargo.lock (dependencies keep their versions)."""
    return re.compile(r'(^\[\[package\]\]\nname = "' + re.escape(package) + r'"\nversion = ")([^"]*)(")', re.M)


def _shell_package() -> str:
    match = _TOML_NAME.search(CARGO_TOML.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"no package name in {CARGO_TOML}")
    return match.group(1)


def read_version() -> str:
    value = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not _SEMVER.match(value):
        raise ValueError(f"VERSION must be MAJOR.MINOR.PATCH, got {value!r}")
    return value


def _stamp_toml(path: Path, version: str) -> bool:
    text = path.read_text(encoding="utf-8")
    match = _TOML_VERSION.search(text)
    if match is None:
        raise ValueError(f"no package version line in {path}")
    if match.group(2) == version:
        return False
    path.write_text(text[: match.start(2)] + version + text[match.end(2) :], encoding="utf-8", newline="\n")
    return True


def _stamp_package_json(path: Path, version: str) -> bool:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") == version:
        return False
    data["version"] = version
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return True


def _stamp_cargo_lock(path: Path, version: str) -> bool:
    text = path.read_text(encoding="utf-8")
    match = _cargo_lock_entry(_shell_package()).search(text)
    if match is None:
        raise ValueError(f"no entry for the shell package in {path}")
    if match.group(2) == version:
        return False
    path.write_text(text[: match.start(2)] + version + text[match.end(2) :], encoding="utf-8", newline="\n")
    return True


def _stamp_package_lock(path: Path, version: str) -> bool:
    data = json.loads(path.read_text(encoding="utf-8"))
    root = data.get("packages", {}).get("", {})
    if data.get("version") == version and root.get("version", version) == version:
        return False
    data["version"] = version
    if root:
        root["version"] = version
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return True


def stamped_versions() -> dict[str, str]:
    """What each derived file says now (for the drift test)."""
    out = {str(PACKAGE_JSON.relative_to(REPO)): json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["version"]}
    for path in (CARGO_TOML, PYPROJECT):
        match = _TOML_VERSION.search(path.read_text(encoding="utf-8"))
        out[str(path.relative_to(REPO))] = match.group(2) if match else ""
    lock = _cargo_lock_entry(_shell_package()).search(CARGO_LOCK.read_text(encoding="utf-8"))
    out[str(CARGO_LOCK.relative_to(REPO))] = lock.group(2) if lock else ""
    npm = json.loads(PACKAGE_LOCK.read_text(encoding="utf-8"))
    out[str(PACKAGE_LOCK.relative_to(REPO))] = npm.get("version", "")
    out[str(PACKAGE_LOCK.relative_to(REPO)) + ' packages[""]'] = npm.get("packages", {}).get("", {}).get("version", "")
    return out


def sync() -> list[str]:
    """Write VERSION into the derived files; returns the ones that changed."""
    version = read_version()
    changed = []
    if _stamp_package_json(PACKAGE_JSON, version):
        changed.append(PACKAGE_JSON.name)
    for path in (CARGO_TOML, PYPROJECT):
        if _stamp_toml(path, version):
            changed.append(str(path.relative_to(PC)))
    if _stamp_cargo_lock(CARGO_LOCK, version):
        changed.append(CARGO_LOCK.name)
    if _stamp_package_lock(PACKAGE_LOCK, version):
        changed.append(PACKAGE_LOCK.name)
    return changed


if __name__ == "__main__":
    updated = sync()
    print(f"version {read_version()}: " + (", ".join(updated) if updated else "everything already in sync"))
    sys.exit(0)
