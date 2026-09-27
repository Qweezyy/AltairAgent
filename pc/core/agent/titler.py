"""A chat's title, written by the model in parallel with its first answer.

A separate tiny request instead of asking the main answer to start with a title line: the
title then never leaks into the answer, a slow or failing title never delays or breaks the
answer, and it is usually ready before the answer starts streaming.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any

from core.logging_setup import get_logger

logger = get_logger("titler")

_PROMPT = (
    "Write a short title for a chat that starts with the user's message below. "
    "At most 6 words, in the same language as the message, no quotes, no trailing period, "
    "no emoji. Reply with the title only.\n\nMessage:\n{task}"
)
MAX_LEN = 60


def clean_title(raw: str) -> str:
    """The first line, without quotes, labels like "Title:", markdown or a trailing period."""
    text = (raw or "").strip().splitlines()[0] if (raw or "").strip() else ""
    text = re.sub(r"^\s*(title|заголовок|название)\s*[:：]\s*", "", text, flags=re.IGNORECASE)
    quotes = "\"'«»“”`*#"
    # Twice: models write both '"Title".' and '"Title."'.
    for _ in range(2):
        text = re.sub(r"[.。]+$", "", text.strip().strip(quotes)).strip()
    if len(text) > MAX_LEN:
        text = text[: MAX_LEN - 1].rstrip() + "…"
    return text


def fallback_title(task: str) -> str:
    """The old behaviour when the model gives no title: the first line of the message."""
    first = (task or "").strip().splitlines()[0] if (task or "").strip() else ""
    return first[:50] + ("..." if len(first) > 50 else "")


async def generate_title(task: str, build_client: Callable[..., Any],
                         client_kwargs: dict[str, Any] | list[dict[str, Any]],
                         timeout: float = 15.0) -> str | None:
    """Ask the model for a title; None when it fails (the caller falls back).

    Several candidates (dicts of client kwargs) are tried in turn: a routing tier may have a
    key that is not allowed for its model, and the title should still come from some model.
    """
    candidates = [client_kwargs] if isinstance(client_kwargs, dict) else list(client_kwargs)
    for kwargs in candidates:
        title = await _ask(task, build_client, kwargs, timeout)
        if title:
            return title
    return None


async def _ask(task: str, build_client: Callable[..., Any], client_kwargs: dict[str, Any],
               timeout: float) -> str | None:
    try:
        client = build_client(**client_kwargs)
    except Exception as exc:  # noqa: BLE001 - a title must never break the run
        logger.info("title: no client for %s (%s)", client_kwargs.get("model"), exc)
        return None
    try:
        prompt = [{"role": "user", "content": _PROMPT.format(task=(task or "").strip()[:1500])}]
        # Reasoning models spend tokens on thinking first: a tight cap would leave no title.
        turn = await asyncio.wait_for(client.complete(prompt, max_tokens=400), timeout=timeout)
        return clean_title(turn.content or "") or None
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.info("title: %s did not answer (%s: %s)", client_kwargs.get("model"), type(exc).__name__,
                    str(exc)[:200])
        return None
    finally:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            logger.debug("title client close failed", exc_info=True)
