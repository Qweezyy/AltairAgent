"""Does a command change the system itself — packages, services, users, the network, /etc, the
machine's power or disks? The "Autopilot" mode runs every other command by itself and asks only
for these (a server where something else runs: the agent works freely but does not reconfigure the
machine without the owner).

Patterns over the command text, like the risk tiers (core/security/risk.py): they catch the usual
spellings, sudo or not; a command that hides its intent (eval of a variable) is the classifier's
known gap, and "Careful" is the mode for a machine where that matters.
"""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, str]] = [
    (r"\b(apt|apt-get|aptitude)\s+(-\S+\s+)*(install|remove|purge|upgrade|dist-upgrade|full-upgrade|autoremove)\b",
     "installs or removes packages"),
    (r"\bdpkg\s+(-\S*\s+)*(-i|--install|-r|--remove|-P|--purge)\b", "installs or removes packages"),
    (r"\b(yum|dnf|zypper|pacman|apk|snap|flatpak)\s+(-\S+\s+)*(install|remove|erase|update|upgrade|add|del|-S|-R)\b",
     "installs or removes packages"),
    (r"\bsystemctl\s+(--\S+\s+)*(start|stop|restart|reload|enable|disable|mask|unmask|daemon-reload|isolate|"
     r"reboot|poweroff|halt|kill|edit|set-default)\b", "changes system services"),
    (r"\bservice\s+\S+\s+(start|stop|restart|reload)\b", "changes system services"),
    (r"\b(useradd|userdel|usermod|groupadd|groupdel|groupmod|adduser|deluser|passwd|chpasswd|visudo|chsh)\b",
     "changes users or rights"),
    (r"\b(ufw|iptables|ip6tables|nft|firewall-cmd|iptables-restore)\b", "changes the firewall"),
    (r"\bip\s+(-\S+\s+)*(link|addr|address|route|rule)\s+(add|del|delete|set|change|replace|flush)\b",
     "changes the network"),
    (r"\b(ifup|ifdown|nmcli|netplan\s+apply|resolvectl\s+(dns|domain))\b", "changes the network"),
    (r">>?\s*/etc/|\btee\b[^|;&]*\s/etc/|\bsed\b[^|;&]*\s-i[^|;&]*\s/etc/"
     r"|\b(cp|mv|install|ln|rm|chmod|chown|truncate|patch)\b[^|;&]*\s/etc/", "changes system configuration in /etc"),
    (r"\b(reboot|shutdown|poweroff|halt)\b|\binit\s+[06]\b", "restarts or stops the machine"),
    (r"\b(mount|umount|mkfs(\.\w+)?|fdisk|parted|sfdisk|wipefs|swapon|swapoff|lvcreate|lvremove|vgcreate)\b",
     "changes disks or mounts"),
    (r"\bcrontab\s+(-\S*\s+)*-[re]\b|>\s*/var/spool/cron", "changes scheduled jobs"),
    (r"\bsysctl\s+(-\S+\s+)*-w\b|\bmodprobe\b|\brmmod\b", "changes the kernel"),
    (r"\bhostnamectl\s+set|\btimedatectl\s+set", "changes machine settings"),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), why) for p, why in _PATTERNS]


def system_change(command: str) -> str:
    """Why the command changes the system ("" when it does not, as far as the patterns see)."""
    text = command or ""
    for pattern, why in _COMPILED:
        if pattern.search(text):
            return why
    return ""
