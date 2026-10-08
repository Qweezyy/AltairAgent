"""A server's own door for the phone: TLS on a port of its own, entered only with a body's key.

Off by default (the PC's tunnel and the phone's way through the PC need no open port). Turned on
from the PC (Settings → Servers), it listens on every address with a self-signed certificate made
here; the phone pins that certificate's SHA-256 when it pairs (the PC's QR carries it), so no
certificate authority is involved and a man in the middle is refused. Behind it the usual checks
hold: without a trusted body's session token only /api/health and the pairing and sign-in paths
answer (server/remote_auth.py).
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import socket
from pathlib import Path
from typing import Any

from core.logging_setup import get_logger
from server.lan_bridge import _Listener

logger = get_logger("server.remote_access")


def ensure_certificate(folder: Path, name: str) -> tuple[Path, Path, str]:
    """This body's TLS certificate and key (made once, kept), and the certificate's SHA-256."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    folder.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = folder / "cert.pem", folder / "key.pem"
    if not (cert_path.exists() and key_path.exists()):
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"altair-{name}"[:64])])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=3650))
                .sign(key, hashes.SHA256()))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
        try:
            key_path.chmod(0o600)
        except OSError as exc:
            logger.debug("chmod tls key: %s", exc)
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path, fingerprint(cert_path)


def fingerprint(cert_path: Path) -> str:
    """SHA-256 of the certificate (DER), hex — what the phone pins."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


class _FromOutside:
    """Whatever comes through the door is from outside — even a connection from this machine
    itself: no "this machine" shortcut may open behind it (found by the tests)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") in ("http", "websocket"):
            client = scope.get("client") or ("", 0)
            scope = {**scope, "client": (DOOR_CLIENT, client[1])}
        await self.app(scope, receive, send)


#: The client address every request through the door carries (never a loopback one).
DOOR_CLIENT = "remote-door"


class _TlsListener(_Listener):
    """The LAN bridge's listener, with TLS; everything through it counts as from outside."""

    def __init__(self, app: Any, host: str, port: int, cert: Path, key: Path) -> None:
        super().__init__(_FromOutside(app), host, port)
        self.cert, self.key = cert, key

    async def start(self) -> None:
        import uvicorn

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.host, self.port))
        except OSError:
            sock.close()
            raise
        sock.setblocking(False)
        config = uvicorn.Config(self._app, lifespan="off", log_level="warning", ws_ping_interval=30,
                                ssl_certfile=str(self.cert), ssl_keyfile=str(self.key))
        self._server = uvicorn.Server(config)
        config.load()
        self._server.lifespan = config.lifespan_class(config)
        await self._server.startup(sockets=[sock])
        self._task = asyncio.create_task(self._run(), name=f"remote-access-{self.port}")


class RemoteAccess:
    def __init__(self, app: Any) -> None:
        self.app = app
        self._listener: _TlsListener | None = None
        self.error = ""
        self.fingerprint = ""

    @property
    def enabled(self) -> bool:
        return self._listener is not None

    def status(self) -> dict[str, Any]:
        settings = self.app.state.settings
        return {"enabled": self.enabled, "port": settings.remote_port, "fingerprint": self.fingerprint,
                "error": self.error}

    async def apply(self, enabled: bool) -> dict[str, Any]:
        settings = self.app.state.settings
        if self._listener is not None:
            await self._listener.stop()
            self._listener = None
        self.error = ""
        if not enabled:
            return self.status()
        from core.bodies import get_gate

        name = get_gate(settings.data_dir / "identity", settings.body_kind).identity.id
        cert, key, self.fingerprint = await asyncio.to_thread(ensure_certificate, settings.data_dir / "remote", name)
        listener = _TlsListener(self.app, "0.0.0.0", settings.remote_port, cert, key)
        try:
            await listener.start()
        except OSError as exc:
            self.error = f"port {settings.remote_port}: {exc}"
            logger.warning("remote access did not start: %s", self.error)
            return self.status()
        self._listener = listener
        logger.info("remote access on :%d (certificate %s…)", settings.remote_port, self.fingerprint[:16])
        return self.status()

    async def close(self) -> None:
        if self._listener is not None:
            await self._listener.stop()
            self._listener = None
