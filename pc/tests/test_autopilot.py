"""The "Autopilot" mode (0.3.0 stage 4): everything runs by itself except reconfiguring the machine
— packages, services, users, network, /etc, power, disks — which asks the owner."""

from __future__ import annotations

import pytest

from core.security.approval import ApprovalRequest
from core.security.system_change import system_change
from core.tools import build_default_registry
from core.tools.base import ToolContext


@pytest.mark.parametrize("command", [
    "sudo apt-get install -y nginx", "apt remove --purge nginx", "dnf install htop", "systemctl restart nginx",
    "systemctl --now disable ssh", "service nginx reload", "useradd deploy", "passwd root", "ufw allow 80",
    "iptables -A INPUT -p tcp --dport 22 -j DROP", "ip route add default via 10.0.0.1", "netplan apply",
    "echo 'PermitRootLogin no' >> /etc/ssh/sshd_config", "sed -i 's/22/2222/' /etc/ssh/sshd_config",
    "cp nginx.conf /etc/nginx/sites-enabled/app", "reboot", "shutdown -h now", "mkfs.ext4 /dev/vdb",
    "crontab -r", "sysctl -w net.ipv4.ip_forward=1", "echo x | sudo tee /etc/hosts",
])
def test_these_change_the_system(command):
    assert system_change(command), command


@pytest.mark.parametrize("command", [
    "ls -la /etc", "cat /etc/os-release", "git pull && npm ci && npm run build", "python3 manage.py migrate",
    "docker compose up -d", "systemctl status nginx", "journalctl -u nginx -n 50", "apt list --installed",
    "pytest -q", "curl -s https://example.com", "grep -r TODO src/", "pip install -r requirements.txt",
])
def test_these_do_not(command):
    assert system_change(command) == "", command


async def test_autopilot_runs_ordinary_commands_and_asks_for_system_ones(settings):
    asked: list[ApprovalRequest] = []

    async def deny(request):
        asked.append(request)
        return False

    settings.approval_mode = "autopilot"
    ctx = ToolContext(settings=settings, approver=deny)
    shell = build_default_registry().get("execute_command")
    ok = await shell.invoke({"command": "python -c \"print(40+2)\""}, ctx)
    assert ok.ok and "42" in ok.content and asked == []                  # ran by itself
    blocked = await shell.invoke({"command": "systemctl restart nginx"}, ctx)
    assert not blocked.ok and len(asked) == 1                            # asked, and the owner said no
    edit = await build_default_registry().get("write_file").invoke({"path": "a.txt", "content": "x"}, ctx)
    assert edit.ok and len(asked) == 1                                   # edits go by themselves


async def test_a_careful_server_keeps_asking_from_an_autopilot_chat():
    from core.bodies_routing import stricter

    assert stricter("autopilot", "manual") == "manual"
    assert stricter("bypass", "autopilot") == "autopilot"
    assert stricter("autopilot", "accept_edits") == "accept_edits"
