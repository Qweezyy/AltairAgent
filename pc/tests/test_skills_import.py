"""Skills the user adds from files: SKILL.md, .zip, folders — and what the agent then sees."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from core.skills.manager import SkillManager, parse_frontmatter

SKILL = """---
name: pdf-tools
description: >
  Use for PDF work: filling forms,
  merging and splitting files.
license: MIT
---
# PDF

Run `scripts/fill.py` to fill a form. Field names are in references/fields.md.
"""


def make_skill(root: Path, name: str = "pdf-tools", text: str = SKILL) -> Path:
    folder = root / name
    (folder / "scripts").mkdir(parents=True)
    (folder / "references").mkdir()
    (folder / "SKILL.md").write_text(text, encoding="utf-8")
    (folder / "scripts" / "fill.py").write_text("print('fill')\n", encoding="utf-8")
    (folder / "references" / "fields.md").write_text("- name\n", encoding="utf-8")
    return folder


def test_multiline_description_is_read_whole():
    meta, body = parse_frontmatter(SKILL)
    assert meta["description"] == "Use for PDF work: filling forms, merging and splitting files."
    assert meta["name"] == "pdf-tools" and body.startswith("# PDF")


def test_import_folder_and_read_lists_bundled_files(settings, tmp_path):
    manager = SkillManager(settings)
    installed = manager.import_path(make_skill(tmp_path / "src"))
    assert [s.name for s in installed] == ["pdf-tools"]

    skill = manager.get("pdf-tools")
    assert skill is not None and "merging and splitting" in skill.description
    text = manager.read("pdf-tools")
    assert "scripts/fill.py" in text and "references/fields.md" in text
    assert str(skill.path.parent) in text  # the agent knows where to run the scripts from
    assert "pdf-tools" in manager.prompt_section()


def test_import_zip_of_a_folder_and_of_several_skills(settings, tmp_path):
    src = tmp_path / "src"
    make_skill(src, "one", SKILL.replace("pdf-tools", "one"))
    make_skill(src, "two", SKILL.replace("pdf-tools", "two"))
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in src.rglob("*"):
            if path.is_file():
                zf.write(path, Path("my-skills") / path.relative_to(src))  # wrapped in a folder
    manager = SkillManager(settings)
    assert sorted(s.name for s in manager.import_path(archive)) == ["one", "two"]
    assert (manager.skills_dir / "one" / "scripts" / "fill.py").is_file()


def test_import_single_md_file_names_it_from_frontmatter(settings, tmp_path):
    md = tmp_path / "whatever.md"
    md.write_text("---\nname: Git Flow!\ndescription: branching rules\n---\nUse rebase.\n", encoding="utf-8")
    manager = SkillManager(settings)
    assert [s.name for s in manager.import_path(md)] == ["Git-Flow"]
    assert "Use rebase." in manager.read("Git-Flow")


def test_existing_skill_needs_overwrite(settings, tmp_path):
    manager = SkillManager(settings)
    folder = make_skill(tmp_path / "src")
    manager.import_path(folder)
    with pytest.raises(FileExistsError, match="pdf-tools"):
        manager.import_path(folder)
    (folder / "SKILL.md").write_text(SKILL.replace("# PDF", "# PDF v2"), encoding="utf-8")
    manager.import_path(folder, overwrite=True)
    assert "# PDF v2" in manager.read("pdf-tools")


def test_zip_slip_and_non_skills_are_refused(settings, tmp_path):
    manager = SkillManager(settings)
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("SKILL.md", "---\nname: evil\ndescription: x\n---\n")
        zf.writestr("../../outside.txt", "pwned")
    with pytest.raises(ValueError, match="unsafe path"):
        manager.import_path(evil)
    assert not (tmp_path.parent / "outside.txt").exists()

    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("readme.txt", "no skill here")
    with pytest.raises(ValueError, match="no SKILL.md"):
        manager.import_path(empty)
    with pytest.raises(ValueError):
        manager.import_path(tmp_path / "picture.png")


def test_delete_removes_only_the_skill(settings, tmp_path):
    manager = SkillManager(settings)
    manager.import_path(make_skill(tmp_path / "src"))
    assert manager.delete("pdf-tools") is True
    assert manager.get("pdf-tools") is None
    assert manager.skills_dir.is_dir()
    assert manager.delete("pdf-tools") is False
    assert manager.delete("../..") is False


def test_project_scope_goes_into_the_workspace(settings, tmp_path):
    manager = SkillManager(settings)
    manager.import_path(make_skill(tmp_path / "src"), scope="project")
    skill = manager.get("pdf-tools")
    assert skill is not None and skill.scope == "project"
    assert settings.workspace in skill.path.parents
