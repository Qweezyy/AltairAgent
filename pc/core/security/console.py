"""Подтверждение действий в консоли (режимы --cli и --repl).

Без этого консольные режимы молча разрешали бы всё, игнорируя APPROVAL_MODE,
и поведение агента в окне и в терминале отличалось бы.
"""

from __future__ import annotations

import asyncio
import json

from core.security.approval import ApprovalRequest


async def console_approver(request: ApprovalRequest) -> bool:
    """Спрашивает разрешение в терминале. Пустой ответ = отказ."""
    args = json.dumps(request.args, ensure_ascii=False, indent=2)[:1500]
    prompt = (
        f"\n--- ТРЕБУЕТСЯ ПОДТВЕРЖДЕНИЕ ---\n"
        f"Инструмент: {request.name}\n"
        f"{request.reason}\n"
        f"Аргументы:\n{args}\n"
        f"Разрешить? [y/N]: "
    )
    try:
        answer = await asyncio.to_thread(input, prompt)
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in {"y", "yes", "д", "да"}
