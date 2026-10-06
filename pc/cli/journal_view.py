"""The Journal in the terminal: one line per record, the same wording as the app window."""

from __future__ import annotations

import time
from typing import Any

from rich.text import Text

from cli import theme, tools_view
from cli.texts import Texts


def line(record: dict[str, Any], t: Texts, *, show_chat: bool = True) -> Text:
    d = record.get("data") or {}
    kind = str(record.get("kind") or "")
    stamp = time.strftime("%d.%m %H:%M:%S", time.localtime(float(record.get("ts") or 0)))
    mark, style, text = "·", theme.MUTED, kind
    if kind in ("user", "user.steer"):
        mark, style, text = theme.PROMPT, theme.INK, str(d.get("text") or "")
    elif kind == "run.started":
        mark, text = "▶", t("jr.started", task=d.get("task") or "")
    elif kind == "run.finished":
        mark, style = "✓", theme.OK
        text = t("jr.finished", sec=round((d.get("duration_ms") or 0) / 1000, 1), steps=d.get("steps") or 0)
        if d.get("cost_usd"):
            text += f" · ${float(d['cost_usd']):.4f}"
    elif kind == "run.failed":
        mark, style, text = "✗", theme.ERR, t("jr.failed", message=d.get("message") or "")
    elif kind == "run.cancelled":
        mark, style, text = "■", theme.WARN, t("jr.cancelled")
    elif kind == "tool":
        label, hint = tools_view.label_of(str(d.get("name") or ""), d.get("args") or {})
        mark, style = ("●", theme.MUTED) if d.get("ok") else ("✗", theme.ERR)
        text = f"{label}({hint})" if hint else label
    elif kind == "approval":
        approved = bool(d.get("approved"))
        mark, style = ("✓", theme.OK) if approved else ("✗", theme.ERR)
        text = t("jr.approval." + str(d.get("scope") or "once"), name=d.get("name") or "")
    elif kind == "file.restored":
        mark, style, text = "↺", theme.WARN, t("jr.restored", path=d.get("path") or "")
    elif kind == "run.rollback":
        mark, style, text = "↺", theme.WARN, t("jr.rollback", n=len(d.get("restored") or []))
    elif kind == "update.ready":
        mark, style, text = "⬆", theme.OK, t("jr.updated", version=d.get("version") or "")
    elif kind == "update.failed":
        mark, style, text = "⬆", theme.ERR, t("jr.update_failed", error=d.get("error") or "")
    out = Text(f"{stamp}  ", style=theme.FAINT)
    out.append(f"{mark} ", style=style)
    out.append(tools_view.one_line(text, 160), style=style if style != theme.MUTED else "")
    if show_chat and record.get("chat"):
        out.append(f"  {str(record['chat'])[:6]}", style=theme.FAINT)
    return out
