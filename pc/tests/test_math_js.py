"""Проверка маскировки формул из static/math.js.

Логика чисто строковая и живёт в JavaScript — переписывать её на Python ради
теста значило бы проверять копию, а не то, что реально работает в окне.
Поэтому тест запускает node; если node в системе нет, проверка пропускается.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

CASES = Path(__file__).parent / "math_cases.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node не установлен")
def test_math_masking_cases_pass():
    result = subprocess.run(  # noqa: S603 - запускаем свой же файл проверок
        [shutil.which("node"), str(CASES)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ПРОВЕРОК ПРОШЛИ" in result.stdout
