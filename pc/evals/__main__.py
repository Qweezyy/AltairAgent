"""Запуск эталонов: python -m evals [--live] [--case подстрока].

Без --live гоняются только детерминированные инварианты (без обращения к
модели). С --live добавляются живые проверки способностей — нужен ключ API.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from core.settings import get_settings
from evals.cases import deterministic_cases, live_cases
from evals.harness import EvalCase, format_scorecard, run_case


def _force_utf8_output() -> None:
    """Консоль Windows часто в cp1251 — иначе кириллица роняет печать отчёта."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


async def _amain(cases: list[EvalCase], live: bool) -> int:
    settings = get_settings()

    live_factory = None
    if live:
        from core.llm import build_llm_client

        if not settings.openrouter_api_key:
            print("Для живых эталонов нужен ключ API (OPENROUTER_API_KEY).")
            return 2
        live_factory = build_llm_client

    results = []
    for case in cases:
        # Каждый эталон — на своих настройках: копируем, чтобы не протекали.
        case_settings = settings.model_copy(deep=True)
        print(f"… {case.name}", flush=True)
        results.append(await run_case(case, case_settings, live_llm_factory=live_factory))

    print("\n" + format_scorecard(results))
    return 0 if all(r.passed for r in results) else 1


def main() -> int:
    _force_utf8_output()
    parser = argparse.ArgumentParser(description="Эталоны поведения агента")
    parser.add_argument("--live", action="store_true", help="добавить живые проверки (нужен ключ)")
    parser.add_argument("--case", default="", help="запустить только задачи с этой подстрокой в имени")
    args = parser.parse_args()

    cases = list(deterministic_cases())
    if args.live:
        cases += list(live_cases())
    if args.case:
        cases = [c for c in cases if args.case.lower() in c.name.lower()]

    if not cases:
        print("Нет задач под фильтр.")
        return 2

    return asyncio.run(_amain(cases, args.live))


if __name__ == "__main__":
    sys.exit(main())
