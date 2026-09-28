"""Memory tools: notes in the global and the project memory folders, and search in chats.

A note is created with `remember`, read with `memory_read`, and changed or deleted with
`memory_edit` / `memory_delete` — only after it was read in this chat, and only if it has not
changed since (the user may edit the files by hand). The indexes of both folders are in the
system prompt; the notes themselves are read on demand.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from core.chat_search import search_chats
from core.folder_memory import FolderMemory
from core.i18n import tr
from core.memory import TYPES, MemoryDir, MemoryStore, Note, content_hash, slugify
from core.security.approval import ApprovalRequest
from core.tools.base import Tool, ToolContext, ToolResult

#: ctx.scratch key: {note path: hash of what was read} — the read-before-edit rule.
READS_KEY = "_memory_reads"

Scope = Literal["auto", "global", "project"]


def _global(ctx: ToolContext) -> MemoryStore:
    """The run's global memory when there is one, otherwise its own (single tests)."""
    if ctx.memory is not None:
        return ctx.memory
    return MemoryStore(ctx.settings.data_dir)


def _project(ctx: ToolContext) -> FolderMemory:
    return FolderMemory(ctx.settings.workspace)


def _dirs(ctx: ToolContext, scope: str) -> list[tuple[str, MemoryDir]]:
    if scope == "global":
        return [("global", _global(ctx))]
    if scope in ("project", "folder"):
        return [("project", _project(ctx))]
    return [("project", _project(ctx)), ("global", _global(ctx))]


def _find(ctx: ToolContext, name: str, scope: str) -> tuple[str, MemoryDir, Note] | ToolResult:
    hits = [(s, d, n) for s, d in _dirs(ctx, scope) if (n := d.get(name)) is not None]
    if not hits:
        return ToolResult.fail(f"no memory note '{name}' ({scope}); the indexes in your context list them")
    if len(hits) > 1:
        return ToolResult.fail(f"'{name}' is in both the project and the global memory: give scope")
    return hits[0]


def _mark_read(ctx: ToolContext, folder: MemoryDir, name: str) -> None:
    raw = folder.raw(name)
    if raw is not None:
        ctx.scratch.setdefault(READS_KEY, {})[str(folder.path_of(name))] = content_hash(raw)


def _check_read(ctx: ToolContext, folder: MemoryDir, name: str) -> ToolResult | None:
    """The note must have been read in this chat, and be unchanged since."""
    seen = ctx.scratch.get(READS_KEY, {}).get(str(folder.path_of(name)))
    if seen is None:
        return ToolResult.fail(f"read the note first: memory_read name='{name}' (edits need the current text)")
    raw = folder.raw(name)
    if raw is None or content_hash(raw) != seen:
        return ToolResult.fail(f"the note '{name}' changed since you read it: memory_read it again")
    return None


def _pressure(*folders: MemoryDir) -> str:
    notes = [p for f in folders if (p := f.index_pressure())]
    return ("\n" + "\n".join(notes)) if notes else ""


# ------------------------------------------------------------------ remember


class RememberArgs(BaseModel):
    title: str = Field(description="A short title (a few words); it names the note in the index")
    description: str = Field(description="ONE line: the gist, specific enough to judge relevance from the index alone")
    type: Literal["user", "feedback", "project", "reference"] = Field(
        description=(
            "user: who the user is, role, expertise, preferences; feedback: a correction or an approach "
            "the user confirmed; project: ongoing work, decisions, constraints the code does not show; "
            "reference: where to find something outside the project"
        )
    )
    body: str = Field(
        default="",
        description=(
            "The note in full (markdown). For feedback and project end with 'Why:' and 'How to apply:' "
            "lines. Link related notes as [[name]]. Empty = the description."
        ),
    )
    scope: Scope = Field(
        default="auto",
        description="global (all chats) or project (this workspace). auto: user/feedback → global, project/reference → project",
    )


class RememberTool(Tool):
    name = "remember"
    description = (
        "Saves a NEW memory note: a file with a header (dates are added for you) plus one line in the memory "
        "index that is in your context. Before saving, check the index: if a note on this already exists, "
        "memory_read it and update it with memory_edit instead of adding a twin. Save what will help in a "
        "future conversation — not what the code, git history or project docs already say, not momentary "
        "details, never secrets."
    )
    Args = RememberArgs
    category = "read"
    timeout = 15.0

    async def run(self, args: RememberArgs, ctx: ToolContext) -> str | ToolResult:
        scope = args.scope if args.scope != "auto" else ("global" if args.type in ("user", "feedback") else "project")
        folder = _global(ctx) if scope == "global" else _project(ctx)
        twins = folder.similar(args.title, args.description)
        try:
            note = folder.create(args.title, args.description, args.type, args.body)
        except FileExistsError:
            return ToolResult.fail(
                f"a {scope} note '{slugify(args.title)}' exists: memory_read it and update it with memory_edit"
            )
        except ValueError as exc:
            return ToolResult.fail(str(exc))
        _mark_read(ctx, folder, note.name)  # its author knows its text
        out = f"Saved to the {scope} memory: {note.name}.md ({note.type}) — {note.description}"
        if twins:
            out += ("\nSimilar notes already there: " + ", ".join(t.name for t in twins)
                    + ". If this is the same thing, merge them (memory_edit) and delete the extra one.")
        return out + _pressure(folder)


# ------------------------------------------------------------------ read


class MemoryReadArgs(BaseModel):
    name: str = Field(default="", description="The note's name (the file name in the index, without .md); empty = the index")
    scope: Scope = Field(default="auto", description="global | project | auto (look in both)")


class MemoryReadTool(Tool):
    name = "memory_read"
    description = (
        "Reads a memory note in full (header with its dates, and the text), or — without a name — the index. "
        "Read a note before relying on its details, and always before memory_edit or memory_delete. Notes "
        "can be out of date: check a file, function or fact from memory against the present before acting on it."
    )
    Args = MemoryReadArgs
    category = "read"
    timeout = 15.0

    async def run(self, args: MemoryReadArgs, ctx: ToolContext) -> str | ToolResult:
        if not args.name.strip():
            parts = []
            for scope, folder in _dirs(ctx, args.scope):
                text = folder.index_text().strip()
                parts.append(f"[{scope} memory — {folder.root}]\n{text or '(empty)'}")
            return "\n\n".join(parts)
        found = _find(ctx, args.name.strip().removesuffix(".md"), args.scope)
        if isinstance(found, ToolResult):
            return found
        scope, folder, note = found
        raw = folder.raw(note.name) or ""
        _mark_read(ctx, folder, note.name)
        return f"[{scope} memory: {folder.path_of(note.name)}]\n{raw}"


# ------------------------------------------------------------------ edit


class MemoryEditArgs(BaseModel):
    name: str = Field(description="The note's name (from the index)")
    scope: Scope = Field(default="auto", description="global | project | auto")
    old_text: str = Field(default="", description="Exact text in the note's body to replace (with new_text)")
    new_text: str = Field(default="", description="The replacement for old_text")
    body: str | None = Field(default=None, description="Or the whole new body (instead of old_text/new_text)")
    title: str | None = Field(default=None, description="A new title (optional)")
    description: str | None = Field(default=None, description="A new one-line description for the index (optional)")
    type: Literal["user", "feedback", "project", "reference"] | None = Field(default=None, description="A new type (optional)")


class MemoryEditTool(Tool):
    name = "memory_edit"
    description = (
        "Changes a memory note you have READ in this chat with memory_read (refused otherwise, or if the note "
        "changed since): replace old_text with new_text in its body, or give the whole new body; optionally a "
        "new title, one-line description or type. The index line and the modified date update by themselves."
    )
    Args = MemoryEditArgs
    category = "edit"
    timeout = 15.0

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.mem_edit", name=args.name)

    async def run(self, args: MemoryEditArgs, ctx: ToolContext) -> str | ToolResult:
        found = _find(ctx, args.name.strip().removesuffix(".md"), args.scope)
        if isinstance(found, ToolResult):
            return found
        scope, folder, note = found
        refused = _check_read(ctx, folder, note.name)
        if refused:
            return refused
        if args.body is not None:
            note.body = args.body
        elif args.old_text:
            count = note.body.count(args.old_text)
            if count != 1:
                return ToolResult.fail(
                    "old_text is not in the note" if count == 0 else f"old_text is in the note {count} times: make it unique"
                )
            note.body = note.body.replace(args.old_text, args.new_text, 1)
        elif args.title is None and args.description is None and args.type is None:
            return ToolResult.fail("nothing to change: give old_text/new_text, body, title, description or type")
        if args.title is not None and args.title.strip():
            note.title = args.title
        if args.description is not None and args.description.strip():
            note.description = args.description
        if args.type is not None:
            note.type = args.type
        folder.save(note)
        _mark_read(ctx, folder, note.name)
        return f"Updated the {scope} note {note.name}.md — {note.description}" + _pressure(folder)


class MemoryDeleteArgs(BaseModel):
    name: str = Field(description="The note's name (from the index)")
    scope: Scope = Field(default="auto", description="global | project | auto")


class MemoryDeleteTool(Tool):
    name = "memory_delete"
    description = (
        "Deletes a memory note that turned out wrong or stale — one you have READ in this chat with memory_read "
        "(refused otherwise). Its index line goes too."
    )
    Args = MemoryDeleteArgs
    category = "edit"
    timeout = 15.0

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.mem_delete", name=args.name)

    async def run(self, args: MemoryDeleteArgs, ctx: ToolContext) -> str | ToolResult:
        found = _find(ctx, args.name.strip().removesuffix(".md"), args.scope)
        if isinstance(found, ToolResult):
            return found
        scope, folder, note = found
        refused = _check_read(ctx, folder, note.name)
        if refused:
            return refused
        folder.delete(note.name)
        ctx.scratch.get(READS_KEY, {}).pop(str(folder.path_of(note.name)), None)
        return f"Deleted the {scope} note {note.name}.md"


# ------------------------------------------------------------------ recall


class RecallArgs(BaseModel):
    query: str = Field(default="", description="What to look for in the notes' text (empty: the most recent)")
    scope: Scope = Field(default="auto", description="global | project | auto (both)")
    limit: int = Field(default=8, ge=1, le=30)


class RecallTool(Tool):
    name = "recall"
    description = (
        "Searches the text of all memory notes (not only the index lines in your context) and lists the "
        "matches with their names; open one with memory_read."
    )
    Args = RecallArgs
    category = "read"
    timeout = 15.0

    async def run(self, args: RecallArgs, ctx: ToolContext) -> str:
        lines = []
        for scope, folder in _dirs(ctx, args.scope):
            for note in folder.search(args.query, args.limit):
                lines.append(f"- [{scope}] {note.name} ({note.type}, modified {note.modified[:10]}): {note.description}")
        return "Memory notes:\n" + "\n".join(lines[: args.limit]) if lines else "Nothing in memory matches."


# --------------------------------------------------------------- search_chats


class SearchChatsArgs(BaseModel):
    query: str = Field(description="What to look for in the conversations")
    scope: Literal["all", "current"] = Field(default="all", description="all chats, or only the current one")
    limit: int = Field(default=8, ge=1, le=20)


class SearchChatsTool(Tool):
    name = "search_chats"
    description = (
        "Searches the saved chats — this one and others: where something was discussed, what you answered "
        "before. Returns matching fragments with the chat's title. Use it instead of asking again what was "
        "already settled in earlier conversations."
    )
    Args = SearchChatsArgs
    category = "read"
    timeout = 20.0

    async def run(self, args: SearchChatsArgs, ctx: ToolContext) -> str:
        hits = search_chats(
            ctx.settings.storage_dir,
            args.query,
            current_session_id=ctx.run_id,
            scope=args.scope,
            limit=args.limit,
        )
        if not hits:
            where = "in this chat" if args.scope == "current" else "in the chats"
            return f"Nothing found {where} for '{args.query}'."
        blocks = [f"[{hit.title}] ({hit.role}): {hit.snippet}" for hit in hits]
        return f"Found in the conversations for '{args.query}':\n\n" + "\n\n".join(blocks)


# --------------------------------------------------------------- suggest_memory


class SuggestMemoryArgs(BaseModel):
    title: str = Field(description="A short title")
    description: str = Field(description="The fact in one line")
    type: Literal["user", "feedback", "project", "reference"] = Field(default="user")
    body: str = Field(default="", description="Details (optional)")
    scope: Scope = Field(default="auto", description="global | project | auto")


class SuggestMemoryTool(Tool):
    name = "suggest_memory"
    description = (
        "Proposes a memory note to the user, who confirms or declines it. Use it for facts ABOUT THE USER and "
        "agreements, so they see and control what is remembered. For your own working notes use remember."
    )
    Args = SuggestMemoryArgs
    category = "read"
    timeout = None  # waits for the person

    async def run(self, args: SuggestMemoryArgs, ctx: ToolContext) -> str | ToolResult:
        description = " ".join(args.description.split())
        if not description:
            return ToolResult.fail("nothing to save")
        approved = await ctx.approver(
            ApprovalRequest(
                name="suggest_memory",
                args={"title": args.title, "description": description, "scope": args.scope},
                reason=tr("appr.mem_suggest", text=description),
                category="edit",
            )
        )
        if not approved:
            return "The user chose not to save this."
        return await RememberTool().run(
            RememberArgs(title=args.title or description[:60], description=description, type=args.type,
                         body=args.body, scope=args.scope), ctx)


__all__ = [
    "TYPES",
    "MemoryDeleteTool",
    "MemoryEditTool",
    "MemoryReadTool",
    "RecallTool",
    "RememberTool",
    "SearchChatsTool",
    "SuggestMemoryTool",
]
