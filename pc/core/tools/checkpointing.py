"""Общий хелпер: снять снимок файла перед изменением и сообщить интерфейсу.

Вынесено отдельно, чтобы все изменяющие инструменты (write/edit/patch/delete)
делали снимок одинаково — забыть один инструмент означало бы, что часть правок
нельзя откатить, и пользователь не поймёт почему.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from core.events import CheckpointCreated
from core.security.paths import safe_relpath
from core.tools.base import ToolContext


async def snapshot_before_change(ctx: ToolContext, abs_path: Path, op: str) -> None:
    """Снимает состояние файла до изменения (если включены снимки)."""
    if ctx.checkpoints is None:
        return
    rel_path = safe_relpath(abs_path, ctx.settings)
    checkpoint = await asyncio.to_thread(ctx.checkpoints.snapshot, abs_path, rel_path, op)
    await ctx.emitter(
        CheckpointCreated(
            id=checkpoint.id,
            path=checkpoint.path,
            op=checkpoint.op,
            recoverable=checkpoint.recoverable,
        )
    )
