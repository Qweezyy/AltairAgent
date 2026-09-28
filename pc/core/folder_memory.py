"""The memory of a workspace: `<workspace>/.agent/memory/`, laid out like the global memory
(core/memory.py): an index `MEMORY.md` and one note per file. Notes about this project —
decisions, constraints, lessons learned — live next to the project, readable by a human.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from core.fs_atomic import path_lock
from core.logging_setup import get_logger
from core.memory import _OLD_CATEGORY, MemoryDir

logger = get_logger("folder_memory")

_BULLET = re.compile(r"^- \[(?P<cat>\w+)\] (?P<date>\d{4}-\d{2}-\d{2}): (?P<text>.+)$")


class FolderMemory(MemoryDir):
    label = "project_memory"
    heading = "Project memory (this workspace)"

    def __init__(self, workspace: Path | str) -> None:
        self.workspace = Path(workspace)
        super().__init__(self.workspace / ".agent" / "memory")

    def _migrate(self) -> None:
        """The bullets of the old `.agent/memory.md` become notes, once; the file is kept aside."""
        old = self.workspace / ".agent" / "memory.md"
        if not old.exists():
            return
        with path_lock(self.index_path):
            if not old.exists():
                return
            try:
                lines = old.read_text(encoding="utf-8").splitlines()
            except OSError:
                return
            for line in lines:
                if not line.startswith("- "):
                    continue
                m = _BULLET.match(line.strip())
                text = m.group("text") if m else line[2:].strip()
                cat = m.group("cat") if m else "project"
                date = m.group("date") if m else datetime.now().date().isoformat()
                if text.strip():
                    self._import(text.strip(), _OLD_CATEGORY.get(cat, "project"), f"{date}T00:00:00")
            try:
                old.replace(old.with_name("memory.md.migrated"))
            except OSError:
                logger.warning(".agent/memory.md could not be set aside after the migration")
            self._write_index()
