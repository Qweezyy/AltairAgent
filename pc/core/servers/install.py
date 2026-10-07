"""Installing the agent on a server, by itself, from the owner's SSH login.

The steps (each one reports progress and may be retried):

    connect     the login works; the server's host key is pinned
    preflight   what the server is (core/servers/preflight.py); refuse early if it cannot work
    key         this PC's own SSH key goes into authorized_keys; from here on the key is used
                and the password is forgotten
    packages    unzip/curl when missing (apt-get)
    download    the signed release for the server's architecture: the server downloads it from
                GitHub and the PC checks its SHA-256 against the checksums whose Ed25519 signature
                it verified itself; or a local package is uploaded (offline, or a build not
                released yet)
    unpack      into /opt/altair/releases/<version>, `current` points at it (the old version
                stays for a rollback)
    configure   /var/lib/altair/.env: the model settings and keys from this PC (no Windows paths,
                no PC-only settings), the server's mode; memory, skills and providers copied over
    identity    the server's body key is made, the PC and the server trust each other's card
    service     a systemd service that restarts by itself, listening on 127.0.0.1 only
    health      the agent answers on the server

The password and the sudo password stay in memory for the duration of the install and are
never written anywhere, logged or put in the Journal.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import shlex
import tarfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from core.logging_setup import get_logger
from core.servers.preflight import Preflight, run_preflight
from core.servers.registry import ServerRecord, ServerStore
from core.servers.remote import Login, RemoteError, SSHRemote, make_key
from core.settings import Settings
from core.version import __version__

logger = get_logger("servers.install")

STEPS = ("connect", "preflight", "key", "packages", "download", "unpack", "configure", "identity", "service",
         "health")
MODES = ("owner", "autopilot", "careful")
#: The approval mode the agent works in on the server, per server mode.
MODE_APPROVAL = {"owner": "bypass", "autopilot": "autopilot", "careful": "manual"}
REPO = "Qweezyy/AltairAgent"
SERVER_PORT = 8137
#: Settings of this PC that make no sense on a server (they are set for the server instead).
_LOCAL_ONLY_KEYS = {
    "HOST", "PORT", "WORKSPACE_PATH", "APP_PATH", "BRIDGE_LAN", "BRIDGE_TOKEN", "APPROVAL_MODE", "BODY_KIND",
    "CLI_ON_PATH", "UPDATE_URL", "BROWSER_NETWORK", "SEARXNG_URL",
}
_WINDOWS_PATH = re.compile(r"(^|[\"'=\s])[A-Za-z]:[\\/]")

Progress = Callable[[str, str, float], None]


class InstallError(RuntimeError):
    """An install step failed; `step` says which, the message why."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(message)
        self.step = step


@dataclass
class InstallRequest:
    host: str
    user: str = "root"
    port: int = 22
    password: str = ""
    sudo_password: str = ""
    name: str = ""
    mode: str = "owner"
    #: "github" (the signed release) or a path to a local package zip.
    source: str = "github"
    #: Copy this PC's memory, skills and providers to the server.
    bring_memory: bool = True
    #: An update of a server added before: only the program changes — its settings, memory and
    #: body keys stay as they are there (the server may have learned and changed things).
    update: bool = False


@dataclass
class ReleaseRef:
    version: str
    name: str
    url: str
    sha256: str


@dataclass
class Layout:
    """Where things go on the server: system-wide as root, in the home folder otherwise."""

    install_dir: str = "/opt/altair"
    data_dir: str = "/var/lib/altair"
    unit_path: str = "/etc/systemd/system/altair.service"
    user_service: bool = False
    #: `altair` on the server's PATH, so whoever logs in there talks to the same agent.
    command: str = "/usr/local/bin/altair"
    guardian_unit_path: str = "/etc/systemd/system/altair-guardian.service"

    @classmethod
    def for_login(cls, root_rights: bool, home: str) -> Layout:
        if root_rights:
            return cls()
        return cls(install_dir=f"{home}/.local/share/altair", data_dir=f"{home}/.local/share/altair/data",
                   unit_path=f"{home}/.config/systemd/user/altair.service", user_service=True,
                   command=f"{home}/.local/bin/altair",
                   guardian_unit_path=f"{home}/.config/systemd/user/altair-guardian.service")


@dataclass
class InstallState:
    """What the install learned on the way: the report it gives back."""

    server_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    preflight: Preflight | None = None
    host_key: str = ""
    host_key_fingerprint: str = ""
    version: str = ""
    body_id: str = ""
    #: The release folder this install put on the server.
    release: str = ""


# ---------------------------------------------------------------- the release


async def signed_release(arch: str, repo: str = REPO) -> ReleaseRef:
    """The latest release's package for `arch` (x64 | arm64) with its checksum, the checksums'
    signature verified here, on the PC, with the app's release key."""
    from core.updater import parse_sums, verify_signature

    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"Altair/{__version__}"}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=headers) as client:
        release = (await client.get(f"https://api.github.com/repos/{repo}/releases/latest")).raise_for_status().json()
        assets = {a.get("name", ""): a.get("browser_download_url", "") for a in release.get("assets") or []}
        suffix = f"-linux-{arch}.zip"
        package = next((n for n in assets if n.endswith(suffix)), "")
        if not package:
            raise InstallError("download", f"the latest release has no Linux {arch} package")
        sums_url, sig_url = assets.get("SHA256SUMS.txt"), assets.get("SHA256SUMS.txt.sig")
        if not sums_url or not sig_url:
            raise InstallError("download", "the latest release is not signed")
        sums = (await client.get(sums_url)).raise_for_status().content
        signature = (await client.get(sig_url)).raise_for_status().text
    if not verify_signature(sums, signature):
        raise InstallError("download", "the release's signature does not match the app's key")
    sha = parse_sums(sums.decode("utf-8", "replace")).get(package, "")
    if not sha:
        raise InstallError("download", "the signed checksums do not list the package")
    version = str(release.get("tag_name") or "").lstrip("vV").strip()
    return ReleaseRef(version=version, name=package, url=assets[package], sha256=sha)


#: The first version that can be a server body (--body-card / --trust-body, BODY_KIND).
MIN_SERVER_VERSION = "0.3.0"


def _older_than(version: str, floor: str) -> bool:
    def parts(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in re.findall(r"\d+", v.split("-")[0])[:3]) or (0,)

    return parts(version) < parts(floor)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version_of_package(path: Path) -> str:
    """The version a local package carries: Altair-0.3.0-linux-x64.zip → 0.3.0."""
    match = re.search(r"(\d+\.\d+\.\d+)", path.name)
    return match.group(1) if match else f"local-{_sha256_file(path)[:8]}"


# ---------------------------------------------------------------- what the server gets from this PC


def server_env(pc_env_text: str, mode: str, layout: Layout) -> str:
    """The server's .env: this PC's model and agent settings (keys included — they travel only
    over SSH and land in a file only the agent's user can read), without what belongs to this PC
    (its paths, its ports, its bridge), plus what the server needs."""
    kept: list[str] = []
    for raw in pc_env_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in _LOCAL_ONLY_KEYS or _WINDOWS_PATH.search("=" + value):
            continue
        kept.append(f"{key}={value.strip()}")
    server = [
        "# Written by the Altair server installer. The settings window of any body can change it.",
        "HOST=127.0.0.1",
        f"PORT={SERVER_PORT}",
        "BODY_KIND=server",
        f"APPROVAL_MODE={MODE_APPROVAL.get(mode, 'manual')}",
        f"WORKSPACE_PATH={layout.data_dir}/workspace",
        "JOURNAL=true",
        "BRIDGE_LAN=false",
    ]
    return "\n".join(server + ["", "# From the owner's PC:"] + kept) + "\n"


def bundle_of_self(settings: Settings, with_memory: bool) -> bytes:
    """A tar.gz of what makes this agent "the same one": providers, memory notes, own skills."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        providers = settings.app_dir / "providers.json"
        if providers.is_file():
            tar.add(providers, arcname="providers.json")
        if with_memory:
            memory = settings.data_dir / "memory"
            if memory.is_dir():
                tar.add(memory, arcname="storage/memory")
            skills = settings.app_dir / "skills"
            if skills.is_dir():
                tar.add(skills, arcname="skills")
    return buf.getvalue()


#: A new release must answer within this long, or the guardian goes back to the one before.
UPDATE_DEADLINE_S = 300


def guardian_source() -> str:
    """The guardian script as shipped with this app (a data file in the build, see build_app.py)."""
    return (Path(__file__).resolve().parent / "guardian.py").read_text(encoding="utf-8")


def guardian_unit_text(layout: Layout) -> str:
    target = "default.target" if layout.user_service else "multi-user.target"
    return f"""[Unit]
Description=Altair guardian (keeps the agent alive, rolls back a bad update or change)
After=network-online.target

[Service]
Environment=APP_PATH={layout.data_dir}
Environment=ALTAIR_INSTALL_DIR={layout.install_dir}
Environment=ALTAIR_PORT={SERVER_PORT}
ExecStart=/usr/bin/env python3 {layout.install_dir}/guardian.py watch
Restart=always
RestartSec=10

[Install]
WantedBy={target}
"""


def unit_text(layout: Layout, user_home: str) -> str:
    target = "default.target" if layout.user_service else "multi-user.target"
    return f"""[Unit]
Description=Altair agent (server body)
After=network-online.target
Wants=network-online.target

[Service]
Environment=APP_PATH={layout.data_dir}
Environment=HOME={user_home}
WorkingDirectory={layout.data_dir}
ExecStart={layout.install_dir}/current/LocalAIAgent --server --host 127.0.0.1 --port {SERVER_PORT}
Restart=always
RestartSec=5

[Install]
WantedBy={target}
"""


# ---------------------------------------------------------------- the install


def login_for(store: ServerStore, req: InstallRequest) -> tuple[Login, ServerRecord | None]:
    """How to log in for this request. Installed here before (an update, a repair, a re-check):
    the PC's own key, no password needed, and the host key must be the one seen the first time."""
    known = next((s for s in store.all() if (s.host, s.port, s.user) == (req.host, req.port, req.user)), None)
    if known is not None and store.key_path(known.id).exists() and not req.password:
        return Login(host=req.host, port=req.port, user=req.user, key_path=str(store.key_path(known.id)),
                     host_key=known.host_key), known
    return Login(host=req.host, port=req.port, user=req.user, password=req.password,
                 host_key=known.host_key if known else ""), known


class Installer:
    def __init__(self, settings: Settings, request: InstallRequest, progress: Progress | None = None,
                 remote_factory: Callable[[Login], SSHRemote] | None = None) -> None:
        self.settings = settings
        self.remote_factory = remote_factory or SSHRemote
        self.req = request
        self.progress = progress or (lambda step, detail, pct: None)
        self.state = InstallState()
        self.store = ServerStore(settings.data_dir)

    def _step(self, step: str, detail: str = "", pct: float = 0.0) -> None:
        self.progress(step, detail, pct)

    async def run(self) -> ServerRecord:
        req = self.req
        if req.mode not in MODES:
            raise InstallError("preflight", f"unknown mode '{req.mode}' (owner, autopilot, careful)")
        self._step("connect", f"{req.user}@{req.host}:{req.port}")
        login, known = login_for(self.store, req)
        if known is not None:
            self.state.server_id = known.id
        try:
            async with self.remote_factory(login) as remote:
                self.state.host_key, self.state.host_key_fingerprint = remote.host_key, remote.host_key_fingerprint
                remote.sudo_password = req.sudo_password
                self._step("preflight")
                pre = await run_preflight(remote)
                self.state.preflight = pre
                if not pre.can_install:
                    raise InstallError("preflight", "; ".join(pre.problems))
                if pre.sudo == "password" and not req.sudo_password:
                    raise InstallError("preflight", "this login needs the sudo password")
                home = (await remote.run("printf %s \"$HOME\"")).out.strip() or "/root"
                layout = Layout.for_login(True, home)
                if req.update:
                    if known is None:
                        raise InstallError("connect", "this server was never added here: install it first")
                    key_path = self.store.key_path(known.id)
                else:
                    key_path = await self._put_key(remote)
            # From here on: the PC's own key, the pinned host key, no password.
            key_login = Login(host=req.host, port=req.port, user=req.user, key_path=str(key_path),
                              host_key=self.state.host_key)
            async with self.remote_factory(key_login) as remote:
                remote.sudo = {"root": "", "nopass": "sudo -n", "password": "sudo -S -p ''"}.get(pre.sudo, "")
                remote.sudo_password = req.sudo_password
                await self._packages(remote, pre)
                package = await self._download(remote, pre, layout)
                await self._unpack(remote, package, layout)
                if req.update and known is not None:
                    self.state.body_id = known.body_id
                else:
                    await self._configure(remote, layout)
                    await self._identity(remote, layout)
                await self._service(remote, layout, home)
                await self._guardian(remote, layout)
                await self._health(remote, layout)
        except RemoteError as exc:
            raise InstallError("connect", str(exc)) from exc
        record = ServerRecord(
            id=self.state.server_id, name=req.name or (known.name if known else req.host), host=req.host, port=req.port, user=req.user,
            host_key=self.state.host_key, host_key_fingerprint=self.state.host_key_fingerprint,
            body_id=self.state.body_id, arch=pre.package_arch, system=pre.system, version=self.state.version,
            mode=known.mode if req.update and known is not None else req.mode,
            install_dir=layout.install_dir, data_dir=layout.data_dir,
            user_service=layout.user_service, port_remote=SERVER_PORT,
        )
        self.store.save(record)
        self._step("done", record.name, 1.0)
        return record

    async def _put_key(self, remote: SSHRemote) -> Path:
        """This PC's own key for this server goes into authorized_keys, and is tried at once."""
        self._step("key")
        path = self.store.key_path(self.state.server_id)
        pub = path.with_suffix(".pub")
        if path.exists() and pub.exists():
            public = pub.read_text(encoding="ascii").strip()     # the key this PC already has there
        else:
            public = await make_key(path, f"altair-pc-{self.state.server_id}")
        script = ("umask 077; mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && "
                  f"grep -qxF {shlex.quote(public)} ~/.ssh/authorized_keys || echo {shlex.quote(public)} >> ~/.ssh/authorized_keys")
        result = await remote.run(script)
        if not result.ok:
            raise InstallError("key", f"could not add the key: {result.err.strip()[:300]}")
        try:
            probe = self.remote_factory(Login(host=self.req.host, port=self.req.port, user=self.req.user, key_path=str(path),
                                    host_key=remote.host_key))
            async with probe:
                await probe.run("true")
        except RemoteError as exc:
            raise InstallError("key", f"the server does not accept the new key: {exc}") from exc
        return path

    async def _packages(self, remote: SSHRemote, pre: Preflight) -> None:
        self._step("packages", ", ".join(pre.missing) or "nothing to add")
        if not pre.missing:
            return
        # A fresh server often runs unattended-upgrades right after boot and holds the dpkg lock:
        # wait for it (up to 10 minutes) instead of failing.
        wait = "-o DPkg::Lock::Timeout=600"
        cmd = (f"export DEBIAN_FRONTEND=noninteractive; apt-get {wait} update -qq && "
               f"apt-get {wait} install -y -qq {' '.join(pre.missing)} ca-certificates")
        result = await remote.run(cmd, root=True)
        if not result.ok:
            raise InstallError("packages", f"apt-get failed: {(result.err or result.out).strip()[-400:]}")

    async def _download(self, remote: SSHRemote, pre: Preflight, layout: Layout) -> str:
        """The package on the server, its checksum checked. Returns its path there."""
        downloads = f"{layout.install_dir}/downloads"
        await self._must(remote, f"mkdir -p {downloads}", "download", root=True)
        source = self.req.source
        if source == "github":
            self._step("download", "release")
            try:
                ref = await signed_release(pre.package_arch)
            except httpx.HTTPError as exc:
                raise InstallError("download", f"could not read the release from GitHub: {exc}") from exc
            if _older_than(ref.version, MIN_SERVER_VERSION):
                raise InstallError("download", f"the latest release ({ref.version}) cannot run as a server body "
                                   f"yet: that needs {MIN_SERVER_VERSION} or newer. Install from a newer package.")
            target = f"{downloads}/{ref.name}"
            self.state.version = ref.version
            self._step("download", f"{ref.name}", 0.05)
            await self._must(remote, f"curl -fL --retry 3 -s -o {shlex.quote(target)} {shlex.quote(ref.url)}",
                             "download", root=True)
            expected = ref.sha256
        elif source.startswith("server:"):
            # A package built on the server itself (a development build for its own glibc): it
            # never travelled, so there is no signed checksum to hold it to.
            target = source.removeprefix("server:")
            self.state.version = _version_of_package(Path(target))
            self._step("download", Path(target).name, 1.0)
            exists = await remote.run(f"test -f {shlex.quote(target)}", root=True)
            if not exists.ok:
                raise InstallError("download", f"no package at {target} on the server")
            return target
        else:
            local = Path(source)
            if not await asyncio.to_thread(local.is_file):
                raise InstallError("download", f"no package at {local}")
            self.state.version = _version_of_package(local)
            expected = await asyncio.to_thread(_sha256_file, local)
            target = f"{downloads}/{local.name}"
            # SFTP writes as the login user: upload to its home, then move with root rights.
            staging = f"/tmp/altair-upload-{self.state.server_id}.zip"
            await remote.put(local, staging, lambda done, total: self._step(
                "download", local.name, done / total if total else 0.0))
            await self._must(remote, f"mv {shlex.quote(staging)} {shlex.quote(target)}", "download", root=True)
        got = (await remote.run(f"sha256sum {shlex.quote(target)}", root=True)).out.split()
        if not got or got[0].lower() != expected.lower():
            raise InstallError("download", "the package on the server does not match the signed checksum")
        return target

    async def _unpack(self, remote: SSHRemote, package: str, layout: Layout) -> None:
        self._step("unpack", self.state.version)
        version = self.state.version
        # Its own folder per install (the version alone would overwrite the release running now,
        # and leave nothing to go back to).
        release = f"{layout.install_dir}/releases/{version}-{time.strftime('%Y%m%d-%H%M%S')}"
        self.state.release = release
        staging = f"{layout.install_dir}/releases/.staging"
        script = f"""set -e
rm -rf {staging} && mkdir -p {staging}
unzip -q {shlex.quote(package)} -d {staging}
root=$(dirname "$(find {staging} -maxdepth 3 -type f -name LocalAIAgent | head -1)")
test -x "$root/LocalAIAgent" || chmod +x "$root/LocalAIAgent"
rm -rf {release} && mv "$root" {release} && rm -rf {staging}
chmod +x {release}/LocalAIAgent {release}/bin/altair 2>/dev/null || true
printf %s {shlex.quote(version)} > {release}/VERSION
# The guardian keeps the new release only if it answers in time; otherwise back to this one.
mkdir -p {layout.data_dir}/guardian
prev=$(readlink -f {layout.install_dir}/current 2>/dev/null || true)
if [ -n "$prev" ] && [ -d "$prev" ] && [ "$prev" != {release} ]; then
  printf '{{"previous": "%s", "new": "%s", "deadline": %s}}' "$prev" {release} $(( $(date +%s) + {UPDATE_DEADLINE_S} )) > {layout.data_dir}/guardian/pending-update.json
fi
ln -sfn {release} {layout.install_dir}/current
"""
        await self._must(remote, script, "unpack", root=True)

    async def _configure(self, remote: SSHRemote, layout: Layout) -> None:
        self._step("configure")
        from core.config_file import config_path

        pc_env = ""
        try:
            pc_env = await asyncio.to_thread(config_path(self.settings).read_text, encoding="utf-8")
        except OSError as exc:
            logger.info("no settings file to bring over: %s", exc)
        env_text = server_env(pc_env, self.req.mode, layout)
        bundle = await asyncio.to_thread(bundle_of_self, self.settings, self.req.bring_memory)
        data = layout.data_dir
        await self._must(remote, f"umask 077; mkdir -p {data}/workspace && chmod 700 {data}", "configure", root=True)
        write_env = f"umask 077; cat > {data}/.env"
        result = await remote.run(write_env, root=True, stdin=env_text)
        if not result.ok:
            raise InstallError("configure", f"could not write the settings: {result.err.strip()[:300]}")
        if bundle:
            import base64

            unpack = f"base64 -d | tar -xzf - -C {data}"
            result = await remote.run(unpack, root=True, stdin=base64.b64encode(bundle).decode("ascii"))
            if not result.ok:
                raise InstallError("configure", f"could not copy memory and skills: {result.err.strip()[:300]}")

    async def _identity(self, remote: SSHRemote, layout: Layout) -> None:
        """The server makes its body key; the two bodies trust each other's card."""
        self._step("identity")
        from core.bodies import Identity, TrustStore

        binary = f"{layout.install_dir}/current/LocalAIAgent"
        env = f"APP_PATH={layout.data_dir}"
        card_out = await self._must(remote, f"{env} {binary} --body-card", "identity", root=True)
        card = _last_json(card_out)
        if not card or card.get("kind") != "server":
            raise InstallError("identity", "the server did not give its body card")
        mine = TrustStore(self.settings.data_dir / "identity")
        try:
            await asyncio.to_thread(mine.add, card)
        except ValueError as exc:
            raise InstallError("identity", f"the server's card is not valid: {exc}") from exc
        pc_card = await asyncio.to_thread(lambda: Identity(self.settings.data_dir / "identity",
                                                           self.settings.body_kind).card())
        await self._must(remote, f"{env} {binary} --trust-body {shlex.quote(json.dumps(pc_card))}", "identity",
                         root=True)
        self.state.body_id = str(card["id"])

    async def _service(self, remote: SSHRemote, layout: Layout, home: str) -> None:
        self._step("service")
        unit = unit_text(layout, home)
        result = await remote.run(f"cat > {layout.unit_path}", root=True, stdin=unit)
        if not result.ok:
            raise InstallError("service", f"could not write the service: {result.err.strip()[:300]}")
        # Without APP_PATH the binary would look for its data next to itself, not in data_dir.
        wrapper = (f"#!/bin/sh\nexport APP_PATH={shlex.quote(layout.data_dir)}\n"
                   f"exec {shlex.quote(layout.install_dir)}/current/bin/altair \"$@\"\n")
        folder = shlex.quote(str(Path(layout.command).parent))
        command = shlex.quote(layout.command)
        await self._must(remote, f"mkdir -p {folder} && cat > {command} && chmod 755 {command}", "service",
                         root=not layout.user_service, stdin=wrapper)
        ctl = "systemctl --user" if layout.user_service else "systemctl"
        await self._must(remote, f"{ctl} daemon-reload && {ctl} enable altair >/dev/null 2>&1 && {ctl} restart altair",
                         "service", root=not layout.user_service)

    async def _guardian(self, remote: SSHRemote, layout: Layout) -> None:
        """The guardian next to the agent: its own service, run by the system's python3."""
        source = await asyncio.to_thread(guardian_source)
        script = f"{layout.install_dir}/guardian.py"
        await self._must(remote, f"cat > {shlex.quote(script)} && chmod 755 {shlex.quote(script)}", "service",
                         root=not layout.user_service, stdin=source)
        unit = guardian_unit_text(layout)
        await self._must(remote, f"cat > {shlex.quote(layout.guardian_unit_path)}", "service",
                         root=not layout.user_service, stdin=unit)
        ctl = "systemctl --user" if layout.user_service else "systemctl"
        await self._must(remote, f"{ctl} daemon-reload && {ctl} enable altair-guardian >/dev/null 2>&1 && "
                                 f"{ctl} restart altair-guardian", "service", root=not layout.user_service)

    async def _health(self, remote: SSHRemote, layout: Layout | None = None, seconds: int = 120) -> None:
        self._step("health")
        script = (f"for i in $(seq 1 {seconds // 2}); do curl -fsS -m 3 http://127.0.0.1:{SERVER_PORT}/api/health "
                  "&& exit 0; sleep 2; done; exit 1")
        result = await remote.run(script, timeout=seconds + 30)
        if not result.ok:
            logs = await remote.run("journalctl -u altair -n 25 --no-pager 2>/dev/null | tail -25", root=True)
            detail = (logs.out or logs.err)[-1500:]
            if layout is not None:
                # Do not leave the server with an agent that does not start: back to the release
                # that worked, now (the guardian would do it at its deadline anyway).
                back = await remote.run(f"APP_PATH={shlex.quote(layout.data_dir)} python3 "
                                        f"{shlex.quote(layout.install_dir)}/guardian.py rollback "
                                        "'the install health check failed'", root=not layout.user_service)
                if back.ok:
                    raise InstallError("health", "the new version did not start, so the server went back to the "
                                                 "one it ran before (it keeps working):\n" + detail)
            raise InstallError("health", "the agent did not start on the server:\n" + detail)

    async def _must(self, remote: SSHRemote, command: str, step: str, *, root: bool = False,
                    stdin: str | None = None) -> str:
        result = await remote.run(command, root=root, stdin=stdin)
        if not result.ok:
            detail = (result.err or result.out).strip()[-500:]
            raise InstallError(step, f"{detail or 'exit code ' + str(result.code)}")
        return result.out


def _last_json(text: str) -> dict[str, Any] | None:
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


# ---------------------------------------------------------------- removing it again


async def uninstall(settings: Settings, record: ServerRecord, *, keep_data: bool = False,
                    progress: Progress | None = None) -> None:
    """Stops and removes the agent from the server, takes this PC's key out of authorized_keys,
    forgets the server here and stops trusting its body."""
    report = progress or (lambda step, detail, pct: None)
    store = ServerStore(settings.data_dir)
    login = Login(host=record.host, port=record.port, user=record.user, key_path=str(store.key_path(record.id)),
                  host_key=record.host_key)
    ctl = "systemctl --user" if record.user_service else "systemctl"
    unit = ("~/.config/systemd/user/altair.service" if record.user_service
            else "/etc/systemd/system/altair.service")
    data = "" if keep_data else record.data_dir
    home = record.install_dir.rsplit("/.local/share/altair", 1)[0]
    command = Layout.for_login(not record.user_service, home).command
    async with SSHRemote(login) as remote:
        pre = await run_preflight(remote)
        remote.sudo = {"root": "", "nopass": "sudo -n"}.get(pre.sudo, remote.sudo)
        report("service", "stop", 0.2)
        guardian_unit = unit.replace("altair.service", "altair-guardian.service")
        await remote.run(f"{ctl} disable --now altair-guardian 2>/dev/null; rm -f {guardian_unit}; "
                         f"{ctl} disable --now altair 2>/dev/null; rm -f {unit}; {ctl} daemon-reload",
                         root=not record.user_service)
        report("files", "remove", 0.5)
        await remote.run(f"rm -rf {shlex.quote(record.install_dir)} {shlex.quote(data) if data else ''}; "
                         f"rm -f {shlex.quote(command)}",
                         root=True)
        report("key", "remove", 0.8)
        marker = f"altair-pc-{record.id}"
        await remote.run(f"test -f ~/.ssh/authorized_keys && grep -v {shlex.quote(marker)} ~/.ssh/authorized_keys "
                         "> ~/.ssh/.ak.tmp; mv ~/.ssh/.ak.tmp ~/.ssh/authorized_keys; chmod 600 ~/.ssh/authorized_keys")
    from core.bodies import TrustStore

    if record.body_id:
        await asyncio.to_thread(TrustStore(settings.data_dir / "identity").revoke, record.body_id)
    store.remove(record.id)
    report("done", record.name, 1.0)
