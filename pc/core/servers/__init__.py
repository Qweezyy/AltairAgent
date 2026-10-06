"""Servers as bodies of the agent (0.3.0): adding one by its SSH login, installing the agent
there by itself, keeping the list of servers this PC manages.

    remote.py     — commands and file uploads over SSH (asyncssh), host key pinned on first use
    preflight.py  — what the server is: system, architecture, memory, disk, sudo, Docker, network
    install.py    — the installation, step by step, with progress; uninstalling
    registry.py   — servers.json: the servers this PC installed and how to reach them
"""

from core.servers.registry import ServerRecord, ServerStore

__all__ = ["ServerRecord", "ServerStore"]
