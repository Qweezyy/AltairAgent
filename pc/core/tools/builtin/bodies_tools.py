"""`bodies`: the machines this agent works on — this PC and the servers — and which fits a task.

The machine tools run on any of them with their `body` argument (core/bodies_routing.py); this
tool tells the agent what each body is and how busy it is right now, and ranks them for a task
(core/bodies_planner.py).
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.bodies_planner import Needs, name_of, rank
from core.tools.base import Tool, ToolContext


class BodiesArgs(BaseModel):
    action: Literal["list", "pick"] = Field(
        default="list", description="list: every body with its state and load; pick: rank them for a task.")
    gpu: bool = Field(default=False, description="pick: the task needs a GPU")
    docker: bool = Field(default=False, description="pick: the task needs Docker")
    min_memory_gb: float = Field(default=0.0, description="pick: memory the task needs, GB")
    arch: str = Field(default="", description="pick: x64 or arm64, if it matters")
    long_running: bool = Field(default=False, description="pick: hours of work, or meant to go on while the PC is off")
    data_on: str = Field(default="", description="pick: the body that already has the files or repository")
    label: str = Field(default="", description="pick: an owner's label that fits, e.g. 'builds'")


async def collect(ctx: ToolContext) -> list[dict[str, Any]]:
    """This PC and every server, as /api/bodies has them."""
    from core.bodies import get_gate
    from core.bodies_routing import router
    from core.body_labels import BodyLabels
    from core.body_status import status

    settings = ctx.settings
    labels = await asyncio.to_thread(BodyLabels(settings.data_dir).all)
    card = await asyncio.to_thread(lambda: get_gate(settings.data_dir / "identity", settings.body_kind).identity.card())
    me = await asyncio.to_thread(status, card, settings.data_dir)
    bodies: list[dict[str, Any]] = [{"id": card["id"], "self": True, "name": "pc", "state": "online",
                                     "agent": "ok", "status": me, "labels": labels.get(card["id"], [])}]
    for t in router.servers():
        r = t.record
        live = t.public()
        bodies.append({"id": r.id, "self": False, "name": r.name or r.host, "host": r.host, "mode": r.mode,
                       "arch": r.arch, "labels": labels.get(r.id, []), "state": live["state"],
                       "agent": live["agent"], "error": live["error"], "status": live["status"] or {}})
    return bodies


def _line(b: dict[str, Any]) -> str:
    st = b.get("status") or {}
    ld = st.get("load") or {}
    parts = [name_of(b) + (" (this machine)" if b.get("self") else "")]
    if not b.get("self"):
        state = b.get("state")
        parts.append("online" if state == "online" and b.get("agent") != "down"
                     else "agent not answering" if state == "online" else f"{state}: {b.get('error') or ''}".strip())
    facts = [st.get("system") or "", st.get("arch") or b.get("arch") or ""]
    if st.get("cpus"):
        facts.append(f"{st['cpus']} CPU")
    if st.get("mem_mb"):
        facts.append(f"{st['mem_mb'] / 1024:.1f} GB RAM")
    facts.append("GPU: " + ", ".join(st["gpus"]) if st.get("gpus") else "no GPU")
    facts.append("Docker" if st.get("docker") else "no Docker")
    parts.append(", ".join(f for f in facts if f))
    if ld:
        parts.append(f"load: CPU {ld.get('cpu_pct', 0):.0f}%, RAM used {ld.get('mem_used_mb', 0) / 1024:.1f} GB, "
                     f"disk free {ld.get('disk_free_mb', 0) / 1024:.1f} GB, tasks {ld.get('tasks', 0)}")
    if b.get("mode"):
        parts.append(f"mode {b['mode']}")
    if b.get("labels"):
        parts.append("labels: " + ", ".join(b["labels"]))
    return "- " + " · ".join(parts)


class BodiesTool(Tool):
    name = "bodies"
    description = (
        "The machines you work on: this PC and the user's servers, with what each has (CPU, RAM, GPU, "
        "Docker) and how busy it is now. action='pick' ranks them for a task with the reasons. Run "
        "machine tools on a server by passing its name as their `body` argument."
    )
    Args = BodiesArgs
    category = "read"
    timeout = 30.0

    async def run(self, args: BodiesArgs, ctx: ToolContext) -> str:
        bodies = await collect(ctx)
        if args.action == "list":
            if len(bodies) == 1:
                return "Only this machine: no servers are added (Settings → Servers)."
            return "\n".join(_line(b) for b in bodies)
        needs = Needs(gpu=args.gpu, docker=args.docker, min_memory_gb=args.min_memory_gb, arch=args.arch,
                      long_running=args.long_running, data_on=args.data_on, label=args.label)
        lines = []
        for i, c in enumerate(rank(bodies, needs), 1):
            verdict = f"{i}. {c.name}" if c.fits else f"✗ {c.name}"
            lines.append(f"{verdict} — {'; '.join(c.reasons) or 'fits'}")
        best = next((c for c in rank(bodies, needs) if c.fits), None)
        lines.append("" if best is None else f"Best: {best.name}. Tell the user in one line where and why.")
        if best is None:
            lines.append("No body fits: tell the user what is missing.")
        return "\n".join(line for line in lines if line)
