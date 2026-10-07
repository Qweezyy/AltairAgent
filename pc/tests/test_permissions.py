"""Режимы разрешений и запомненные решения «всегда разрешать»."""

from __future__ import annotations

import pytest

from core.security.permissions import MODES, PermissionStore, decide, mode_catalog
from core.settings import Settings
from core.tools.base import Tool, ToolContext, ToolResult


class ReadTool(Tool):
    name = "look"
    description = "Только смотрит."
    category = "read"

    async def run(self, args, ctx) -> str:
        return "посмотрел"


class EditTool(Tool):
    name = "edit_something"
    description = "Меняет файл."
    category = "edit"
    dangerous = True

    async def run(self, args, ctx) -> str:
        return "изменил"


class ShellTool(Tool):
    name = "run_something"
    description = "Запускает команду."
    category = "execute"
    dangerous = True

    async def run(self, args, ctx) -> str:
        return "запустил"


class SloppyTool(Tool):
    """Опасный инструмент, у которого забыли указать категорию."""

    name = "sloppy"
    description = "Что-то опасное."
    dangerous = True

    async def run(self, args, ctx) -> str:
        return "ой"


# ------------------------------------------------------------------ режимы


@pytest.mark.parametrize(
    ("mode", "read", "edit", "execute"),
    [
        ("manual", "allow", "ask", "ask"),
        ("accept_edits", "allow", "allow", "ask"),
        ("plan", "allow", "block", "block"),
        ("auto", "allow", "ask", "ask"),  # дальше решает Tool.auto_verdict
        ("bypass", "allow", "allow", "allow"),
    ],
)
def test_mode_matrix(mode, read, edit, execute):
    assert decide("read", mode) == read
    assert decide("edit", mode) == edit
    assert decide("execute", mode) == execute


def test_unknown_mode_falls_back_to_manual():
    assert decide("edit", "чепуха") == "ask"


def test_catalog_describes_every_mode():
    catalog = mode_catalog()
    assert {item["id"] for item in catalog} == set(MODES)
    assert all(item["title"] and item["hint"] for item in catalog)


def test_legacy_env_values_are_migrated():
    """Старые auto/dangerous/all не должны ломать настройки пользователя."""
    assert Settings(_env_file=None, approval_mode="auto").approval_mode == "bypass"
    assert Settings(_env_file=None, approval_mode="dangerous").approval_mode == "manual"
    assert Settings(_env_file=None, approval_mode="all").approval_mode == "manual"


def test_dangerous_tool_without_category_is_not_treated_as_safe():
    """Иначе забытая категория тихо отключила бы подтверждение."""
    assert SloppyTool.category == "edit"


# ------------------------------------------------------ поведение инструментов


async def test_read_tool_never_asks(ctx):
    ctx.settings.approval_mode = "manual"
    asked = []

    async def approver(request):
        asked.append(request.name)
        return True

    ctx.approver = approver
    assert (await ReadTool().invoke({}, ctx)).ok
    assert asked == []


async def test_accept_edits_skips_files_but_asks_for_commands(ctx):
    ctx.settings.approval_mode = "accept_edits"
    asked = []

    async def approver(request):
        asked.append(request.name)
        return True

    ctx.approver = approver
    await EditTool().invoke({}, ctx)
    await ShellTool().invoke({}, ctx)

    assert asked == ["run_something"], "правки не должны спрашивать, команды — должны"


async def test_plan_mode_blocks_changes_with_explanation(ctx):
    ctx.settings.approval_mode = "plan"

    result = await EditTool().invoke({}, ctx)

    assert not result.ok
    assert "планирования" in result.content
    assert "переключит режим" in result.content


async def test_approval_request_carries_category(ctx):
    ctx.settings.approval_mode = "manual"
    seen = {}

    async def approver(request):
        seen["category"] = request.category
        return False

    ctx.approver = approver
    await ShellTool().invoke({}, ctx)
    assert seen["category"] == "execute"


# ------------------------------------------------------ «всегда разрешать»


def test_permission_store_project_scope(settings: Settings, tmp_path):
    store = PermissionStore(settings=settings)
    other = tmp_path / "другой"
    other.mkdir()

    store.allow_project("execute_command", settings.workspace)

    assert store.is_allowed("execute_command", settings.workspace)
    assert not store.is_allowed("execute_command", other), "разрешение не должно течь в другой проект"
    assert not store.is_allowed("write_file", settings.workspace)


def test_permission_store_global_scope(settings: Settings, tmp_path):
    store = PermissionStore(settings=settings)
    other = tmp_path / "другой"
    other.mkdir()

    store.allow_global("read_file")

    assert store.is_allowed("read_file", settings.workspace)
    assert store.is_allowed("read_file", other)


def test_permissions_survive_restart(settings: Settings):
    PermissionStore(settings=settings).allow_project("write_file", settings.workspace)

    fresh = PermissionStore(settings=settings)  # как после перезапуска приложения
    assert fresh.is_allowed("write_file", settings.workspace)


def test_permissions_live_in_app_dir_not_project(settings: Settings):
    store = PermissionStore(settings=settings)
    store.allow_global("write_file")

    assert settings.app_dir in store.path.parents
    assert settings.workspace not in store.path.parents


def test_revoke_clears_everything(settings: Settings):
    store = PermissionStore(settings=settings)
    store.allow_global("write_file")
    store.allow_project("execute_command", settings.workspace)

    store.revoke_all()

    assert not store.is_allowed("write_file", settings.workspace)
    assert store.summary(settings.workspace) == {"global": [], "project": []}


async def test_tool_result_type_is_preserved(ctx):
    ctx.settings.approval_mode = "bypass"
    result = await EditTool().invoke({}, ctx)
    assert isinstance(result, ToolResult) and result.ok


def test_tool_context_defaults(settings):
    context = ToolContext(settings=settings)
    assert context.settings.approval_mode in MODES


# ------------------------------------------------------------ режим «Авто»


def test_auto_is_first_in_catalog():
    """The order as in Claude Code: Auto, Manual, Edits; then Autopilot (0.3.0: everything but
    reconfiguring the machine), Plan, No confirmations."""
    assert [item["id"] for item in mode_catalog()] == [
        "auto",
        "manual",
        "accept_edits",
        "autopilot",
        "plan",
        "bypass",
    ]


async def test_auto_allows_edits_inside_workspace(ctx):
    ctx.settings.approval_mode = "auto"
    asked = []

    async def approver(request):
        asked.append(request.name)
        return True

    ctx.approver = approver
    assert (await EditTool().invoke({}, ctx)).ok
    assert asked == [], "правки в песочнице спрашивать незачем"


async def test_auto_asks_before_running_code(ctx):
    ctx.settings.approval_mode = "auto"
    asked = []

    async def approver(request):
        asked.append(request.name)
        return True

    ctx.approver = approver
    await ShellTool().invoke({}, ctx)
    assert asked == ["run_something"]


@pytest.mark.parametrize(
    "command",
    ["git status", "git log --oneline", "ls -la", "python -m pytest", "npm test", "cat file.txt"],
)
def test_read_only_commands_recognised(command):
    from core.tools.builtin.shell import is_read_only_command

    assert is_read_only_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "pip install requests",
        "git push",
        "rm file.txt",
        "python script.py",
        "npm install",
        "echo hi > file.txt",   # перенаправление меняет файл
        "ls && rm -f x",        # склейка прячет вторую команду
        "cat f | sh",           # конвейер в интерпретатор
    ],
)
def test_changing_commands_require_a_question(command):
    from core.tools.builtin.shell import is_read_only_command

    assert not is_read_only_command(command)


async def test_delete_always_asks_even_in_auto(ctx):
    """Удаление необратимо, автоматическое решение здесь недопустимо."""
    from core.tools.builtin.files import DeletePathTool

    ctx.settings.approval_mode = "auto"
    victim = ctx.settings.workspace / "нужный.txt"
    victim.write_text("данные", encoding="utf-8")

    asked = []

    async def approver(request):
        asked.append(request.name)
        return False

    ctx.approver = approver
    result = await DeletePathTool().invoke({"path": "нужный.txt"}, ctx)

    assert asked == ["delete_path"]
    assert not result.ok
    assert victim.exists()
