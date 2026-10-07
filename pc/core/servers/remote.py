"""Commands and file uploads on a server, over SSH.

The server's host key is pinned on first contact (trust on first use, as `ssh` asks "are you
sure you want to continue connecting"): the owner sees its fingerprint when adding the server,
and every later connection must present the same key or is refused — a man in the middle cannot
pose as the server afterwards.

Logging in: a password is accepted only for the first connection, to put this PC's own key on
the server; it is never stored. After that the key is used.
"""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core.i18n import tr
from core.logging_setup import get_logger

logger = get_logger("servers.remote")

#: How long one command may run before it is cut (an apt-get or a download takes minutes).
COMMAND_TIMEOUT = 900


class RemoteError(RuntimeError):
    """The server could not be reached, refused the login, or presented another host key."""


@dataclass
class RunResult:
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0


@dataclass
class Login:
    host: str
    port: int = 22
    user: str = "root"
    password: str = ""
    key_path: str = ""
    #: The pinned host key line ("ssh-ed25519 AAAA…"); empty on first contact.
    host_key: str = ""

    def safe(self) -> dict:
        """For logs and the Journal: never the password."""
        return {"host": self.host, "port": self.port, "user": self.user, "with": "key" if self.key_path else "password"}


class SSHRemote:
    """One SSH connection to a server. Use as `async with SSHRemote(login) as r: await r.run(...)`."""

    def __init__(self, login: Login) -> None:
        self.login = login
        self._conn = None
        self.host_key = ""             # the key line the server presented
        self.host_key_fingerprint = ""
        #: A command prefix that gives root rights ("" when logged in as root).
        self.sudo = ""
        self.sudo_password = ""

    async def __aenter__(self) -> SSHRemote:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        import asyncssh

        login = self.login
        known = None
        if login.host_key:
            name = login.host if login.port == 22 else f"[{login.host}]:{login.port}"
            known = asyncssh.import_known_hosts(f"{name} {login.host_key}\n")
        # Keepalives notice a dead link (a sleeping laptop, a dropped Wi-Fi) within ~45 s; the
        # tunnel then reconnects instead of hanging on a half-open connection.
        options: dict = {"known_hosts": known, "connect_timeout": 25, "username": login.user,
                         "port": login.port, "keepalive_interval": 15, "keepalive_count_max": 3}
        if login.key_path:
            options["client_keys"] = [login.key_path]
            options["password"] = None
        else:
            options["client_keys"] = None     # only the password the owner typed, not ~/.ssh
            options["password"] = login.password
        try:
            self._conn = await asyncssh.connect(login.host, **options)
        except asyncssh.HostKeyNotVerifiable as exc:
            raise RemoteError(tr("srv.e.host_key")) from exc
        except asyncssh.PermissionDenied as exc:
            raise RemoteError(tr("srv.e.denied")) from exc
        except (TimeoutError, asyncio.TimeoutError) as exc:
            # str() of a timeout is empty: say what happened instead of "could not connect: ".
            raise RemoteError(tr("srv.e.timeout", where=f"{login.host}:{login.port}",
                                 seconds=options["connect_timeout"])) from exc
        except (OSError, asyncssh.Error) as exc:
            raise RemoteError(tr("srv.e.connect", where=f"{login.host}:{login.port}",
                                 why=str(exc) or type(exc).__name__)) from exc
        key = self._conn.get_server_host_key()
        if key is not None:
            self.host_key = key.export_public_key("openssh").decode("ascii").strip()
            self.host_key_fingerprint = key.get_fingerprint()

    async def forward_local(self, remote_port: int, local_port: int = 0) -> int:
        """Forwards 127.0.0.1:<local_port> here to 127.0.0.1:<remote_port> on the server (0 = any
        free port); returns the local port."""
        if self._conn is None:
            raise RemoteError("not connected")
        listener = await self._conn.forward_local_port("127.0.0.1", local_port, "127.0.0.1", remote_port)
        return int(listener.get_port())

    async def wait_closed(self) -> None:
        """Returns when the connection is gone (closed here, by the server or by the keepalive)."""
        if self._conn is not None:
            await self._conn.wait_closed()

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            await self._conn.wait_closed()
            self._conn = None

    async def run(self, command: str, *, root: bool = False, timeout: float = COMMAND_TIMEOUT,
                  stdin: str | None = None) -> RunResult:
        """Runs a shell command. `root`: with the root rights this login has (sudo when not root)."""
        import asyncssh

        if self._conn is None:
            raise RemoteError("not connected")
        full = command
        data = stdin
        if root and self.sudo:
            full = f"{self.sudo} sh -c {shlex.quote(command)}"
            if self.sudo_password:
                # sudo -S reads the password from stdin; it is never written to disk or logged.
                data = self.sudo_password + "\n" + (stdin or "")
        try:
            r = await self._conn.run(full, check=False, timeout=timeout, input=data)
        except asyncssh.TimeoutError as exc:
            raise RemoteError(f"the command took longer than {int(timeout)} s: {command[:80]}") from exc
        except (OSError, asyncssh.Error) as exc:
            raise RemoteError(f"the connection broke: {exc}") from exc
        return RunResult(int(r.exit_status if r.exit_status is not None else 255), str(r.stdout or ""), str(r.stderr or ""))

    async def put(self, local: Path, remote_path: str, on_progress: Callable[[int, int], None] | None = None) -> None:
        """Uploads a file (SFTP)."""
        if self._conn is None:
            raise RemoteError("not connected")
        total = (await asyncio.to_thread(Path(local).stat)).st_size

        def progress(_src: bytes, _dst: bytes, done: int, _total: int) -> None:
            if on_progress:
                on_progress(done, total)

        async with self._conn.start_sftp_client() as sftp:
            await sftp.put(str(local), remote_path, progress_handler=progress)


async def make_key(path: Path, comment: str) -> str:
    """A new ed25519 key pair for one server; returns the public key line."""
    return await asyncio.to_thread(_write_key, path, comment)


def _write_key(path: Path, comment: str) -> str:
    import asyncssh

    key = asyncssh.generate_private_key("ssh-ed25519", comment=comment)
    path.parent.mkdir(parents=True, exist_ok=True)
    key.write_private_key(str(path))
    public = key.export_public_key("openssh").decode("ascii").strip()
    path.with_suffix(".pub").write_text(public + "\n", encoding="ascii")
    try:
        path.chmod(0o600)
    except OSError as exc:
        logger.debug("chmod server key: %s", exc)
    return public
