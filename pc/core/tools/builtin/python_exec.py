"""Выполнение Python-кода в отдельном процессе.

Важно: код выполняется НЕ через exec() в процессе агента. Иначе любая
ошибка в сгенерированном коде (бесконечный цикл, sys.exit, падение
интерпретатора) убивала бы весь сервер.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from core.i18n import tr
from core.tools.base import Tool, ToolContext, ToolResult
from core.utils.proc import python_argv, run_process


class RunPythonArgs(BaseModel):
    code: str = Field(description="Python code; print() the results you need")
    timeout: float = Field(default=60.0, ge=1, le=600, description="Time limit in seconds")


class RunPythonTool(Tool):
    name = "run_python"
    description = (
        "Runs Python code in a separate process and returns stdout/stderr. Use it for calculations, "
        "data processing and checking hypotheses. State is not kept between calls, so write "
        "self-contained scripts. Filtering or aggregating data here and printing only the result "
        "keeps large intermediate data out of the conversation."
    )
    Args = RunPythonArgs
    category = "execute"
    dangerous = True
    timeout = None  # таймаут задаётся аргументом и контролируется процессом

    def approval_reason(self, args: RunPythonArgs) -> str:  # type: ignore[override]
        preview = args.code.strip().splitlines()[:5]
        return tr("appr.python", code="\n".join(preview))

    async def run(self, args: RunPythonArgs, ctx: ToolContext) -> ToolResult:
        tmp_dir = ctx.settings.workspace / ".tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        script: Path = tmp_dir / f"snippet_{uuid.uuid4().hex[:12]}.py"

        await asyncio.to_thread(script.write_text, args.code, "utf-8")
        try:
            from core.secrets_store import load_env

            result = await run_process(
                python_argv(script, ctx.settings.workspace),
                cwd=ctx.settings.workspace,
                timeout=args.timeout,
                # Секреты из .env доступны коду; субагенту (F8) — нет.
                env={} if ctx.scratch.get("no_secrets") else load_env(ctx.settings.workspace),
            )
        finally:
            await asyncio.to_thread(script.unlink, True)

        parts: list[str] = []
        if result.stdout.strip():
            parts.append(f"stdout:\n{result.stdout.strip()}")
        if result.stderr.strip():
            parts.append(f"stderr:\n{result.stderr.strip()}")
        if not parts:
            parts.append("Код выполнен, вывод пуст. Не забудь print() для результата.")
        if result.returncode != 0 and not result.timed_out:
            parts.insert(0, f"Процесс завершился с кодом {result.returncode}.")

        return ToolResult(content="\n\n".join(parts), ok=result.ok)
