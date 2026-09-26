"""Инструменты долгосрочной памяти и поиска по чатам."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from core.chat_search import search_chats
from core.folder_memory import FolderMemory
from core.memory import CATEGORIES, MemoryStore
from core.security.approval import ApprovalRequest
from core.tools.base import Tool, ToolContext, ToolResult


def _memory(ctx: ToolContext) -> MemoryStore:
    """Хранилище памяти: общее на запуск, иначе — своё (для отдельных тестов)."""
    if ctx.memory is not None:
        return ctx.memory
    return MemoryStore(ctx.settings.data_dir)


def _bullet_text(line: str) -> str:
    """Текст пункта памяти папки без ведущего «- » (для показа модели)."""
    return line.lstrip().removeprefix("- ").strip()


# ------------------------------------------------------------------ remember


class RememberArgs(BaseModel):
    text: str = Field(description="A short fact or lesson to remember (one statement)")
    category: Literal["user", "preference", "project", "fact"] = Field(
        default="fact",
        description=(
            "user: about the user (name, role, city); preference: how they like you to work; "
            "project: about the project/codebase; fact: any other durable fact or lesson"
        ),
    )
    scope: Literal["auto", "global", "folder"] = Field(
        default="auto",
        description=(
            "Where to save. auto: user/preference go to global memory (all chats), project/fact "
            "to the folder memory (memory.md next to the project). global/folder force it."
        ),
    )


class RememberTool(Tool):
    name = "remember"
    description = (
        "Saves a short durable fact or lesson to memory: about the user and their preferences to "
        "global memory (all chats), about this project and lessons learned to the folder memory "
        "(loaded into context automatically). Not for momentary details, secrets or anything easy "
        "to re-read in the code."
    )
    Args = RememberArgs
    category = "read"
    timeout = 15.0

    async def run(self, args: RememberArgs, ctx: ToolContext) -> str:
        scope = args.scope
        if scope == "auto":
            scope = "global" if args.category in ("user", "preference") else "folder"

        if scope == "folder":
            from core.folder_memory import FolderMemory

            ok = FolderMemory(ctx.settings.workspace).append(args.text, args.category)
            if not ok:
                return "Нечего запоминать (пусто или уже записано в памяти папки)."
            return f"Записал в память папки ({args.category}): {args.text.strip()}"

        fact = _memory(ctx).remember(args.text, args.category, session_id=ctx.run_id)
        if fact is None:
            return "Пустой факт — нечего запоминать."
        return f"Запомнил в глобальной памяти ({fact.category}): {fact.text}"


# -------------------------------------------------------------------- recall


class RecallArgs(BaseModel):
    query: str = Field(default="", description="О чём вспомнить (пусто — последние факты)")
    limit: int = Field(default=10, ge=1, le=30)


class RecallTool(Tool):
    name = "recall"
    description = (
        "Ищет в долгосрочной памяти факты о пользователе и проектах по теме запроса. "
        "Часть памяти и так подставляется автоматически; этот инструмент — чтобы копнуть глубже."
    )
    Args = RecallArgs
    category = "read"
    timeout = 15.0

    async def run(self, args: RecallArgs, ctx: ToolContext) -> str:
        facts = _memory(ctx).recall(args.query, limit=args.limit)
        if not facts:
            return "В памяти ничего подходящего не нашлось."
        lines = [f"- [{f.category}] {f.text}" for f in facts]
        return "Из памяти:\n" + "\n".join(lines)


# --------------------------------------------------------------- search_chats


class SearchChatsArgs(BaseModel):
    query: str = Field(description="Что искать в переписке")
    scope: Literal["all", "current"] = Field(
        default="all", description="all — по всем чатам, current — только по текущему"
    )
    limit: int = Field(default=8, ge=1, le=20)


class SearchChatsTool(Tool):
    name = "search_chats"
    description = (
        "Ищет по сохранённым чатам — этому и другим: где что обсуждалось, что ты отвечал раньше. "
        "Возвращает совпавшие фрагменты с названием чата. Полезно, чтобы не переспрашивать то, "
        "что уже решали в прошлых разговорах."
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
            where = "в текущем чате" if args.scope == "current" else "в чатах"
            return f"По запросу «{args.query}» {where} ничего не нашлось."

        blocks = []
        for hit in hits:
            blocks.append(f"[{hit.title}] ({hit.role}): {hit.snippet}")
        return f"Найдено в переписке по «{args.query}»:\n\n" + "\n\n".join(blocks)


# ---------------------------------------------- курирование памяти (view/remove/replace)
#
# Память ПК двухуровневая: global — memory.json (кросс-чат, структурные факты), folder —
# .agent/memory.md (память рабочей папки). Оба уровня подмешиваются в системный промпт,
# поэтому модель должна уметь их не только пополнять, но и просматривать/чистить/править.


Scope = Literal["global", "folder"]


class MemoryViewArgs(BaseModel):
    scope: Scope = Field(
        default="global", description="global — общая память (кросс-чат); folder — память рабочей папки"
    )


class MemoryViewTool(Tool):
    name = "memory_view"
    description = (
        "Показывает пункты памяти с номерами. scope: global (общая, кросс-чат) | folder "
        "(память рабочей папки). Используй перед memory_remove/memory_replace, чтобы узнать номера."
    )
    Args = MemoryViewArgs
    category = "read"
    timeout = 15.0

    async def run(self, args: MemoryViewArgs, ctx: ToolContext) -> str:
        if args.scope == "global":
            facts = _memory(ctx).all()
            if not facts:
                return "Глобальная память пуста."
            return "\n".join(f"{i}. [{f.category}] {f.text}" for i, f in enumerate(facts, 1))
        lines = FolderMemory(ctx.settings.workspace).bullet_lines()
        if not lines:
            return "Память папки пуста."
        return "\n".join(f"{i}. {_bullet_text(ln)}" for i, ln in enumerate(lines, 1))


class MemoryRemoveArgs(BaseModel):
    scope: Scope = Field(default="global", description="global | folder")
    index: int | None = Field(default=None, ge=1, description="Номер пункта из memory_view")
    contains: str = Field(default="", description="Подстрока для поиска пункта (вместо index)")


class MemoryRemoveTool(Tool):
    name = "memory_remove"
    description = (
        "Удаляет пункт памяти. Укажи index (номер из memory_view) ИЛИ contains (подстрока). "
        "scope: global | folder. Полезно, когда факт устарел или записан по ошибке."
    )
    Args = MemoryRemoveArgs
    category = "edit"
    timeout = 15.0

    async def run(self, args: MemoryRemoveArgs, ctx: ToolContext) -> str | ToolResult:
        if args.index is None and not args.contains.strip():
            return ToolResult.fail("нужен index или contains")
        if args.scope == "global":
            store = _memory(ctx)
            facts = store.all()
            target = _pick(facts, args.index, args.contains, key=lambda f: f.text)
            if target is None:
                return ToolResult.fail("пункт не найден (проверь через memory_view)")
            store.forget(target.id)
            return f"Удалено из глобальной памяти: {target.text}"
        fm = FolderMemory(ctx.settings.workspace)
        lines = fm.bullet_lines()
        target = _pick(lines, args.index, args.contains, key=_bullet_text)
        if target is None:
            return ToolResult.fail("пункт не найден (проверь через memory_view)")
        lines.remove(target)
        fm.write_bullets(lines)
        return f"Удалено из памяти папки: {_bullet_text(target)}"


class MemoryReplaceArgs(BaseModel):
    scope: Scope = Field(default="global", description="global | folder")
    index: int | None = Field(default=None, ge=1, description="Номер пункта (вариант A)")
    new_text: str = Field(default="", description="Новый текст пункта (вариант A)")
    find: str = Field(default="", description="Что искать (вариант B: замена подстроки)")
    replace: str = Field(default="", description="На что заменить (вариант B)")


class MemoryReplaceTool(Tool):
    name = "memory_replace"
    description = (
        "Меняет пункт памяти. Вариант A: index + new_text (заменить весь пункт целиком). "
        "Вариант B: find + replace (замена подстроки во всех пунктах). scope: global | folder."
    )
    Args = MemoryReplaceArgs
    category = "edit"
    timeout = 15.0

    async def run(self, args: MemoryReplaceArgs, ctx: ToolContext) -> str | ToolResult:
        by_index = args.index is not None and args.new_text.strip()
        by_find = bool(args.find.strip())
        if not by_index and not by_find:
            return ToolResult.fail("укажи index+new_text (вариант A) или find+replace (вариант B)")

        if args.scope == "global":
            return await self._replace_global(args, ctx, by_index)
        return self._replace_folder(args, ctx, by_index)

    async def _replace_global(self, args: MemoryReplaceArgs, ctx: ToolContext, by_index: bool) -> str | ToolResult:
        store = _memory(ctx)
        facts = store.all()
        if by_index:
            if not 1 <= args.index <= len(facts):  # type: ignore[operator]
                return ToolResult.fail(f"нет пункта №{args.index}")
            old = facts[args.index - 1]
            store.forget(old.id)
            store.remember(args.new_text.strip(), category=old.category, session_id=ctx.run_id)
            return f"Пункт №{args.index} обновлён."
        changed = 0
        for fact in facts:
            if args.find in fact.text:
                store.forget(fact.id)
                store.remember(fact.text.replace(args.find, args.replace), category=fact.category, session_id=ctx.run_id)
                changed += 1
        if not changed:
            return ToolResult.fail(f"подстрока '{args.find}' не найдена")
        return f"Заменено пунктов: {changed}."

    def _replace_folder(self, args: MemoryReplaceArgs, ctx: ToolContext, by_index: bool) -> str | ToolResult:
        fm = FolderMemory(ctx.settings.workspace)
        lines = fm.bullet_lines()
        if by_index:
            if not 1 <= args.index <= len(lines):  # type: ignore[operator]
                return ToolResult.fail(f"нет пункта №{args.index}")
            lines[args.index - 1] = f"- {args.new_text.strip()}"
            fm.write_bullets(lines)
            return f"Пункт №{args.index} обновлён."
        changed = 0
        new_lines = []
        for ln in lines:
            if args.find in ln:
                changed += 1
                new_lines.append(ln.replace(args.find, args.replace))
            else:
                new_lines.append(ln)
        if not changed:
            return ToolResult.fail(f"подстрока '{args.find}' не найдена")
        fm.write_bullets(new_lines)
        return f"Заменено пунктов: {changed}."


class SuggestMemoryArgs(BaseModel):
    text: str = Field(description="Короткий факт для сохранения (одно утверждение)")
    scope: Scope = Field(default="global", description="global | folder")


class SuggestMemoryTool(Tool):
    name = "suggest_memory"
    description = (
        "Предлагает пользователю сохранить факт в память — он подтвердит или отклонит. Используй "
        "для фактов О ПОЛЬЗОВАТЕЛЕ и договорённостей, чтобы он видел и контролировал, что "
        "запоминается. Для внутренних заметок без подтверждения — remember. scope: global | folder."
    )
    Args = SuggestMemoryArgs
    category = "read"
    timeout = None  # ждём решения человека

    async def run(self, args: SuggestMemoryArgs, ctx: ToolContext) -> str | ToolResult:
        text = " ".join(args.text.split()).strip()
        if not text:
            return ToolResult.fail("нечего сохранять")
        approved = await ctx.approver(
            ApprovalRequest(
                name="suggest_memory",
                args={"text": text, "scope": args.scope},
                reason=f"Сохранить в память ({args.scope}): «{text}»?",
                category="edit",
            )
        )
        if not approved:
            return "Пользователь решил не сохранять этот факт."
        if args.scope == "folder":
            FolderMemory(ctx.settings.workspace).append(text, "fact")
            return f"Пользователь подтвердил — сохранено в память папки: {text}"
        _memory(ctx).remember(text, category="user", session_id=ctx.run_id)
        return f"Пользователь подтвердил — сохранено в глобальную память: {text}"


def _pick(items: list, index: int | None, contains: str, key) -> Any:
    """Выбирает элемент по номеру (1-based) или по подстроке. None — не найден."""
    if index is not None and 1 <= index <= len(items):
        return items[index - 1]
    needle = contains.strip().lower()
    if needle:
        for item in items:
            if needle in key(item).lower():
                return item
    return None


__all__ = [
    "CATEGORIES",
    "MemoryRemoveTool",
    "MemoryReplaceTool",
    "MemoryViewTool",
    "RecallTool",
    "RememberTool",
    "SearchChatsTool",
    "SuggestMemoryTool",
]
