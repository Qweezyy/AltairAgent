"""File editing through patches (unified diff or V4A).

Why it exists next to write_file and edit_file:
  * one call changes several places in several files;
  * only the changed lines go into the context, not the whole file — on large
    files this saves most of the tokens;
  * untouched code physically cannot be clobbered: only the hunks are applied.

Two input formats, because models are trained on different ones: OpenAI models
write V4A (`*** Begin Patch`) natively, most others write unified diff.

Application is atomic: if any hunk fails, nothing is written to disk. Half of a
refactored project is worse than a clear error.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, Field

from core.errors import ToolError
from core.i18n import tr
from core.patch import FileAction, FilePatch, apply_file_patch, parse_patch
from core.patch.model import FileResult
from core.security.paths import resolve_path, safe_relpath
from core.tools.base import Tool, ToolContext, ToolResult
from core.tools.checkpointing import snapshot_before_change
from core.utils.text import looks_binary, read_text_file

EXAMPLE_UNIFIED = """--- a/core/app.py
+++ b/core/app.py
@@ def run():
     logger.info("start")
-    timeout = 30
+    timeout = 60
     return timeout"""

EXAMPLE_V4A = """*** Begin Patch
*** Update File: core/app.py
@@ def run():
     logger.info("start")
-    timeout = 30
+    timeout = 60
     return timeout
*** End Patch"""


class ApplyPatchArgs(BaseModel):
    patch: str = Field(
        description=(
            "The patch, in either format. Unified diff: per file '--- a/path' and '+++ b/path', "
            "then '@@' hunks; new file '--- /dev/null', delete '+++ /dev/null'. V4A: "
            "'*** Begin Patch', then '*** Update File: path' / '*** Add File: path' / "
            "'*** Delete File: path' (optional '*** Move to: new/path'), '@@ <scope line>' "
            "anchors, '*** End Patch'. In both: context lines start with a space, added with "
            "'+', removed with '-'. Line numbers are optional and may be approximate — the "
            "2-3 context lines around each change are what locate it."
        )
    )
    dry_run: bool = Field(default=False, description="Only check that the patch applies; write nothing")


class ApplyPatchTool(Tool):
    name = "apply_patch"
    description = (
        "Applies a multi-hunk / multi-file patch (unified diff or V4A) atomically: all hunks or none. "
        "Best for changes in several places or files at once, and for creating, deleting or moving "
        "files in the same step. For a single localized change edit_file is simpler. Read the "
        "relevant fragments first so the context matches."
    )
    Args = ApplyPatchArgs
    category = "edit"
    dangerous = True
    timeout = 60.0

    def approval_reason(self, args: ApplyPatchArgs) -> str:  # type: ignore[override]
        try:
            patches = parse_patch(args.patch)
        except ToolError:
            return tr("appr.patch_unparsed")
        files = ", ".join(f"{p.path} (+{p.added}/-{p.removed})" for p in patches[:6])
        more = tr("appr.patch_more", n=len(patches) - 6) if len(patches) > 6 else ""
        return tr("appr.patch", files=files, more=more)

    async def run(self, args: ApplyPatchArgs, ctx: ToolContext) -> ToolResult:
        patches = parse_patch(args.patch)

        # (target path, source path to read, patch). They differ only for a move.
        targets: list[tuple[Path, Path, FilePatch]] = []
        for patch in patches:
            target = resolve_path(patch.path, settings=ctx.settings)
            source = target
            if patch.action is FileAction.RENAME:
                if not patch.old_path:
                    raise ToolError(f"Move to '{patch.path}' has no source file.")
                source = resolve_path(patch.old_path, settings=ctx.settings)
            targets.append((target, source, patch))

        def work() -> tuple[list[FileResult], list[tuple[Path, Path, FileResult]]]:
            results: list[FileResult] = []
            writes: list[tuple[Path, Path, FileResult]] = []

            for target, source, patch in targets:
                original: str | None = None
                if source.exists():
                    if source.is_dir():
                        raise ToolError(f"'{patch.old_path or patch.path}' is a directory.")
                    if looks_binary(source):
                        raise ToolError(f"'{patch.old_path or patch.path}' is a binary file; a patch cannot apply.")
                    original = read_text_file(source)
                if patch.action is FileAction.RENAME and target.exists() and target != source:
                    raise ToolError(f"Cannot move to '{patch.path}': the file already exists.")

                result = apply_file_patch(original, patch)
                results.append(result)
                if result.applied:
                    writes.append((target, source, result))
            return results, writes

        results, writes = await asyncio.to_thread(work)

        failed = [r for r in results if not r.applied]
        if failed:
            return ToolResult(content=self._failure_report(failed, results), ok=False)

        if args.dry_run:
            return ToolResult(content=self._success_report(results, ctx, dry_run=True))

        # Snapshot every affected file before writing — a patch changes several files
        # at once, and rollback must work for any of them.
        op = {FileAction.CREATE: "patch", FileAction.DELETE: "delete"}
        for target, source, result in writes:
            await snapshot_before_change(ctx, target, op.get(result.action, "patch"))
            if source != target:
                await snapshot_before_change(ctx, source, "delete")

        def commit() -> None:
            for target, source, result in writes:
                if result.action is FileAction.DELETE:
                    target.unlink(missing_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                # newline="" — the applier already used the source file's line endings.
                target.write_text(result.new_content or "", encoding="utf-8", newline="")
                if source != target:
                    source.unlink(missing_ok=True)

        await asyncio.to_thread(commit)
        return ToolResult(content=self._success_report(results, ctx, dry_run=False))

    # ------------------------------------------------------------------

    def _success_report(self, results: list[FileResult], ctx: ToolContext, *, dry_run: bool) -> str:
        head = "Patch check passed (no files changed):" if dry_run else "Patch applied:"
        lines = [head]
        for result in results:
            action = {
                FileAction.CREATE: "created",
                FileAction.DELETE: "deleted",
                FileAction.MODIFY: "modified",
                FileAction.RENAME: "moved",
            }[result.action]
            path = safe_relpath(resolve_path(result.path, settings=ctx.settings), ctx.settings)
            lines.append(f"  {path}: {action} (+{result.added}/-{result.removed})")

            shifted = [h for h in result.hunks if h.applied and h.strategy != "exact"]
            for hunk in shifted:
                note = {
                    "offset": f"shifted by {hunk.offset:+d} lines",
                    "whitespace": "matched ignoring indentation",
                    "search": "found by searching the file",
                    "insert": "insertion",
                    "create": "new file",
                }.get(hunk.strategy, hunk.strategy)
                lines.append(f"    hunk #{hunk.index}: {note}")

        if any(h.strategy in ("whitespace", "search") for r in results for h in r.hunks):
            lines.append(
                "  Note: some hunks matched loosely — re-read the changed spots with read_file "
                "to confirm they landed where intended."
            )
        return "\n".join(lines)

    def _failure_report(self, failed: list[FileResult], all_results: list[FileResult]) -> str:
        lines = ["Patch NOT applied — nothing on disk changed.", ""]
        for result in failed:
            lines.append(f"File {result.path}: {result.error}")
            for hunk in result.failed_hunks:
                lines.append(f"  Hunk #{hunk.index}: {hunk.error}")
            lines.append("")

        applied = [r.path for r in all_results if r.applied]
        if applied:
            lines.append(f"The other files ({', '.join(applied)}) were not written either — the patch is atomic.")
        lines.append(
            "Next step: read the failing spot with read_file and rebuild the patch with the exact "
            "context (2-3 lines before and after the change). Either format works:\n"
            f"{EXAMPLE_UNIFIED}\n\nor\n\n{EXAMPLE_V4A}"
        )
        return "\n".join(lines)
