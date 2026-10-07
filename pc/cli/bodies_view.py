"""The bodies in the terminal: `altair --body NAME` and `/body` show a server's agent instead of this
PC's — through this PC's backend and the server's tunnel (server/bodies.py), like the window."""

from __future__ import annotations

from typing import Any

import httpx
from rich.table import Table
from rich.text import Text

from cli.texts import Texts


async def list_bodies(root: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.get(f"{root}/api/bodies")
        r.raise_for_status()
        return r.json().get("bodies", [])


def resolve(bodies: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    """By id, name or address; "pc" (or "local") is this PC. Exact first, then a unique prefix."""
    q = query.strip().lower()
    if q in ("pc", "local", "this", "пк"):
        return next((b for b in bodies if b.get("self")), None)
    keys = lambda b: {str(b.get(k) or "").lower() for k in ("id", "name", "host", "body_id")} - {""}  # noqa: E731
    exact = [b for b in bodies if q in keys(b)]
    if len(exact) == 1:
        return exact[0]
    near = [b for b in bodies if any(k.startswith(q) for k in keys(b))]
    return near[0] if len(near) == 1 else None


def live(b: dict[str, Any]) -> str:
    if b.get("state") == "online" and b.get("agent") != "down":
        return "on"
    return "wait" if b.get("state") in ("online", "connecting") else "off"


def table(bodies: list[dict[str, Any]], t: Texts, current: str) -> Table:
    grid = Table(box=None, pad_edge=False, header_style="dim")
    for col in ("", t("bd.c.name"), t("bd.c.state"), t("bd.c.load"), t("bd.c.labels")):
        grid.add_column(col)
    for b in bodies:
        mark = "●" if (b["id"] == current or (not current and b.get("self"))) else " "
        state = live(b)
        style = {"on": "green", "wait": "yellow", "off": "red"}[state]
        load = ""
        status = b.get("status") or {}
        if status.get("load"):
            ld = status["load"]
            load = f"CPU {round(ld.get('cpu_pct', 0))}% · RAM {ld.get('mem_used_mb', 0) // 1024}/{status.get('mem_mb', 0) // 1024} GB"
        name = t("bd.this_pc") if b.get("self") else (b.get("name") or b.get("host") or b["id"])
        grid.add_row(Text(mark, style="bold"), name, Text(t(f"bd.{state}"), style=style), load,
                     ", ".join(b.get("labels") or []))
    return grid
