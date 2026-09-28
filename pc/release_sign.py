"""Signs a release for self-update: writes SHA256SUMS.txt for the release's files and its
Ed25519 signature SHA256SUMS.txt.sig, which the app checks (core/updater.py) before it
installs anything by itself.

    python release_sign.py <folder with the release files>
    python release_sign.py <folder> --verify

The private key stays outside the repository, next to the Android signing key:
~/.altair-keys/altair-update-ed25519.pem (or --key). Losing it means the apps already out
cannot verify new releases: they would show them, but only offer a manual download.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import sys
from pathlib import Path

DEFAULT_KEY = Path.home() / ".altair-keys" / "altair-update-ed25519.pem"
SUMS = "SHA256SUMS.txt"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_sums(folder: Path) -> bytes:
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.name not in (SUMS, SUMS + ".sig"))
    if not files:
        raise SystemExit(f"no release files in {folder}")
    text = "".join(f"{sha256(p)} *{p.name}\n" for p in files).encode("utf-8")
    (folder / SUMS).write_bytes(text)
    return text


def main() -> int:
    from cryptography.hazmat.primitives import serialization

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from core.updater import RELEASE_PUBLIC_KEY, verify_signature

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--key", type=Path, default=DEFAULT_KEY)
    parser.add_argument("--verify", action="store_true", help="only check the folder's signature")
    args = parser.parse_args()
    folder: Path = args.folder

    if args.verify:
        sums = (folder / SUMS).read_bytes()
        signature = (folder / (SUMS + ".sig")).read_text(encoding="ascii")
        ok = verify_signature(sums, signature)
        listed = {line.split(" *", 1)[1]: line.split(" ", 1)[0] for line in sums.decode().splitlines() if " *" in line}
        bad = [name for name, digest in listed.items() if sha256(folder / name) != digest]
        print("signature:", "OK" if ok else "BAD", "| files:", "OK" if not bad else f"changed: {bad}")
        return 0 if ok and not bad else 1

    key = serialization.load_pem_private_key(args.key.read_bytes(), password=None)
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    if base64.b64encode(public).decode() != RELEASE_PUBLIC_KEY:
        raise SystemExit("this key is not the one the app trusts (core/updater.py RELEASE_PUBLIC_KEY)")
    sums = write_sums(folder)
    signature = base64.b64encode(key.sign(sums)).decode("ascii")
    (folder / (SUMS + ".sig")).write_text(signature + "\n", encoding="ascii")
    print(sums.decode().rstrip())
    print(f"signed: {folder / (SUMS + '.sig')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
