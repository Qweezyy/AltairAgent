"""Прогон одной эталонной задачи через настоящий цикл агента.

Задача описывается промптом и функцией-проверкой, которая смотрит на итог
(что ответил агент, какие инструменты вызвал, спрашивал ли подтверждения) и
решает, прошёл эталон или нет. Проверяем свойства ответа, а не точный текст:
модель недетерминирована, но «в ответе есть число 9» — стабильно.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.agent.run_options import RunOptions
from core.agent.runner import AgentRunner
from core.llm.base import LLMClient
from core.settings import Settings
from core.tools import build_default_registry


@dataclass
class CaseRun:
    """Всё, что случилось за прогон задачи — вход для проверки."""

    final_text: str = ""
    ok: bool = True
    tools: list[str] = field(default_factory=list)
    approvals: list[Any] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    steps: int = 0
    tokens: int = 0
    cost_usd: float = 0.0

    def used(self, tool: str) -> bool:
        return tool in self.tools


#: Проверка: получает CaseRun, возвращает (прошло, пояснение).
CheckFn = Callable[[CaseRun], "tuple[bool, str]"]


@dataclass
class EvalCase:
    """Одна эталонная задача."""

    name: str
    prompt: str
    check: CheckFn
    #: Нужна настоящая модель (иначе — сценарная фабрика llm_factory).
    live: bool = False
    approval_mode: str = "bypass"
    options: dict | None = None
    llm_factory: Callable[[], LLMClient] | None = None
    #: Настройки, которые нужно выставить именно для этой задачи (напр. бюджет).
    settings_overrides: dict | None = None
    #: Файлы, создаваемые в рабочей папке до прогона: {относительный_путь: текст}.
    workspace_files: dict | None = None


@dataclass
class EvalResult:
    name: str
    passed: bool
    detail: str = ""
    duration_s: float = 0.0
    tokens: int = 0
    cost_usd: float = 0.0
    error: str = ""


async def run_case(
    case: EvalCase,
    settings: Settings,
    *,
    live_llm_factory: Callable[[], LLMClient] | None = None,
) -> EvalResult:
    """Гоняет одну задачу и применяет её проверку."""
    started = time.perf_counter()

    if case.llm_factory is not None:
        llm = case.llm_factory()
    elif case.live and live_llm_factory is not None:
        llm = live_llm_factory()
    else:
        return EvalResult(
            name=case.name, passed=False, detail="", error="нет модели для задачи (нужен --live с ключом)"
        )

    run = CaseRun()

    async def emitter(event: Any) -> None:
        etype = getattr(event, "type", "")
        if etype == "tool.started":
            run.tools.append(event.name)
        elif etype == "log" and getattr(event, "level", "") == "warning":
            run.warnings.append(event.text)
        elif etype == "run.finished":
            run.final_text = event.text
            run.steps = event.steps
            run.tokens = (event.usage or {}).get("total_tokens", 0)
            run.cost_usd = getattr(event, "cost_usd", 0.0)
        elif etype == "run.failed":
            run.ok = False
            run.final_text = event.message

    async def approver(request: Any) -> bool:
        # Эталон фиксирует запрос подтверждения и по умолчанию отказывает:
        # для проверок безопасности отказ — правильный исход.
        run.approvals.append(request)
        return False

    settings.approval_mode = case.approval_mode  # type: ignore[assignment]
    for key, value in (case.settings_overrides or {}).items():
        setattr(settings, key, value)
    for rel, content in (case.workspace_files or {}).items():
        target = settings.workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    runner = AgentRunner(
        llm=llm,
        registry=build_default_registry(),
        settings=settings,
        emitter=emitter,
        approver=approver,
    )
    options = RunOptions.from_message(case.options) if case.options else RunOptions()

    try:
        result = await runner.run(case.prompt, options)
        run.ok = result.ok
        if not run.final_text:
            run.final_text = result.text
    except Exception as exc:  # noqa: BLE001 - эталон не должен падать сам
        return EvalResult(
            name=case.name,
            passed=False,
            detail="",
            duration_s=time.perf_counter() - started,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        await llm.aclose()

    try:
        passed, detail = case.check(run)
    except Exception as exc:  # noqa: BLE001
        passed, detail = False, f"проверка упала: {type(exc).__name__}: {exc}"

    return EvalResult(
        name=case.name,
        passed=passed,
        detail=detail,
        duration_s=time.perf_counter() - started,
        tokens=run.tokens,
        cost_usd=run.cost_usd,
    )


def format_scorecard(results: list[EvalResult]) -> str:
    """Табличка итогов для консоли."""
    passed = sum(1 for r in results if r.passed)
    lines = [f"Эталоны: {passed}/{len(results)} пройдено", ""]
    for r in results:
        # ASCII-метки: консоль Windows (cp1251) не переваривает галочки.
        mark = "[OK]  " if r.passed else "[FAIL]"
        cost = f" · ${r.cost_usd:.4f}" if r.cost_usd else ""
        tokens = f" · {r.tokens} ток." if r.tokens else ""
        lines.append(f"  {mark} {r.name} ({r.duration_s:.1f} с{tokens}{cost})")
        note = r.error or r.detail
        if note and not r.passed:
            lines.append(f"      {note}")
    return "\n".join(lines)
