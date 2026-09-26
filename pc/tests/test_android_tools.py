from __future__ import annotations

from pathlib import Path

import pytest

from core.tools.builtin.android_tools import AndroidDiagnoseTool, AndroidInstallTool
from core.utils.proc import ProcResult


async def test_android_diagnose_reports_missing_sdk(ctx):
    ctx.settings.android_sdk_path = str(ctx.settings.workspace / "missing-sdk")
    result = await AndroidDiagnoseTool().invoke({}, ctx)
    assert not result.ok
    assert "SDK" in result.content
    assert "не найден" in result.content


async def test_android_install_rejects_apk_outside_workspace(ctx):
    result = await AndroidInstallTool().invoke({"apk": "../app.apk"}, ctx)
    assert not result.ok
    assert "запрещён" in result.content or "разрешён" in result.content


@pytest.mark.asyncio
async def test_android_install_uses_safe_path_and_adb(monkeypatch, ctx):
    apk = Path(ctx.settings.workspace) / "app.apk"
    apk.write_bytes(b"fake")
    calls: list[list[str]] = []

    monkeypatch.setattr("core.tools.builtin.android_tools._adb", lambda _ctx: "adb")

    async def fake_run(argv, **kwargs):
        calls.append(argv)
        return ProcResult(0, "Success", "")

    monkeypatch.setattr("core.tools.builtin.android_tools.run_process", fake_run)
    result = await AndroidInstallTool().invoke({"apk": "app.apk"}, ctx)

    assert result.ok
    assert calls == [["adb", "install", "-r", str(apk)]]
