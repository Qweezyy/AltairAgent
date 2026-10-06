"""What a server is, before anything is installed: one round of read-only commands.

The report says whether the agent can be installed there (Linux with systemd, x64 or arm64,
enough memory and disk, root or sudo), what is missing and will be installed (unzip, curl), and
recommends a mode: a dedicated, empty server → "owner" (the agent works right on it); a server
with other services already running → "autopilot" (edits freely, asks before risky commands).
The verdicts are in the UI language: the window shows them as they are.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from core.i18n import tr
from core.servers.remote import SSHRemote

#: Below this the agent and its browser do not fit; the installer refuses rather than limp.
MIN_MEMORY_MB = 900
MIN_DISK_MB = 2500
#: Ports a bare server listens on by itself: SSH and the local DNS stub. And the agent's own port:
#: without root `ss` does not name the process, and Altair already there is not "other services".
_BASE_PORTS = {"22", "53", "8137"}

_PROBE = r"""
echo "arch=$(uname -m)"
. /etc/os-release 2>/dev/null && echo "system=$PRETTY_NAME" && echo "os_id=$ID"
echo "cpus=$(nproc 2>/dev/null)"
echo "mem_mb=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)"
echo "disk_mb=$(df -Pm / | awk 'NR==2{print $4}')"
echo "uid=$(id -u)"
echo "systemd=$(command -v systemctl >/dev/null && echo yes || echo no)"
echo "docker=$(command -v docker >/dev/null && echo yes || echo no)"
echo "unzip=$(command -v unzip >/dev/null && echo yes || echo no)"
echo "curl=$(command -v curl >/dev/null && echo yes || echo no)"
echo "apt=$(command -v apt-get >/dev/null && echo yes || echo no)"
echo "sudo_nopass=$(sudo -n true 2>/dev/null && echo yes || echo no)"
echo "sudo=$(command -v sudo >/dev/null && echo yes || echo no)"
echo "github=$(curl -s -o /dev/null -m 15 -w '%{http_code}' https://api.github.com 2>/dev/null || echo 0)"
echo "ports=$(ss -ltnpH 2>/dev/null | awk '!/"LocalAIAgent"|"altair"/{n=split($4,a,":"); print a[n]}' | sort -u | tr '\n' ' ')"
echo "installed=$(test -x /opt/altair/current/LocalAIAgent && cat /opt/altair/current/VERSION 2>/dev/null || echo)"
"""


@dataclass
class Preflight:
    arch: str = ""
    system: str = ""
    os_id: str = ""
    cpus: int = 0
    mem_mb: int = 0
    disk_mb: int = 0
    root: bool = False
    sudo: str = "none"            # "root" | "nopass" | "password" | "none"
    systemd: bool = False
    docker: bool = False
    missing: list[str] = field(default_factory=list)
    apt: bool = False
    github: bool = False
    busy_ports: list[str] = field(default_factory=list)
    installed: str = ""
    host_key_fingerprint: str = ""
    problems: list[str] = field(default_factory=list)
    recommended_mode: str = "owner"
    why: str = ""

    @property
    def package_arch(self) -> str:
        return {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(self.arch, "")

    @property
    def can_install(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "package_arch": self.package_arch, "can_install": self.can_install}


def parse(text: str) -> Preflight:
    raw: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            raw[key.strip()] = value.strip()
    p = Preflight(
        arch=raw.get("arch", ""), system=raw.get("system", ""), os_id=raw.get("os_id", ""),
        cpus=_int(raw.get("cpus")), mem_mb=_int(raw.get("mem_mb")), disk_mb=_int(raw.get("disk_mb")),
        root=raw.get("uid") == "0", systemd=raw.get("systemd") == "yes", docker=raw.get("docker") == "yes",
        apt=raw.get("apt") == "yes", github=raw.get("github", "0").startswith(("2", "3")),
        installed=raw.get("installed", ""),
    )
    p.sudo = "root" if p.root else "nopass" if raw.get("sudo_nopass") == "yes" else (
        "password" if raw.get("sudo") == "yes" else "none")
    p.missing = [tool for tool in ("unzip", "curl") if raw.get(tool) != "yes"]
    p.busy_ports = [port for port in raw.get("ports", "").split() if port and port not in _BASE_PORTS]
    _judge(p)
    return p


def _int(value: str | None) -> int:
    try:
        return int(str(value or "0").strip() or 0)
    except ValueError:
        return 0


def _judge(p: Preflight) -> None:
    if not p.package_arch:
        p.problems.append(tr("srv.p.arch", arch=p.arch or "?"))
    if not p.systemd:
        p.problems.append(tr("srv.p.systemd"))
    if p.mem_mb and p.mem_mb < MIN_MEMORY_MB:
        p.problems.append(tr("srv.p.memory", mb=p.mem_mb, need=MIN_MEMORY_MB))
    if p.disk_mb and p.disk_mb < MIN_DISK_MB:
        p.problems.append(tr("srv.p.disk", mb=p.disk_mb, need=MIN_DISK_MB))
    if p.missing and not p.apt:
        p.problems.append(tr("srv.p.missing", tools=", ".join(p.missing)))
    if p.sudo == "none":
        p.problems.append(tr("srv.p.sudo"))
    # Other services already listen here: the agent should not get the run of this machine.
    if p.busy_ports:
        p.recommended_mode = "autopilot"
        p.why = tr("srv.why.busy", ports=", ".join(p.busy_ports[:6]))
    else:
        p.recommended_mode = "owner"
        p.why = tr("srv.why.empty")


async def run_preflight(remote: SSHRemote) -> Preflight:
    result = await remote.run(_PROBE, timeout=60)
    report = parse(result.out)
    report.host_key_fingerprint = remote.host_key_fingerprint
    if report.sudo == "nopass":
        remote.sudo = "sudo -n"
    elif report.sudo == "password":
        remote.sudo = "sudo -S -p ''"
    return report
