"""Инструменты работы с базой данных (SQLite): схема, запросы, ER-диаграмма.

Читающие запросы идут без подтверждения и через read-only подключение —
случайно испортить данные нельзя. Изменяющие (INSERT/UPDATE/CREATE/…) требуют
подтверждения и открывают БД на запись.
"""

from __future__ import annotations

import asyncio
import sqlite3

from pydantic import BaseModel, Field

from core.database import (
    is_write_sql,
    mermaid_er,
    open_readonly,
    run_query,
    schema_overview,
)
from core.database.sqlite_db import open_readwrite
from core.i18n import tr
from core.security.paths import resolve_path
from core.tools.base import Tool, ToolContext, ToolResult

#: Максимум строк в ответе, чтобы не забить контекст.
_MAX_ROWS = 100
_MAX_CELL = 200


class DbSchemaArgs(BaseModel):
    path: str = Field(description="Путь к файлу SQLite-базы в рабочей папке (.db/.sqlite)")


class DbSchemaTool(Tool):
    name = "db_schema"
    description = (
        "Показывает структуру SQLite-базы: таблицы, колонки с типами, первичные и внешние ключи, "
        "число строк. Вызывай первым при работе с незнакомой базой, чтобы понять её устройство."
    )
    Args = DbSchemaArgs
    category = "read"
    timeout = 30.0

    async def run(self, args: DbSchemaArgs, ctx: ToolContext) -> ToolResult:
        path = resolve_path(args.path, settings=ctx.settings, must_exist=True, must_be_file=True)

        def work() -> str:
            conn = open_readonly(path)
            try:
                tables = schema_overview(conn)
            finally:
                conn.close()
            if not tables:
                return "В базе нет таблиц."
            lines = [f"База {path.name}: таблиц {len(tables)}", ""]
            for t in tables:
                count = "?" if t.row_count < 0 else t.row_count
                lines.append(f"■ {t.name} ({count} строк)")
                for c in t.columns:
                    flags = []
                    if c.pk:
                        flags.append("PK")
                    if c.notnull:
                        flags.append("NOT NULL")
                    suffix = f" [{', '.join(flags)}]" if flags else ""
                    lines.append(f"    {c.name}: {c.type or 'TEXT'}{suffix}")
                for fk in t.foreign_keys:
                    lines.append(f"    ↳ {fk.column} → {fk.ref_table}.{fk.ref_column}")
            return "\n".join(lines)

        try:
            return ToolResult(content=await asyncio.to_thread(work))
        except sqlite3.Error as exc:
            return ToolResult.fail(f"Не удалось прочитать базу: {exc}")


class DbQueryArgs(BaseModel):
    path: str = Field(description="Путь к файлу SQLite-базы в рабочей папке")
    sql: str = Field(description="SQL-запрос. SELECT — без подтверждения; изменяющие — с подтверждением")
    limit: int = Field(default=100, ge=1, le=1000, description="Максимум строк в ответе")


class DbQueryTool(Tool):
    name = "db_query"
    description = (
        "Выполняет SQL-запрос к SQLite-базе. SELECT/PRAGMA/EXPLAIN идут через безопасное "
        "read-only подключение и без подтверждения; изменяющие запросы (INSERT/UPDATE/DELETE/DDL) "
        "требуют подтверждения. Возвращает строки таблицей. Используй EXPLAIN QUERY PLAN для оценки."
    )
    Args = DbQueryArgs
    category = "execute"
    dangerous = True
    timeout = 60.0

    def approval_reason(self, args: DbQueryArgs) -> str:  # type: ignore[override]
        return tr("appr.sql", path=args.path, sql=args.sql[:200])

    def auto_verdict(self, args: DbQueryArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        # Только чтение — безопасно (и подключение всё равно read-only).
        return "ask" if is_write_sql(args.sql) else "allow"

    async def run(self, args: DbQueryArgs, ctx: ToolContext) -> ToolResult:
        path = resolve_path(args.path, settings=ctx.settings, must_exist=True, must_be_file=True)
        write = is_write_sql(args.sql)
        limit = min(args.limit, _MAX_ROWS)

        def work() -> ToolResult:
            conn = open_readwrite(path) if write else open_readonly(path)
            try:
                columns, rows = run_query(conn, args.sql, limit=limit)
            finally:
                conn.close()
            if not columns:
                return ToolResult(content="Запрос выполнен. Данных на возврат нет.")
            return ToolResult(content=_render_table(columns, rows, limit))

        try:
            return await asyncio.to_thread(work)
        except sqlite3.Error as exc:
            return ToolResult.fail(f"Ошибка SQL: {exc}")


class DbDiagramArgs(BaseModel):
    path: str = Field(description="Путь к файлу SQLite-базы в рабочей папке")


class DbDiagramTool(Tool):
    name = "db_diagram"
    description = (
        "Строит ER-диаграмму SQLite-базы в формате Mermaid (таблицы, колонки, связи по внешним "
        "ключам). Удобно, чтобы наглядно показать устройство базы. Верни результат пользователю "
        "в блоке ```mermaid."
    )
    Args = DbDiagramArgs
    category = "read"
    timeout = 30.0

    async def run(self, args: DbDiagramArgs, ctx: ToolContext) -> ToolResult:
        path = resolve_path(args.path, settings=ctx.settings, must_exist=True, must_be_file=True)

        def work() -> str:
            conn = open_readonly(path)
            try:
                tables = schema_overview(conn)
            finally:
                conn.close()
            if not tables:
                return "В базе нет таблиц — диаграмму строить не из чего."
            diagram = mermaid_er(tables)
            return f"ER-диаграмма базы {path.name}:\n\n```mermaid\n{diagram}\n```"

        try:
            return ToolResult(content=await asyncio.to_thread(work))
        except sqlite3.Error as exc:
            return ToolResult.fail(f"Не удалось построить диаграмму: {exc}")


def _render_table(columns: list[str], rows: list[tuple], limit: int) -> str:
    """Простая ASCII-таблица результата."""
    def cell(value: object) -> str:
        text = "NULL" if value is None else str(value)
        return text if len(text) <= _MAX_CELL else text[: _MAX_CELL - 1] + "…"

    widths = [len(c) for c in columns]
    str_rows = []
    for row in rows:
        cells = [cell(v) for v in row]
        str_rows.append(cells)
        for i, c in enumerate(cells):
            widths[i] = min(max(widths[i], len(c)), 60)

    def fmt(cells: list[str]) -> str:
        return " | ".join(c.ljust(widths[i])[: widths[i]] for i, c in enumerate(cells))

    out = [fmt(columns), "-+-".join("-" * w for w in widths)]
    out += [fmt(r) for r in str_rows]
    note = f"\n({len(rows)} строк" + (f", показаны первые {limit}" if len(rows) >= limit else "") + ")"
    return "\n".join(out) + note
