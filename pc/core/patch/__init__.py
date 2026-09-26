"""Patch engine: lenient parsing (unified diff + V4A) and application."""

from core.patch.applier import apply_file_patch, apply_hunks, find_unique_block, split_lines
from core.patch.model import (
    FileAction,
    FilePatch,
    FileResult,
    Hunk,
    HunkResult,
    LineOp,
    PatchLine,
)
from core.patch.parser import parse_patch, parse_unified_diff

__all__ = [
    "FileAction",
    "FilePatch",
    "FileResult",
    "Hunk",
    "HunkResult",
    "LineOp",
    "PatchLine",
    "apply_file_patch",
    "apply_hunks",
    "find_unique_block",
    "parse_patch",
    "parse_unified_diff",
    "split_lines",
]
