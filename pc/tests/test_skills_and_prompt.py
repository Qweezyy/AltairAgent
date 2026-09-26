from __future__ import annotations

from core.agent.prompt import build_system_prompt
from core.skills.manager import SkillManager, parse_frontmatter
from core.tools import build_default_registry


def test_parse_frontmatter():
    meta, body = parse_frontmatter("---\nname: demo\ndescription: Тест\n---\n\n# Тело\n")
    assert meta == {"name": "demo", "description": "Тест"}
    assert body.strip() == "# Тело"


def test_parse_frontmatter_absent():
    meta, body = parse_frontmatter("# Просто markdown")
    assert meta == {}
    assert body.startswith("#")


def test_create_and_read_skill(settings):
    manager = SkillManager(settings)
    manager.create("git_flow", "Как работать с git", "# Правила\n1. Коммить осмысленно")

    skills = manager.list_skills()
    assert [s.name for s in skills] == ["git_flow"]
    assert "Правила" in manager.read("git_flow")
    assert "git_flow" in manager.prompt_section()


def test_create_skill_rejects_bad_name(settings):
    manager = SkillManager(settings)
    try:
        manager.create("../evil", "x", "y")
    except ValueError as exc:
        assert "имя навыка" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("ожидалась ValueError")


def test_skill_without_frontmatter_still_listed(settings):
    folder = settings.skills_dir / "legacy"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("Просто инструкция без метаданных", encoding="utf-8")

    skills = SkillManager(settings).list_skills()
    assert skills[0].name == "legacy"
    assert "инструкция" in skills[0].description


def test_system_prompt_contains_tools_and_workspace(settings):
    registry = build_default_registry()
    prompt = build_system_prompt(settings=settings, registry=registry)

    assert str(settings.workspace) in prompt
    for name in ("read_file", "execute_command", "web_search"):
        assert name in prompt
    assert settings.agent_language in prompt


def test_system_prompt_includes_project_rules(settings):
    registry = build_default_registry()
    prompt = build_system_prompt(settings=settings, registry=registry, extra="Всегда пиши тесты.")
    assert "<project_instructions>\nВсегда пиши тесты.\n</project_instructions>" in prompt


def test_system_prompt_is_english_and_model_facing(settings):
    """The prompt is model-facing: English only (user text such as memory is excluded)."""
    import re

    from core.agent.prompt import BEHAVIOUR_RULES

    prompt = build_system_prompt(settings=settings, registry=build_default_registry())
    fixed = prompt.replace(str(settings.workspace), "").replace(settings.agent_language, "")
    assert not re.search(r"[а-яё]", fixed, re.I), re.findall(r".{20}[а-яё]+.{20}", fixed, re.I)[:3]
    assert "reply in theirs" in prompt  # the reply language still follows the user
    # No shouting: frontier models over-apply ALL-CAPS rules (Anthropic/OpenAI guidance).
    shouted = re.findall(r"\b[A-Z]{5,}\b", BEHAVIOUR_RULES)
    assert set(shouted) <= {"EXTERNAL", "DATA"}, shouted


def test_system_prompt_mentions_only_existing_tools(settings):
    """Every `tool_name` the rules refer to must exist — a stale name sends the model astray."""
    import re

    from core.agent.prompt import BEHAVIOUR_RULES

    registry = build_default_registry()
    known = {tool.name for tool in registry.all()}
    # spawn_subagent is registered only when the user enables it.
    optional = {"spawn_subagent"}
    mentioned = set(re.findall(r"`([a-z]+(?:_[a-z]+)+)`", BEHAVIOUR_RULES)) | set(
        re.findall(r"`(ask)`", BEHAVIOUR_RULES)
    )
    not_tools = {"project_builder"}  # a skill name, not a tool
    missing = sorted(mentioned - known - optional - not_tools)
    assert not missing, f"prompt refers to unknown tools: {missing}"


def test_system_prompt_does_not_duplicate_tool_schemas(settings):
    """Tool descriptions travel in the tool definitions; repeating them wasted ~5k tokens/request."""
    registry = build_default_registry()
    prompt = build_system_prompt(settings=settings, registry=registry)
    stable = prompt.split("\n<approach>")[0]
    listed = [t.name for t in registry.all() if f"- {t.name}:" in stable]
    assert not listed, listed
    assert len(prompt) < 30_000


def test_editing_guidance_matches_tools(settings):
    """The prompt recommends edit_file for local changes and apply_patch in both formats."""
    prompt = build_system_prompt(settings=settings, registry=build_default_registry())
    assert "`edit_file`" in prompt and "`apply_patch`" in prompt and "*** Begin Patch" in prompt


def test_prompt_sections_are_english_tagged(settings):
    from core.folder_memory import FolderMemory
    from core.reminders import ReminderStore

    manager = SkillManager(settings)
    manager.create("git_flow", "How to work with git", "# Rules\n1. Commit meaningfully")
    section = manager.prompt_section()
    assert section.strip().startswith("<skills>") and section.rstrip().endswith("</skills>")
    assert "read_skill" in section

    fm = FolderMemory(settings.workspace)
    fm.append("Tests run via pytest -q", "project")
    assert fm.prompt_section().endswith("</folder_memory>")
    assert ReminderStore(settings.data_dir).prompt_section("none") == ""


# --------------------------------------------- встроенные навыки приложения


def test_bundled_skills_are_valid():
    """Все навыки в репозитории парсятся и имеют имя и описание."""
    from pathlib import Path

    from core.skills.manager import parse_frontmatter

    skills_root = Path(__file__).resolve().parent.parent / "skills"
    found = list(skills_root.glob("*/SKILL.md"))
    assert len(found) >= 7, f"ожидалось >=7 навыков, найдено {len(found)}"

    for skill_file in found:
        meta, body = parse_frontmatter(skill_file.read_text(encoding="utf-8"))
        assert meta.get("name"), f"нет name в {skill_file}"
        assert meta.get("description"), f"нет description в {skill_file}"
        assert len(body.strip()) > 100, f"тело навыка {skill_file} подозрительно короткое"
        # Имя в frontmatter совпадает с папкой — иначе read_skill по папке не найдёт.
        assert meta["name"] == skill_file.parent.name


def test_all_functional_domains_have_a_skill():
    """Каждая крупная функция приложения покрыта навыком."""
    from pathlib import Path

    names = {p.parent.name for p in (Path(__file__).resolve().parent.parent / "skills").glob("*/SKILL.md")}
    assert {"coding", "research", "data_analytics", "academic", "socratic_examiner", "daily_life"} <= names


def test_copy_bundled_skills_adds_new_only(tmp_path):
    """Регресс: новые навыки докопируются, существующие не затираются."""
    from main import _copy_bundled_skills

    # Имитируем «пакет» с навыками рядом с exe.
    exe_dir = tmp_path / "app"
    bundled = exe_dir / "skills"
    (bundled / "coding").mkdir(parents=True)
    (bundled / "coding" / "SKILL.md").write_text("---\nname: coding\n---\nновое", encoding="utf-8")
    (bundled / "research").mkdir(parents=True)
    (bundled / "research" / "SKILL.md").write_text("---\nname: research\n---\nновое", encoding="utf-8")

    target = tmp_path / "userdata" / "skills"
    # У пользователя уже есть свой coding с правками — не затирать.
    (target / "coding").mkdir(parents=True)
    (target / "coding" / "SKILL.md").write_text("мои правки", encoding="utf-8")

    added = _copy_bundled_skills(exe_dir, target)
    assert added == 1  # только research добавлен
    assert (target / "coding" / "SKILL.md").read_text(encoding="utf-8") == "мои правки"
    assert (target / "research" / "SKILL.md").exists()
