"""Token counts and file links as the window reads them (static/redesign.js, run by node).

"1,000,000" used to read as 1 and a plain path in an answer took the whole app window to a
404 page with no way back; the cases live in ui_parsing_cases.js.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

CASES = Path(__file__).parent / "ui_parsing_cases.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_ui_parsing_cases_pass():
    result = subprocess.run(  # noqa: S603 - our own cases file
        [shutil.which("node"), str(CASES)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ALL CASES PASSED" in result.stdout
