"""Инструменты безопасности кода (DevSecOps): утечки секретов и уязвимости.

`scan_secrets` — чистый Python, ищет ключи/токены/пароли (маскирует найденное).
`security_scan` — запускает Bandit (анализатор уязвимостей Python), если он есть.
Оба только читают код и идут без подтверждения.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys

from pydantic import BaseModel, Field

from core.security.paths import resolve_path
from core.security.secrets import scan_path
from core.tools.base import Tool, ToolContext, ToolResult

_MAX_SHOWN = 100


def _python_exe() -> str:
    for name in ("python", "python3", "py"):
        found = shutil.which(name)
        if found:
            return found
    return sys.executable


class ScanSecretsArgs(BaseModel):
    path: str = Field(default=".", description="Файл или папка для проверки (по умолчанию весь проект)")


class ScanSecretsTool(Tool):
    name = "scan_secrets"
    description = (
        "Ищет в коде утечки секретов: приватные ключи, API-ключи, токены (AWS, GitHub, Google, "
        "Slack, sk-…), пароли, зашитые прямо в исходники. Найденное показывает замаскированным. "
        "Запускай перед коммитом и перед публикацией — чтобы случайно не утёк ключ."
    )
    Args = ScanSecretsArgs
    category = "read"
    timeout = 60.0

    async def run(self, args: ScanSecretsArgs, ctx: ToolContext) -> ToolResult:
        base = resolve_path(args.path, settings=ctx.settings, must_exist=True)
        workspace = ctx.settings.workspace

        findings = await asyncio.to_thread(scan_path, base, workspace)
        if not findings:
            return ToolResult(content="✅ Секретов в коде не найдено.")

        lines = [f"⚠️ Возможные секреты: {len(findings)}. Проверь каждую строку:", ""]
        for f in findings[:_MAX_SHOWN]:
            lines.append(f"  {f.rel_path}:{f.line} — {f.kind}: {f.masked}")
        if len(findings) > _MAX_SHOWN:
            lines.append(f"  … и ещё {len(findings) - _MAX_SHOWN}.")
        lines.append("")
        lines.append(
            "Секреты не должны лежать в коде. Вынеси их в переменные окружения/.env "
            "(а .env добавь в .gitignore) и отзови те, что уже могли утечь."
        )
        return ToolResult(content="\n".join(lines), ok=False)


class SecurityScanArgs(BaseModel):
    path: str = Field(default=".", description="Файл или папка с Python-кодом для проверки")


class SecurityScanTool(Tool):
    name = "security_scan"
    description = (
        "Проверяет Python-код на уязвимости анализатором Bandit: SQL-инъекции, небезопасная "
        "десериализация (pickle/yaml.load), захардкоженные пароли, слабая криптография, "
        "инъекции команд, небезопасный subprocess. Запускай перед сдачей кода, работающего с "
        "данными, сетью или командами."
    )
    Args = SecurityScanArgs
    category = "read"
    timeout = 120.0

    async def run(self, args: SecurityScanArgs, ctx: ToolContext) -> ToolResult:
        target = resolve_path(args.path, settings=ctx.settings, must_exist=True)
        workspace = ctx.settings.workspace
        return await asyncio.to_thread(self._scan, target, workspace)

    def _scan(self, target, workspace) -> ToolResult:
        py = _python_exe()
        cmd = [py, "-m", "bandit", "-f", "json", "-q"]
        if target.is_dir():
            cmd += ["-r", str(target)]
        else:
            cmd.append(str(target))
        try:
            proc = subprocess.run(
                cmd, cwd=str(workspace), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=110, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ToolResult.fail(f"Bandit не запустился: {exc}")

        if "No module named bandit" in (proc.stderr or ""):
            return ToolResult.fail(
                "Bandit не установлен — анализ уязвимостей недоступен. Поставьте: pip install bandit"
            )
        try:
            data = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            return ToolResult.fail(f"Не удалось разобрать вывод Bandit:\n{(proc.stderr or '')[-300:]}")

        results = data.get("results", [])
        if not results:
            return ToolResult(content="✅ Bandit не нашёл уязвимостей.")

        order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        results.sort(key=lambda r: order.get(r.get("issue_severity", "LOW"), 3))
        icon = {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟡"}
        lines = [f"⚠️ Bandit нашёл проблем: {len(results)}", ""]
        for r in results[:_MAX_SHOWN]:
            sev = r.get("issue_severity", "LOW")
            rel = str(r.get("filename", "?")).replace("\\", "/")
            try:
                from pathlib import Path
                rel = str(Path(rel).resolve().relative_to(workspace.resolve())).replace("\\", "/")
            except (ValueError, OSError):
                pass
            lines.append(
                f"{icon.get(sev, '•')} {rel}:{r.get('line_number', '?')} "
                f"[{r.get('test_id', '')}] {r.get('issue_text', '')}"
            )
        return ToolResult(content="\n".join(lines), ok=False)
