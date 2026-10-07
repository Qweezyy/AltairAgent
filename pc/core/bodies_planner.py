"""Where to run something: the bodies ranked for a task's needs, each with its reasons.

The order of what counts (docs/PLAN_0.3.0.md, stage 3):
  1. hard needs — a GPU, Docker, the architecture, enough memory: a body without them is out;
  2. where the data is — the body that already has the folder or repository;
  3. long work, "while I sleep" — a server keeps going when the PC is off;
  4. load — the less busy body;
  5. the owner's labels ("builds", "prod") and the body the chat is pinned to.

Pure: takes the bodies as /api/bodies describes them (status with facts and load), so the agent's
`bodies` tool and the tests use the same function.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Needs:
    gpu: bool = False
    docker: bool = False
    min_memory_gb: float = 0.0
    arch: str = ""                       # "x64" / "arm64"
    long_running: bool = False           # hours, or "while I sleep"
    data_on: str = ""                    # the body that has the data (name), if known
    label: str = ""                      # an owner's label that fits ("builds")
    pinned: str = ""                     # the body the chat is pinned to


@dataclass
class Choice:
    name: str
    score: float
    fits: bool
    reasons: list[str] = field(default_factory=list)
    body: dict[str, Any] = field(default_factory=dict)


_ARCH = {"x86_64": "x64", "amd64": "x64", "x64": "x64", "aarch64": "arm64", "arm64": "arm64"}


def name_of(body: dict[str, Any]) -> str:
    return "pc" if body.get("self") else str(body.get("name") or body.get("host") or body.get("id"))


def rank(bodies: list[dict[str, Any]], needs: Needs) -> list[Choice]:
    out: list[Choice] = []
    for b in bodies:
        st = b.get("status") or {}
        load = st.get("load") or {}
        name = name_of(b)
        names = {name.lower(), str(b.get("name") or "").lower(), str(b.get("id") or "").lower()} - {""}
        c = Choice(name=name, score=0.0, fits=True, body=b)
        online = b.get("self") or (b.get("state") == "online" and b.get("agent") != "down")
        if not online:
            c.fits = False
            c.reasons.append("not connected")
        if needs.gpu and not st.get("gpus"):
            c.fits = False
            c.reasons.append("no GPU")
        if needs.docker and not st.get("docker"):
            c.fits = False
            c.reasons.append("no Docker")
        mem_gb = (st.get("mem_mb") or 0) / 1024
        if needs.min_memory_gb and mem_gb and mem_gb < needs.min_memory_gb:
            c.fits = False
            c.reasons.append(f"{mem_gb:.1f} GB RAM < {needs.min_memory_gb:g} GB")
        if needs.arch and _ARCH.get(str(st.get("arch") or b.get("arch") or "").lower()) != _ARCH.get(needs.arch.lower(), needs.arch):
            c.fits = False
            c.reasons.append(f"not {needs.arch}")
        if not c.fits:
            c.score = -1000.0
            out.append(c)
            continue
        if needs.gpu:
            c.reasons.append("GPU: " + ", ".join(st.get("gpus") or []))
        if needs.pinned and needs.pinned.lower() in names:
            c.score += 100
            c.reasons.append("the chat is pinned to it")
        if needs.data_on and needs.data_on.lower() in names:
            c.score += 40
            c.reasons.append("the data is there")
        if needs.long_running:
            if b.get("self"):
                c.score -= 15
                c.reasons.append("the PC may be switched off")
            else:
                c.score += 25
                c.reasons.append("keeps running when the PC is off")
        cpu = float(load.get("cpu_pct") or 0)
        tasks = int(load.get("tasks") or 0)
        c.score -= cpu / 10 + tasks * 5
        if cpu >= 70 or tasks:
            c.reasons.append(f"busy: CPU {cpu:.0f}%, {tasks} task(s)")
        mem_free = (st.get("mem_mb") or 0) - (load.get("mem_used_mb") or 0)
        c.score += min(mem_free / 1024, 16) / 2
        labels = [str(x).lower() for x in b.get("labels") or []]
        if needs.label and needs.label.lower() in labels:
            c.score += 20
            c.reasons.append(f"labelled '{needs.label}'")
        if not b.get("self") and not needs.long_running and not needs.data_on:
            # A short task with nothing pointing elsewhere: here is the nearest.
            c.score -= 5
        out.append(c)
    out.sort(key=lambda c: (c.fits, c.score), reverse=True)
    return out
