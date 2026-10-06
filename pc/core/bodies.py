"""Bodies: every device the one agent lives on (this PC, a phone, servers) has its own key.

A body's identity is an Ed25519 key pair kept in the data folder; its id is a short hash of the
public key. A body lets in only the bodies it trusts (their public keys in trusted.json), and
lets them in by proof, not by a shared secret: it hands out a one-time challenge, the other body
signs it, the signature is checked against the trusted key, and a short-lived session token is
issued. Revoking a body (a lost laptop) ends its sessions at once and keeps it out.

The shared bridge token stays for the phone on the LAN (0.1–0.2); bodies reached over the
internet (servers, 0.3.0) use keys.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.fs_atomic import atomic_write_text
from core.logging_setup import get_logger

logger = get_logger("bodies")

KINDS = ("pc", "phone", "server")
#: How long a challenge may be answered, and how long a session token lives.
CHALLENGE_SECONDS = 60
SESSION_SECONDS = 12 * 3600
#: What a body signs: the purpose, the challenge and who asked, so a signature made for one
#: server cannot be replayed to another.
SIGNED_PREFIX = "altair-body-auth:v1"


def body_id(public_key: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(public_key).digest()).decode("ascii")[:16]


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def signed_message(nonce: str, verifier_id: str) -> bytes:
    return f"{SIGNED_PREFIX}:{nonce}:{verifier_id}".encode()


# ---------------------------------------------------------------- this body


class Identity:
    """This body's key pair (created on first use) and its public description."""

    def __init__(self, folder: Path, kind: str = "pc", name: str = "") -> None:
        self.folder = Path(folder)
        self.kind = kind if kind in KINDS else "pc"
        self.name = name or socket.gethostname()
        self._private = None
        # Re-entrant: making the key logs its id, which reads the key again.
        self._lock = threading.RLock()

    @property
    def _key_path(self) -> Path:
        return self.folder / "body_key.pem"

    def _load(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        with self._lock:
            if self._private is not None:
                return self._private
            if self._key_path.exists():
                self._private = serialization.load_pem_private_key(self._key_path.read_bytes(), password=None)
            else:
                self.folder.mkdir(parents=True, exist_ok=True)
                self._private = Ed25519PrivateKey.generate()
                pem = self._private.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
                )
                atomic_write_text(self._key_path, pem.decode("ascii"))
                try:
                    os.chmod(self._key_path, 0o600)   # the private key is readable by its owner only
                except OSError as exc:
                    logger.debug("chmod body key: %s", exc)
                logger.info("A new body key was created (%s)", self.id)
            return self._private

    @property
    def public_key(self) -> bytes:
        from cryptography.hazmat.primitives import serialization

        return self._load().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    @property
    def id(self) -> str:
        return body_id(self.public_key)

    def sign(self, message: bytes) -> str:
        return _b64(self._load().sign(message))

    def answer(self, nonce: str, verifier_id: str) -> str:
        """The signature that proves this body to `verifier_id` for this challenge."""
        return self.sign(signed_message(nonce, verifier_id))

    def card(self) -> dict[str, Any]:
        """What another body needs to trust this one."""
        return {"id": self.id, "name": self.name, "kind": self.kind, "public_key": _b64(self.public_key)}


# ---------------------------------------------------------------- the bodies this one trusts


@dataclass
class TrustedBody:
    id: str
    name: str
    kind: str
    public_key: str
    added_at: float = field(default_factory=time.time)
    revoked_at: float = 0.0

    @property
    def revoked(self) -> bool:
        return bool(self.revoked_at)


class TrustStore:
    """trusted.json: the bodies allowed in. Written atomically; a revoked body stays listed
    (with the time) so the owner sees what was cut off and when."""

    def __init__(self, folder: Path) -> None:
        self.path = Path(folder) / "trusted.json"
        self._lock = threading.Lock()

    def all(self) -> list[TrustedBody]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        out = []
        for item in raw if isinstance(raw, list) else []:
            try:
                out.append(TrustedBody(**{k: item[k] for k in TrustedBody.__dataclass_fields__ if k in item}))
            except (TypeError, KeyError):
                continue
        return out

    def get(self, bid: str) -> TrustedBody | None:
        return next((b for b in self.all() if b.id == bid), None)

    def _save(self, bodies: list[TrustedBody]) -> None:
        atomic_write_text(self.path, json.dumps([asdict(b) for b in bodies], ensure_ascii=False, indent=2))

    def add(self, card: dict[str, Any]) -> TrustedBody:
        """Trusts a body by its card. The id must be the hash of the key it comes with: a card
        cannot claim another body's id."""
        key = base64.b64decode(str(card.get("public_key") or ""), validate=True)
        if len(key) != 32:
            raise ValueError("not an Ed25519 public key")
        bid = body_id(key)
        if card.get("id") and card["id"] != bid:
            raise ValueError("the id does not match the key")
        kind = str(card.get("kind") or "pc")
        body = TrustedBody(id=bid, name=str(card.get("name") or bid)[:80], kind=kind if kind in KINDS else "pc",
                           public_key=_b64(key))
        with self._lock:
            bodies = [b for b in self.all() if b.id != bid]
            bodies.append(body)
            self._save(bodies)
        return body

    def revoke(self, bid: str) -> bool:
        with self._lock:
            bodies = self.all()
            hit = False
            for b in bodies:
                if b.id == bid and not b.revoked:
                    b.revoked_at = time.time()
                    hit = True
            if hit:
                self._save(bodies)
            return hit


# ---------------------------------------------------------------- letting a body in


class Gate:
    """Challenges, signatures and the session tokens they buy (in memory: a restart signs
    everyone out, which is what a restart should do)."""

    def __init__(self, identity: Identity, trust: TrustStore) -> None:
        self.identity = identity
        self.trust = trust
        self._challenges: dict[str, tuple[str, float]] = {}     # nonce -> (body id, expires)
        self._sessions: dict[str, tuple[str, float]] = {}       # token -> (body id, expires)
        self._lock = threading.Lock()

    def challenge(self, bid: str) -> dict[str, str]:
        nonce = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._challenges = {n: v for n, v in self._challenges.items() if v[1] > now}
            self._challenges[nonce] = (bid, now + CHALLENGE_SECONDS)
        return {"nonce": nonce, "verifier": self.identity.id}

    def login(self, bid: str, nonce: str, signature_b64: str) -> str | None:
        """A session token when `signature` is the trusted body's answer to its own challenge."""
        with self._lock:
            pending = self._challenges.pop(nonce, None)       # a challenge is answered once
        if not pending or pending[0] != bid or pending[1] < time.time():
            return None
        body = self.trust.get(bid)
        if body is None or body.revoked:
            return None
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        try:
            key = Ed25519PublicKey.from_public_bytes(base64.b64decode(body.public_key))
            key.verify(base64.b64decode(signature_b64), signed_message(nonce, self.identity.id))
        except (InvalidSignature, ValueError):
            return None
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = (bid, time.time() + SESSION_SECONDS)
        return token

    def who(self, token: str) -> str | None:
        """The body behind a session token (None: unknown, expired or revoked since)."""
        if not token:
            return None
        with self._lock:
            entry = self._sessions.get(token)
        if not entry or entry[1] < time.time():
            return None
        body = self.trust.get(entry[0])
        if body is None or body.revoked:
            with self._lock:
                self._sessions.pop(token, None)
            return None
        return entry[0]

    def end_sessions(self, bid: str) -> int:
        with self._lock:
            gone = [t for t, (b, _) in self._sessions.items() if b == bid]
            for t in gone:
                self._sessions.pop(t, None)
        return len(gone)


_GATES: dict[str, Gate] = {}
_GATES_LOCK = threading.Lock()


def get_gate(folder: Path, kind: str = "pc") -> Gate:
    """The gate of this body (one per identity folder)."""
    key = str(Path(folder).resolve())
    with _GATES_LOCK:
        gate = _GATES.get(key)
        if gate is None:
            gate = _GATES[key] = Gate(Identity(Path(folder), kind), TrustStore(Path(folder)))
        return gate
