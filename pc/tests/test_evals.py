"""Детерминированные эталоны поведения — гоняются в CI без обращения к модели.

Живые эталоны (способности) требуют ключа и запускаются отдельно:
`python -m evals --live`.
"""

from __future__ import annotations

import pytest

from evals.cases import deterministic_cases, live_cases
from evals.harness import format_scorecard, run_case


@pytest.mark.parametrize("case", deterministic_cases(), ids=lambda c: c.name)
async def test_deterministic_eval_passes(case, settings):
    result = await run_case(case, settings)
    assert result.passed, f"{case.name}: {result.error or result.detail}"


def test_every_case_has_a_check():
    """Задача без проверки бессмысленна — ловим это до прогона."""
    for case in deterministic_cases() + live_cases():
        assert callable(case.check), f"у эталона «{case.name}» нет проверки"


def test_scorecard_formats_pass_and_fail():
    from evals.harness import EvalResult

    card = format_scorecard(
        [
            EvalResult(name="ок", passed=True, duration_s=1.2),
            EvalResult(name="провал", passed=False, detail="что-то не так", duration_s=0.5),
        ]
    )
    assert "1/2 пройдено" in card
    assert "[OK]" in card and "[FAIL]" in card
    assert "что-то не так" in card
