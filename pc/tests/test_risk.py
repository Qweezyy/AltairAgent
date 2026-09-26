"""Тесты независимого верификатора шага и тиров риска (core/security/risk.py)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.security.approval import ApprovalRequest
from core.security.risk import assess, collect_text, rank
from core.tools.base import Tool, ToolContext

# ------------------------------------------------------------- модуль assess


def test_tier_ranks_are_ordered() -> None:
    assert rank("safe") < rank("low") < rank("moderate") < rank("high") < rank("critical")
    assert rank("неизвестно") == rank("low")  # запасной вариант


def test_read_is_safe_edit_is_low() -> None:
    assert assess(category="read").tier == "safe"
    assert assess(category="edit").tier == "low"


def test_execute_plain_is_moderate() -> None:
    a = assess(category="execute", scan_text="npm test")
    assert a.tier == "moderate" and not a.blocked and a.reasons == []


def test_irreversible_is_high() -> None:
    assert assess(category="edit", irreversible=True).tier == "high"


def test_catastrophic_command_is_blocked_critical() -> None:
    a = assess(category="execute", scan_text="rm -rf /")
    assert a.tier == "critical" and a.blocked
    assert a.reasons  # причина названа


def test_force_push_is_high_not_blocked() -> None:
    a = assess(category="execute", scan_text="git push origin main --force")
    assert a.tier == "high" and not a.blocked
    assert any("git push" in r for r in a.reasons)


def test_drop_table_flagged_high() -> None:
    a = assess(category="network", scan_text="DROP TABLE users;")
    assert rank(a.tier) >= rank("high") and not a.blocked


def test_collect_text_walks_nested_args() -> None:
    text = collect_text({"command": "ls", "opts": ["-la", {"cwd": "/tmp"}], "n": 5})
    assert "ls" in text and "-la" in text and "/tmp" in text


# ------------------------------------------------ интеграция с Tool.invoke


class _CmdArgs(BaseModel):
    command: str = Field(default="")


class _FakeCmdTool(Tool):
    """Инструмент-команда, который САМ считает себя безопасным (auto_verdict=allow).

    Нужен, чтобы проверить: независимый верификатор всё равно перехватит опасное,
    не доверяя самооценке инструмента/модели.
    """

    name = "fake_cmd"
    description = "Тестовая команда."
    Args = _CmdArgs
    category = "execute"
    dangerous = True
    timeout = None

    def __init__(self) -> None:
        self.ran = False

    def auto_verdict(self, args, ctx):  # type: ignore[override]
        return "allow"

    async def run(self, args: _CmdArgs, ctx: ToolContext) -> str:
        self.ran = True
        return "выполнено"


async def test_block_applies_even_in_bypass(settings):
    """Катастрофическая команда блокируется даже в режиме «Без подтверждений»."""
    s = settings.model_copy(update={"approval_mode": "bypass"})
    tool = _FakeCmdTool()
    result = await tool.invoke({"command": "rm -rf /"}, ToolContext(settings=s))
    assert not result.ok
    assert "заблокирован" in result.content
    assert not tool.ran  # до выполнения дело не дошло


async def test_verifier_escalates_high_risk_to_approval(settings):
    """Высокий риск требует подтверждения, даже если сам инструмент разрешил бы (auto)."""
    s = settings.model_copy(update={"approval_mode": "auto", "risk_gate": True})
    tool = _FakeCmdTool()
    seen: list[ApprovalRequest] = []

    async def approver(req: ApprovalRequest) -> bool:
        seen.append(req)
        return True

    result = await tool.invoke(
        {"command": "git push origin main --force"},
        ToolContext(settings=s, approver=approver),
    )
    assert result.ok and tool.ran
    assert len(seen) == 1  # верификатор заставил спросить
    assert seen[0].tier == "high"
    assert seen[0].reasons  # причина показана пользователю


async def test_risk_gate_off_skips_escalation(settings):
    """С выключенным risk_gate высокий риск не эскалируется (но блок остаётся)."""
    s = settings.model_copy(update={"approval_mode": "auto", "risk_gate": False})
    tool = _FakeCmdTool()
    asked = {"n": 0}

    async def approver(req: ApprovalRequest) -> bool:
        asked["n"] += 1
        return True

    result = await tool.invoke(
        {"command": "git push origin main --force"},
        ToolContext(settings=s, approver=approver),
    )
    assert result.ok and tool.ran
    assert asked["n"] == 0  # без верификатора инструмент прошёл по своей самооценке


async def test_block_holds_with_risk_gate_off(settings):
    """Жёсткий блок катастрофического не зависит от risk_gate."""
    s = settings.model_copy(update={"approval_mode": "bypass", "risk_gate": False})
    tool = _FakeCmdTool()
    result = await tool.invoke({"command": "mkfs.ext4 /dev/sda"}, ToolContext(settings=s))
    assert not result.ok and "заблокирован" in result.content
    assert not tool.ran
