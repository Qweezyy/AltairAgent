"""Выбор рабочей папки: изоляция проектов и живучесть настроек.

Эти тесты закрывают то, что раньше молча ломалось:
  * данные приложения (чаты, навыки, логи) уезжали в папку проекта;
  * навыки пропадали при переключении папки;
  * несуществующий путь тихо создавался или подменялся на папку по умолчанию;
  * агент продолжал работать в старой директории.
"""

from __future__ import annotations

import pytest

from core.errors import ConfigError, PathNotAllowed
from core.security.paths import resolve_path
from core.settings import Settings, validate_workspace
from core.skills.manager import SkillManager

# ------------------------------------------------- разделение app / workspace


def test_app_data_never_goes_into_project_folder(settings: Settings):
    project = settings.workspace
    switched = settings.for_workspace(project)

    for path in (switched.storage_dir, switched.logs_dir, switched.skills_dir, switched.mcp_config_path):
        assert settings.app_dir in path.parents or path == settings.app_dir, (
            f"{path} должен лежать в папке приложения, а не в проекте"
        )
        assert project not in path.parents


def test_for_workspace_keeps_app_dir(settings: Settings, tmp_path):
    other = tmp_path / "other_project"
    other.mkdir()

    switched = settings.for_workspace(other)

    assert switched.workspace == other.resolve()
    assert switched.app_dir == settings.app_dir
    assert settings.workspace != switched.workspace  # исходные настройки не мутируют


def test_sandbox_follows_new_workspace(settings: Settings, tmp_path):
    other = tmp_path / "another"
    other.mkdir()
    switched = settings.for_workspace(other)

    inside = resolve_path("file.txt", settings=switched)
    assert inside.parent == other.resolve()

    with pytest.raises(PathNotAllowed):
        resolve_path(str(settings.workspace / "secret.txt"), settings=switched)


# ------------------------------------------------------------------ навыки


def test_global_skills_survive_workspace_switch(settings: Settings, tmp_path):
    SkillManager(settings).create("global_rule", "Общее правило", "# Всегда")

    other = tmp_path / "project_two"
    other.mkdir()
    manager = SkillManager(settings.for_workspace(other))

    assert [s.name for s in manager.list_skills()] == ["global_rule"]


def test_project_skill_is_visible_only_in_its_project(settings: Settings, tmp_path):
    SkillManager(settings).create("local_rule", "Правило проекта", "# Только тут", scope="project")

    assert [s.name for s in SkillManager(settings).list_skills()] == ["local_rule"]

    other = tmp_path / "project_three"
    other.mkdir()
    assert SkillManager(settings.for_workspace(other)).list_skills() == []


def test_project_skill_overrides_global(settings: Settings):
    manager = SkillManager(settings)
    manager.create("style", "Глобальный стиль", "# Глобально")
    manager.create("style", "Стиль проекта", "# Локально", scope="project")

    skills = manager.list_skills()
    assert len(skills) == 1
    assert skills[0].scope == "project"
    assert "Локально" in manager.read("style")


# -------------------------------------------------------------- валидация


def test_missing_folder_with_existing_parent_is_created(tmp_path):
    target = tmp_path / "new_project"
    assert validate_workspace(target) == target.resolve()
    assert target.is_dir()


def test_typo_path_is_rejected_instead_of_creating_garbage(tmp_path):
    with pytest.raises(ConfigError) as exc:
        validate_workspace(tmp_path / "нет" / "такой" / "папки")
    assert "не существует" in str(exc.value)


def test_file_instead_of_folder_is_rejected(tmp_path):
    file_path = tmp_path / "notes.txt"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(ConfigError):
        validate_workspace(file_path)


def test_empty_path_is_rejected():
    with pytest.raises(ConfigError):
        validate_workspace("   ")


def test_quoted_path_from_explorer_is_accepted(tmp_path):
    """Windows копирует путь в кавычках — это не должно ломать выбор."""
    assert validate_workspace(f'"{tmp_path}"') == tmp_path.resolve()
