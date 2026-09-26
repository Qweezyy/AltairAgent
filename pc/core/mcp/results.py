"""Shared by the stdio and HTTP MCP clients: how a tool result reaches the model."""

from __future__ import annotations

import json
from typing import Any


def format_call_result(result: dict[str, Any]) -> tuple[str, bool]:
    """MCP `tools/call` result → (text for the model, success)."""
    chunks: list[str] = []
    for block in result.get("content") or []:
        if block.get("type") == "text":
            chunks.append(block.get("text", ""))
        else:
            chunks.append(json.dumps(block, ensure_ascii=False))
    structured = result.get("structuredContent")
    if not chunks and structured is not None:
        chunks.append(json.dumps(structured, ensure_ascii=False))
    text = "\n".join(c for c in chunks if c) or "Done (empty result)."
    return text, not result.get("isError", False)
