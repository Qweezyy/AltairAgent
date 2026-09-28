"""The agent's long-term memory: a folder of notes with a one-line-per-note index.

Two such folders, laid out the same way:
  * global — `<data dir>/memory/`: about the user and how they like to work (all chats);
  * folder — `<workspace>/.agent/memory/`: about this project (see core/folder_memory.py).

Each folder holds `MEMORY.md`, the index — one line per note: `- [Title](file.md) — hook` —
and one markdown file per note: a header (name, title, a one-line description, type, created
and modified dates) and the note itself in as much detail as it needs.

Only the indexes go into the system prompt (capped, see INDEX_MAX_LINES / INDEX_MAX_BYTES):
the model sees what it knows at a glance and opens a note with memory_read when it needs the
details. The index is written by the store from the notes' headers, so it never drifts from
the files; `modified` is stamped by the store on every write, not by the model.

Kinds of notes (the `type` field):
  * user      — who the user is, their role, expertise, preferences;
  * feedback  — corrections and approaches the user confirmed, with why and how to apply;
  * project   — ongoing work, decisions and constraints the code does not show;
  * reference — where to find things outside the project (trackers, dashboards, docs).

Plain files on purpose: the user can open, edit or delete any of them.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.fs_atomic import atomic_write_text, path_lock
from core.logging_setup import get_logger

logger = get_logger("memory")

TYPES = ("user", "feedback", "project", "reference")
INDEX_NAME = "MEMORY.md"
#: Only this much of an index is loaded into the prompt; the rest would be dropped.
INDEX_MAX_LINES = 200
INDEX_MAX_BYTES = 25_000
#: From this share of a limit on, a write reminds the model to tighten the index.
INDEX_WARN_SHARE = 0.8
#: Older categories of the JSON store → types.
_OLD_CATEGORY = {"user": "user", "preference": "feedback", "project": "project", "fact": "project"}

_WORD_RE = re.compile(r"[\w\-]{3,}", re.UNICODE)
_SLUG_RE = re.compile(r"[^\w]+", re.UNICODE)


def slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-_")
    return slug[:60].strip("-_") or "note"


def _one_line(text: str) -> str:
    return " ".join((text or "").split())


#: A one-line fact longer than this keeps its full text in the note and a gist in the index.
GIST_CHARS = 150


def gist(text: str, limit: int = GIST_CHARS) -> str:
    """The index line of a long fact: its first sentence, or the start cut at a word."""
    text = _one_line(text)
    if len(text) <= limit:
        return text
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    if len(first) <= limit:
        return first
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def short_title(text: str) -> str:
    return gist(text, 60)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class Note:
    name: str
    title: str
    description: str
    type: str = "project"
    body: str = ""
    created: str = ""
    modified: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    # The fields the settings panel and the phone sync have always read.
    @property
    def id(self) -> str:
        return self.name

    @property
    def text(self) -> str:
        return self.description

    @property
    def category(self) -> str:
        return self.type

    @property
    def created_at(self) -> float:
        try:
            return datetime.fromisoformat(self.created).timestamp()
        except ValueError:
            return 0.0

    def render(self) -> str:
        head = {"name": self.name, "title": self.title, "description": self.description, "type": self.type,
                "created": self.created, "modified": self.modified, **self.extra}
        lines = ["---", *(f"{k}: {_one_line(v)}" for k, v in head.items() if v), "---", ""]
        return "\n".join(lines) + self.body.strip() + "\n"

    def index_line(self) -> str:
        return f"- [{self.title}]({self.name}.md) — {self.description}"

    @property
    def fact(self) -> str:
        """The note as one line (the phone keeps facts as lines): a one-line body in full,
        otherwise the description."""
        body = self.body.strip()
        return body if body and "\n" not in body else self.description

    def words(self) -> set[str]:
        return {w.lower() for w in _WORD_RE.findall(f"{self.title} {self.description} {self.body}")}


def parse_note(name: str, text: str) -> Note:
    """A note file → Note. A file without a header is a note whose body is the whole file."""
    head: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                key, sep, value = line.partition(":")
                if sep and key.strip():
                    head[key.strip()] = value.strip()
            body = text[end + 4:].lstrip("\n")
    known = {"name", "title", "description", "type", "created", "modified"}
    first_line = next((ln.strip("# ").strip() for ln in body.splitlines() if ln.strip()), name)
    return Note(
        name=name,
        title=head.get("title") or head.get("name") or name,
        description=head.get("description") or _one_line(first_line)[:160],
        type=head.get("type") if head.get("type") in TYPES else "project",
        body=body.rstrip(),
        created=head.get("created", ""),
        modified=head.get("modified", ""),
        extra={k: v for k, v in head.items() if k not in known},
    )


class MemoryDir:
    """One memory folder: its notes and its index."""

    #: How the prompt names this folder's index.
    label = "memory"
    heading = "Memory"

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self._migrate()

    @property
    def index_path(self) -> Path:
        return self.root / INDEX_NAME

    def path_of(self, name: str) -> Path:
        return self.root / f"{slugify(name)}.md"

    # ------------------------------------------------------------ reading

    def notes(self) -> list[Note]:
        if not self.root.is_dir():
            return []
        notes = []
        for path in sorted(self.root.glob("*.md")):
            if path.name == INDEX_NAME:
                continue
            try:
                notes.append(parse_note(path.stem, path.read_text(encoding="utf-8")))
            except OSError:
                logger.warning("memory note unreadable: %s", path)
        return notes

    def get(self, name: str) -> Note | None:
        path = self.path_of(name)
        try:
            return parse_note(path.stem, path.read_text(encoding="utf-8"))
        except OSError:
            return None

    def raw(self, name: str) -> str | None:
        try:
            return self.path_of(name).read_text(encoding="utf-8")
        except OSError:
            return None

    def index_text(self) -> str:
        try:
            return self.index_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def search(self, query: str, limit: int = 10) -> list[Note]:
        terms = {w.lower() for w in _WORD_RE.findall(query or "")}
        notes = self.notes()
        if not terms:
            return sorted(notes, key=lambda n: n.modified, reverse=True)[:limit]
        scored = []
        for note in notes:
            text = f"{note.title} {note.description} {note.body}".lower()
            hits = sum(1 for t in terms if t in text) + len(terms & note.words())
            if hits:
                scored.append((hits, note.modified, note))
        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        return [note for _, _, note in scored[:limit]]

    def similar(self, title: str, description: str, limit: int = 3) -> list[Note]:
        """Notes that look like the same thing (to update instead of adding a twin)."""
        words = {w.lower() for w in _WORD_RE.findall(f"{title} {description}")}
        if not words:
            return []
        out = []
        for note in self.notes():
            theirs = {w.lower() for w in _WORD_RE.findall(f"{note.title} {note.description}")}
            if theirs and len(words & theirs) / len(words | theirs) >= 0.5:
                out.append(note)
        return out[:limit]

    # ------------------------------------------------------------ writing

    def create(self, title: str, description: str, type_: str, body: str, name: str = "") -> Note:
        """A new note. Raises FileExistsError when the name is taken (update that note instead)."""
        title, description = _one_line(title), _one_line(description)
        if not title or not description:
            raise ValueError("a note needs a title and a one-line description")
        if type_ not in TYPES:
            raise ValueError(f"type must be one of: {', '.join(TYPES)}")
        now = _now_iso()
        note = Note(name=slugify(name or title), title=title, description=description, type=type_,
                    body=body.strip() or description, created=now, modified=now)
        with path_lock(self.index_path):
            if self.path_of(note.name).exists():
                raise FileExistsError(note.name)
            self._write(note)
        return note

    def save(self, note: Note) -> Note:
        """Writes an existing note back; `modified` is stamped here."""
        note.modified = _now_iso()
        note.title, note.description = _one_line(note.title), _one_line(note.description)
        with path_lock(self.index_path):
            self._write(note)
        return note

    def delete(self, name: str) -> bool:
        with path_lock(self.index_path):
            path = self.path_of(name)
            if not path.exists():
                return False
            path.unlink()
            self._write_index()
        return True

    def clear(self) -> int:
        notes = self.notes()
        for note in notes:
            self.delete(note.name)
        return len(notes)

    def _write(self, note: Note) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not atomic_write_text(self.path_of(note.name), note.render()):
            raise OSError(f"could not write the memory note {note.name}")
        self._write_index()

    def _write_index(self) -> None:
        notes = self.notes()
        order = {t: i for i, t in enumerate(TYPES)}
        notes.sort(key=lambda n: (order.get(n.type, 9), n.title.lower()))
        text = f"# {self.heading}\n\n" + "".join(n.index_line() + "\n" for n in notes)
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.index_path, text)

    def index_pressure(self) -> str:
        """A reminder when the index nears or passes what the prompt loads (else "")."""
        text = self.index_text()
        lines, size = len(text.splitlines()), len(text.encode("utf-8"))
        share = max(lines / INDEX_MAX_LINES, size / INDEX_MAX_BYTES)
        if share >= 1:
            return (f"The {self.label} index is over its limit ({lines} lines, {size} bytes; the prompt loads "
                    f"{INDEX_MAX_LINES} lines / {INDEX_MAX_BYTES} bytes): what is past it is not seen. Merge "
                    "related notes, shorten descriptions and delete stale notes now.")
        if share >= INDEX_WARN_SHARE:
            return (f"The {self.label} index is near its limit ({lines} lines, {size} bytes): keep descriptions "
                    "to one short line, merge related notes and delete stale ones.")
        return ""

    # ------------------------------------------------------------ prompt

    def prompt_section(self) -> str:
        text = self.index_text().strip()
        if not text or not any(ln.startswith("- ") for ln in text.splitlines()):
            return ""
        lines = text.splitlines()[:INDEX_MAX_LINES]
        clipped = "\n".join(lines).encode("utf-8")[:INDEX_MAX_BYTES].decode("utf-8", "ignore")
        return f"<{self.label}>\n{clipped}\n</{self.label}>"

    # ------------------------------------------------------------ the old formats

    def _migrate(self) -> None:
        """Hook for the older one-file stores (subclasses)."""

    def _import(self, text: str, type_: str, when: str) -> None:
        base = slugify(" ".join(text.split()[:6]))
        name, n = base, 2
        while self.path_of(name).exists():
            if parse_note(name, self.path_of(name).read_text(encoding="utf-8")).fact == text:
                return  # imported already
            name, n = f"{base}-{n}", n + 1
        note = Note(name=name, title=short_title(text), description=gist(text), type=type_, body=text,
                    created=when, modified=when)
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path_of(name), note.render())


class MemoryStore(MemoryDir):
    """The global memory: `<data dir>/memory/`, shared by every chat."""

    label = "global_memory"
    heading = "Global memory (about the user, across chats)"

    def __init__(self, base_dir: Path | str) -> None:
        self.data_dir = Path(base_dir)
        super().__init__(self.data_dir / "memory")

    def _migrate(self) -> None:
        """The facts of the old `memory.json` become notes, once; the file is kept aside."""
        old = self.data_dir / "memory.json"
        if not old.exists():
            return
        with path_lock(self.index_path):
            if not old.exists():
                return
            try:
                facts = json.loads(old.read_text(encoding="utf-8")).get("facts", [])
            except (OSError, ValueError, AttributeError):
                facts = []
            for fact in facts if isinstance(facts, list) else []:
                text = _one_line(str((fact or {}).get("text") or ""))
                if not text:
                    continue
                when = datetime.fromtimestamp(float(fact.get("created_at") or time.time())).isoformat(timespec="seconds")
                self._import(text, _OLD_CATEGORY.get(str(fact.get("category")), "project"), when)
            try:
                old.replace(old.with_name("memory.json.migrated"))
            except OSError:
                logger.warning("memory.json could not be set aside after the migration")
            self._write_index()

    # --- what the settings panel and the phone sync use --------------------------

    def remember(self, text: str, category: str = "project", session_id: str = "") -> Note | None:
        """A one-line fact as a note (the phone sync, "suggest to remember"). A fact that is
        already there is returned as is."""
        text = _one_line(text)
        if not text:
            return None
        for note in self.notes():
            if text.lower() in (note.fact.lower(), note.description.lower()):
                return note
        type_ = category if category in TYPES else _OLD_CATEGORY.get(category, "project")
        base, n = slugify(" ".join(text.split()[:6])), 2
        name = base
        while self.path_of(name).exists():
            name, n = f"{base}-{n}", n + 1
        return self.create(short_title(text), gist(text), type_, text, name=name)

    def all(self) -> list[Note]:
        return self.notes()

    def forget(self, name: str) -> bool:
        return self.delete(name)

    def recall(self, query: str, limit: int = 10) -> list[Note]:
        return self.search(query, limit)
