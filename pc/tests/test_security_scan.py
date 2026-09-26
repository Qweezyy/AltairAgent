"""DevSecOps: сканер секретов и анализ уязвимостей (Bandit)."""

from __future__ import annotations

import pytest

from core.security.secrets import _mask, scan_text
from core.tools.base import ToolContext
from core.tools.builtin.security_tools import ScanSecretsTool, SecurityScanTool

# --------------------------------------------------------------- сканер секретов


def test_mask_never_shows_full():
    masked = _mask("AKIAIOSFODNN7EXAMPLE")
    assert "AKIAIOSFODNN7EXAMPLE" not in masked
    assert masked.startswith("AKIA")
    assert _mask("short") == "*****"


def test_detects_known_formats():
    def kinds(text):
        return {f.kind for f in scan_text(text)}

    assert any("приватный ключ" in k for k in kinds("-----BEGIN RSA PRIVATE KEY-----"))
    assert any("AWS" in k for k in kinds("key = AKIAIOSFODNN7EXAMPLE"))
    assert any("sk-" in k for k in kinds('OPENAI="sk-abcdefghijklmnopqrstuvwxyz012345"'))
    assert any("GitHub" in k for k in kinds("t = ghp_0123456789abcdefghijklmnopqrstuvwxyzAB"))


def test_detects_assignment():
    findings = scan_text('password = "SuperSecret123!"')
    assert findings
    assert "SuperSecret123!" not in findings[0].masked


def test_ignores_placeholders():
    assert scan_text('password = "your_password_here"') == []
    assert scan_text('api_key = "${API_KEY}"') == []
    assert scan_text('token = os.environ["TOKEN"]') == []
    assert scan_text('secret = "xxxxxxxx"') == []


def test_reports_line_numbers():
    text = "line1\nkey = AKIAIOSFODNN7EXAMPLE\nline3"
    findings = scan_text(text)
    assert findings[0].line == 2


@pytest.mark.asyncio
async def test_scan_secrets_tool(settings):
    (settings.workspace / "config.py").write_text(
        'API_KEY = "sk-abcdefghijklmnopqrstuvwxyz012345"\nDEBUG = True\n', encoding="utf-8"
    )
    (settings.workspace / "clean.py").write_text("x = 1\n", encoding="utf-8")
    result = await ScanSecretsTool().run(ScanSecretsTool.Args(path="."), ToolContext(settings=settings))
    assert not result.ok
    assert "config.py" in result.content
    # Секрет замаскирован — не светится целиком.
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in result.content


@pytest.mark.asyncio
async def test_scan_secrets_clean(settings):
    (settings.workspace / "clean.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    result = await ScanSecretsTool().run(ScanSecretsTool.Args(path="."), ToolContext(settings=settings))
    assert result.ok
    assert "не найдено" in result.content


# ------------------------------------------------------------------- Bandit


@pytest.mark.asyncio
async def test_security_scan_finds_vuln(settings):
    pytest.importorskip("bandit")
    # eval() пользовательского ввода — классическая находка Bandit (B307).
    (settings.workspace / "vuln.py").write_text(
        "import subprocess\n"
        "def run(cmd):\n"
        "    subprocess.call(cmd, shell=True)\n"
        "def calc(x):\n"
        "    return eval(x)\n",
        encoding="utf-8",
    )
    result = await SecurityScanTool().run(
        SecurityScanTool.Args(path="vuln.py"), ToolContext(settings=settings)
    )
    # Должна найтись хотя бы одна проблема (eval / shell=True).
    assert not result.ok
    assert "vuln.py" in result.content


@pytest.mark.asyncio
async def test_security_scan_clean(settings):
    pytest.importorskip("bandit")
    (settings.workspace / "ok.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    result = await SecurityScanTool().run(
        SecurityScanTool.Args(path="ok.py"), ToolContext(settings=settings)
    )
    assert result.ok
    assert "не нашёл" in result.content
