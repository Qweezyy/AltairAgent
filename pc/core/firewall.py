"""Windows Firewall for the phone bridge: is the app reachable from the network, and fix it.

Windows 11 marks a new network "Public" by default, and an unsigned app gets no inbound rule
unless the user answers the firewall prompt the first time it listens — a missed or cancelled
prompt even leaves a *block* rule. Then the phone just times out with no hint why. So the
pairing screen checks the rules for this program and offers one button that (after the usual
UAC confirmation) removes our block rules and allows inbound connections — only from the local
subnet and the Tailscale range, never from the whole internet.

The rule is tied to the program, not the port: the desktop shell picks a free port when 8137
is busy, and a port rule would then silently stop matching.
"""

from __future__ import annotations

import json
import os
import sys

from core.logging_setup import get_logger
from core.utils.proc import powershell_argv, run_process

logger = get_logger("firewall")

RULE_NAME = "Altair phone bridge"
#: Local network + Tailscale (100.64.0.0/10) — the places a paired phone connects from.
REMOTE_SCOPE = "LocalSubnet,100.64.0.0/10"


def program_path() -> str:
    """The executable that listens: LocalAIAgent.exe when packaged, python.exe in development."""
    return os.path.abspath(sys.executable)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


async def status() -> dict[str, object]:
    """{"supported", "allowed", "blocked", "profile"}: can the phone get through?"""
    if os.name != "nt":
        return {"supported": False, "allowed": True, "blocked": False, "profile": ""}
    program = _ps_quote(program_path())
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        f"$r = Get-NetFirewallApplicationFilter -Program {program} | Get-NetFirewallRule |"
        " Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' };"
        "$p = (Get-NetConnectionProfile | Select-Object -First 1).NetworkCategory;"
        "@{allow=@($r | Where-Object Action -eq 'Allow').Count;"
        " block=@($r | Where-Object Action -eq 'Block').Count; profile=\"$p\"} | ConvertTo-Json -Compress"
    )
    result = await run_process(powershell_argv(script), timeout=30.0)
    try:
        data = json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError:
        logger.warning("firewall status unreadable: %s", (result.stderr or result.stdout)[:200])
        return {"supported": True, "allowed": False, "blocked": False, "profile": "", "unknown": True}
    blocked = int(data.get("block") or 0) > 0
    return {
        "supported": True,
        "allowed": int(data.get("allow") or 0) > 0 and not blocked,
        "blocked": blocked,
        "profile": str(data.get("profile") or ""),
    }


async def allow() -> bool:
    """Ask Windows (UAC) to remove our block rules and add the allow rule. True when it worked."""
    if os.name != "nt":
        return True
    program = _ps_quote(program_path())
    name = _ps_quote(RULE_NAME)
    inner = (
        f"Get-NetFirewallApplicationFilter -Program {program} | Get-NetFirewallRule |"
        " Where-Object { $_.Direction -eq 'Inbound' -and $_.Action -eq 'Block' } | Remove-NetFirewallRule;"
        f"Get-NetFirewallRule -DisplayName {name} -ErrorAction SilentlyContinue | Remove-NetFirewallRule;"
        f"New-NetFirewallRule -DisplayName {name} -Direction Inbound -Action Allow -Program {program}"
        f" -Protocol TCP -RemoteAddress {REMOTE_SCOPE} -Profile Any | Out-Null"
    )
    # The elevated half runs in its own PowerShell started with -Verb RunAs: that is what shows
    # the UAC prompt. -Wait so we can re-check the rules right after.
    launcher = (
        "$ErrorActionPreference='Stop';"
        "try { Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "
        f"@('-NoProfile','-ExecutionPolicy','Bypass','-Command',{_ps_quote(inner)}); 'ok' }}"
        " catch { 'cancelled' }"
    )
    result = await run_process(powershell_argv(launcher), timeout=180.0)
    if "cancelled" in result.stdout:
        logger.info("firewall change cancelled by the user")
        return False
    return bool((await status()).get("allowed"))
