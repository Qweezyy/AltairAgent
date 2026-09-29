"""Commands started by the agent: they start while the backend's stdin is busy, and the stop
button ends them with everything they started.

Two regressions from real use: launched by the desktop shell, the backend's stdin is the
shell's pipe with a thread reading it, and on Windows every child inheriting that stdin hung
(execute_command never ran even `Write-Output ping`); and a stopped command killed only its
PowerShell, leaving what it had started running.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.utils.proc import powershell_argv, run_process

windows_only = pytest.mark.skipif(os.name != "nt", reason="the hang and the tree kill are Windows specifics")

CHILD = r'''
import os, subprocess, sys, threading, time
sys.path.insert(0, {pc!r})
if {patch}:
    from core.utils.proc import install_devnull_stdin_default
    install_devnull_stdin_default()
threading.Thread(target=lambda: os.read(0, 1024), daemon=True).start()   # the shell watcher
time.sleep(0.5)
try:
    subprocess.run([sys.executable, "-c", "print(1)"], capture_output=True, timeout=6, creationflags=0x08000000)
    result = "ok"
except subprocess.TimeoutExpired:
    result = "hung"
open({out!r}, "w").write(result)
os._exit(0)
'''


def _windowed_child(tmp_path: Path, patch: bool) -> str:
    """Runs CHILD in a windowed Python (like the packaged backend) with a pipe for stdin."""
    out = tmp_path / f"result-{patch}.txt"
    script = tmp_path / f"child-{patch}.py"
    script.write_text(CHILD.format(pc=str(Path(__file__).resolve().parents[1]), patch=patch, out=str(out)),
                      encoding="utf-8")
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    proc = subprocess.Popen([str(pythonw), str(script)], stdin=subprocess.PIPE)
    try:
        proc.wait(timeout=40)
    finally:
        if proc.poll() is None:
            proc.kill()
    return out.read_text(encoding="utf-8") if out.exists() else "no result"


@windows_only
def test_children_start_while_the_backends_stdin_is_being_read(tmp_path):
    assert _windowed_child(tmp_path, patch=True) == "ok"


@windows_only
async def test_stopping_a_command_ends_what_it_started(tmp_path):
    marker = tmp_path / "grandchild.pid"
    code = f"import os,time; open(r'{marker}','w').write(str(os.getpid())); time.sleep(60)"
    command = f"& '{sys.executable}' -c \"{code}\""
    task = asyncio.create_task(run_process(powershell_argv(command), timeout=90))
    for _ in range(100):
        if marker.exists() and marker.read_text().strip():
            break
        await asyncio.sleep(0.1)
    pid = int(marker.read_text())
    task.cancel()                                            # the stop button
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(1.0)
    listing = await asyncio.to_thread(
        subprocess.run, ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True)
    alive = listing.stdout
    assert str(pid) not in alive, "the command's child outlived the stop"
