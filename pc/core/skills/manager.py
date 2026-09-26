"""Навыки (Skills) — файлы с инструкциями, которые агент подгружает по требованию.

Навык = папка `skills/<имя>/SKILL.md` с frontmatter:

    ---
    name: python_expert
    description: Когда и зачем читать этот навык.
    ---
    # Тело инструкции...

В системный промпт попадают ТОЛЬКО имя и описание (дёшево по токенам).
Полный текст модель читает инструментом `read_skill`, когда он реально нужен.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from core.logging_setup import get_logger
from core.settings import Settings, get_settings
from core.utils.text import read_text_file

logger = get_logger("skills")

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
#: Import limits: a skill is instructions plus a few scripts/references, not a dataset.
MAX_IMPORT_BYTES = 25 * 1024 * 1024
MAX_IMPORT_FILES = 500
#: Bundled files shown to the model with the skill (the rest are still on disk).
LISTED_FILES = 60
_SKILL_FILES = ("SKILL.md", "skill.md", "Skill.md")


def safe_skill_name(raw: str) -> str:
    """A folder-safe skill name from whatever a downloaded skill calls itself."""
    return re.sub(r"[^A-Za-z0-9_-]+", "-", raw.strip()).strip("-_")[:64]


@dataclass(slots=True)
class Skill:
    name: str
    description: str
    path: Path
    #: "global" — навык приложения, "project" — навык текущей рабочей папки.
    scope: str = "global"

    @property
    def rel_path(self) -> str:
        return str(self.path)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """YAML-like `key: value` frontmatter without a YAML dependency.

    Handles what real SKILL.md files use: quoted values, block scalars (`description: >` / `|`)
    and indented continuation lines — a description folded over several lines must not be cut
    to its first line, or the model never learns when to use the skill.
    """
    text = text.lstrip("\ufeff")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    key = ""
    block: list[str] = []
    folded = True

    def flush() -> None:
        if key and block:
            joined = (" " if folded else "\n").join(part.strip() for part in block if part.strip())
            meta[key] = joined.strip().strip("\"'")

    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1] in (" ", "\t") and key:
            block.append(line)
            continue
        if ":" not in line:
            continue
        flush()
        raw_key, _, value = line.partition(":")
        key, value = raw_key.strip().lower(), value.strip()
        block, folded = [], True
        if value in (">", ">-", "|", "|-"):
            folded = value.startswith(">")
        elif value:
            block = [value]
    flush()
    return meta, text[match.end() :]


class SkillManager:
    """Сканирует папку skills/ и отдаёт метаданные навыков."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def skills_dir(self) -> Path:
        """Куда создаются новые глобальные навыки."""
        return self.settings.skills_dir

    @property
    def skills_dirs(self) -> list[Path]:
        """Источники навыков: глобальные (папка приложения) + проектные."""
        return self.settings.skills_dirs

    def list_skills(self) -> list[Skill]:
        """Навыки из всех источников.

        Проектный навык с тем же именем перекрывает глобальный — это позволяет
        переопределить общее правило под конкретный репозиторий.
        """
        found: dict[str, Skill] = {}

        for index, directory in enumerate(self.skills_dirs):
            scope = "global" if index == 0 else "project"
            if not directory.exists():
                continue
            for entry in sorted(directory.iterdir()):
                skill_file = entry / "SKILL.md"
                if entry.name.startswith(".") or not entry.is_dir() or not skill_file.exists():
                    continue
                try:
                    meta, body = parse_frontmatter(read_text_file(skill_file))
                except OSError as exc:
                    logger.warning("Не удалось прочитать %s: %s", skill_file, exc)
                    continue

                description = meta.get("description")
                if not description:
                    first_line = next(
                        (ln.strip() for ln in body.splitlines() if ln.strip() and not ln.startswith("#")),
                        "",
                    )
                    description = first_line or "Specialised instructions."
                # The folder name is the fallback: a free-form frontmatter name ("Git Flow!")
                # could not be passed to read_skill or used on disk.
                declared = meta.get("name") or ""
                name = declared if _NAME_RE.match(declared) else entry.name
                found[name] = Skill(
                    name=name, description=description, path=skill_file, scope=scope
                )

        return [found[name] for name in sorted(found)]

    def get(self, name: str) -> Skill | None:
        name = name.strip().replace("/", "").replace("\\", "")
        for skill in self.list_skills():
            if skill.name == name or skill.path.parent.name == name:
                return skill
        return None

    def read(self, name: str) -> str:
        """The skill text plus where its bundled files live (scripts, references, templates)."""
        skill = self.get(name)
        if skill is None:
            available = ", ".join(s.name for s in self.list_skills()) or "none"
            raise FileNotFoundError(f"Skill '{name}' not found. Available: {available}.")
        text = read_text_file(skill.path)
        files = self.bundled_files(skill)
        if not files:
            return text
        listing = "\n".join(f"- {f}" for f in files[:LISTED_FILES])
        more = f"\n- … and {len(files) - LISTED_FILES} more" if len(files) > LISTED_FILES else ""
        return (
            f"{text.rstrip()}\n\n<skill_files folder=\"{skill.path.parent}\">\n"
            "Files bundled with this skill; paths in the instructions are relative to this folder. "
            f"Read them with read_file; run scripts from this folder.\n{listing}{more}\n</skill_files>"
        )

    @staticmethod
    def bundled_files(skill: Skill) -> list[str]:
        folder = skill.path.parent
        out: list[str] = []
        for path in folder.rglob("*"):
            rel = path.relative_to(folder)
            if path.is_file() and path != skill.path and not any(p.startswith(".") for p in rel.parts):
                out.append(rel.as_posix())
        return sorted(out)

    # ------------------------------------------------------------------ import / delete

    def import_path(self, source: Path, *, scope: str = "global", overwrite: bool = False) -> list[Skill]:
        """Install skills from a SKILL.md / .md file, a .zip or a folder (one skill or several).

        Raises FileExistsError (message = the clashing names) when a skill exists and
        `overwrite` is off, ValueError for anything that is not a valid skill.
        """
        source = Path(source)
        if not source.exists():
            raise ValueError(f"'{source}' does not exist")
        base = self.settings.project_skills_dir if scope == "project" else self.skills_dir
        with tempfile.TemporaryDirectory(prefix="skill-import-") as tmp:
            staging = Path(tmp)
            if source.is_dir():
                roots = self._skill_roots(source)
            elif source.suffix.lower() == ".zip":
                self._extract_zip(source, staging / "zip")
                roots = self._skill_roots(staging / "zip")
            elif source.suffix.lower() in (".md", ".markdown"):
                folder = staging / "single"
                folder.mkdir()
                shutil.copyfile(source, folder / "SKILL.md")
                fallback = source.parent.name if source.name.lower() == "skill.md" else source.stem
                roots = [(folder, fallback)]
            else:
                raise ValueError("a skill is a SKILL.md file, a .zip or a folder with SKILL.md")
            if not roots:
                raise ValueError("no SKILL.md found: this is not a skill")

            plan: list[tuple[Path, str]] = []
            for folder, fallback in roots:
                meta, _ = parse_frontmatter(read_text_file(self._skill_file(folder)))
                name = safe_skill_name(meta.get("name") or fallback)
                if not _NAME_RE.match(name):
                    raise ValueError(f"cannot make a skill name from '{meta.get('name') or fallback}'")
                self._check_size(folder)
                plan.append((folder, name))
            clashes = [name for _, name in plan if (base / name).exists()]
            if clashes and not overwrite:
                raise FileExistsError(", ".join(clashes))

            base.mkdir(parents=True, exist_ok=True)
            installed: list[Skill] = []
            for folder, name in plan:
                target = base / name
                incoming = base / f".{name}.incoming"
                shutil.rmtree(incoming, ignore_errors=True)
                shutil.copytree(folder, incoming, symlinks=False)
                skill_file = self._skill_file(incoming)
                if skill_file.name != "SKILL.md":
                    skill_file.rename(incoming / "SKILL.md")
                if target.exists():
                    shutil.rmtree(target)
                incoming.rename(target)
                installed.append(self.get(name) or Skill(name, "", target / "SKILL.md", scope))
            return installed

    def delete(self, name: str, scope: str = "global") -> bool:
        base = self.settings.project_skills_dir if scope == "project" else self.skills_dir
        skill = next((s for s in self.list_skills() if s.name == name and s.scope == scope), None)
        folder = skill.path.parent if skill else base / safe_skill_name(name)
        if not folder.is_dir() or folder.resolve().parent != base.resolve():
            return False
        shutil.rmtree(folder)
        return True

    @staticmethod
    def _skill_file(folder: Path) -> Path:
        for candidate in _SKILL_FILES:
            if (folder / candidate).is_file():
                return folder / candidate
        raise ValueError(f"no SKILL.md in {folder.name}")

    @staticmethod
    def _skill_roots(root: Path) -> list[tuple[Path, str]]:
        """Skill folders inside `root`: root itself, its children, or one wrapper level deeper."""

        def has_skill(folder: Path) -> bool:
            return any((folder / c).is_file() for c in _SKILL_FILES)

        if has_skill(root):
            return [(root, root.name)]
        children = [d for d in sorted(root.iterdir()) if d.is_dir() and not d.name.startswith((".", "__"))]
        found = [(d, d.name) for d in children if has_skill(d)]
        if not found and len(children) == 1:  # a zip of a folder of skills
            inner = children[0]
            found = [(d, d.name) for d in sorted(inner.iterdir()) if d.is_dir() and has_skill(d)]
        return found

    @staticmethod
    def _extract_zip(archive: Path, dest: Path) -> None:
        with zipfile.ZipFile(archive) as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            if len(members) > MAX_IMPORT_FILES:
                raise ValueError(f"too many files in the archive (limit {MAX_IMPORT_FILES})")
            if sum(m.file_size for m in members) > MAX_IMPORT_BYTES:
                raise ValueError("the archive is too large for a skill (limit 25 MB)")
            dest.mkdir(parents=True)
            root = dest.resolve()
            for member in members:
                target = (dest / member.filename).resolve()
                if root not in target.parents:  # zip-slip: ../../ paths
                    raise ValueError(f"unsafe path in the archive: {member.filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)

    @staticmethod
    def _check_size(folder: Path) -> None:
        files = [p for p in folder.rglob("*") if p.is_file()]
        if len(files) > MAX_IMPORT_FILES:
            raise ValueError(f"too many files in the skill (limit {MAX_IMPORT_FILES})")
        if sum(p.stat().st_size for p in files) > MAX_IMPORT_BYTES:
            raise ValueError("the skill is too large (limit 25 MB)")

    def create(self, name: str, description: str, content: str, scope: str = "global") -> Skill:
        """Создаёт навык. scope='project' кладёт его в .agent/skills текущего проекта."""
        if not _NAME_RE.match(name):
            raise ValueError(
                f"Недопустимое имя навыка '{name}': латиница, цифры, '_' и '-', до 64 символов."
            )
        base = self.settings.project_skills_dir if scope == "project" else self.skills_dir
        folder = base / name
        folder.mkdir(parents=True, exist_ok=True)
        skill_file = folder / "SKILL.md"
        body = content.strip()
        # Не дублируем frontmatter, если модель уже его написала.
        if body.startswith("---"):
            _, body = parse_frontmatter(body)
        text = f"---\nname: {name}\ndescription: {description.strip()}\n---\n\n{body.strip()}\n"
        skill_file.write_text(text, encoding="utf-8", newline="\n")
        return Skill(name=name, description=description, path=skill_file, scope=scope)

    def prompt_section(self) -> str:
        """System prompt section listing the skills (model-facing, English)."""
        skills = self.list_skills()
        if not skills:
            return ""
        lines = [
            "",
            "<skills>",
            "Skills are specialised instructions. When a task falls under a skill, read it with "
            "read_skill before acting — it holds the tested recipe.",
        ]
        lines += [f"- {s.name}: {s.description}" for s in skills]
        lines.append("</skills>")
        return "\n".join(lines)
