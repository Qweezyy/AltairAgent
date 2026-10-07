"""A fingerprint of the working tree: which files, how big, last changed when.

The checks of a run say something about one state of the folder. If the folder changes after
the checks finished and before the run's verdict, "the checks passed" no longer speaks for what
is delivered — the verdict must say "unknown", never "passed" (a reviewer's point: the witness
must see the tree the evidence is about). Size and modification time catch every edit, and the
walk stays fast on big projects; content is not read.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

#: Folders that are not the delivered work: tools' caches, dependencies, version control.
SKIP_DIRS = frozenset({".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
                       ".mypy_cache", ".ruff_cache", ".tox", ".nox", ".gradle", ".next", ".nuxt", ".turbo",
                       ".parcel-cache", ".cache", ".agent"})
#: Files the checks themselves write (their own results), not part of the work.
SKIP_FILES = frozenset({".coverage", "coverage.xml", ".DS_Store", "Thumbs.db"})


def tree_fingerprint(root: Path | str) -> str:
    """sha256 over (relative path, size, mtime) of every file outside the skipped folders."""
    base = Path(root)
    h = hashlib.sha256()
    entries: list[str] = []
    for folder, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name in SKIP_FILES or name.endswith((".pyc", ".tmp")):
                continue
            path = Path(folder) / name
            try:
                st = path.stat()
            except OSError:
                continue
            rel = str(path.relative_to(base)).replace("\\", "/")
            entries.append(f"{rel}\0{st.st_size}\0{st.st_mtime_ns}")
    for entry in sorted(entries):
        h.update(entry.encode("utf-8", errors="surrogateescape"))
        h.update(b"\n")
    return h.hexdigest()[:16]
