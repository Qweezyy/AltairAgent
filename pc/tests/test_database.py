"""Инструменты работы с SQLite: схема, запросы, ER-диаграмма, безопасность."""

from __future__ import annotations

import sqlite3

import pytest

from core.database import is_write_sql
from core.tools.base import ToolContext
from core.tools.builtin.db_tools import DbDiagramTool, DbQueryTool, DbSchemaTool


@pytest.fixture()
def db(settings):
    """Создаёт SQLite-базу с двумя связанными таблицами."""
    path = settings.workspace / "shop.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE books (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            author_id INTEGER,
            FOREIGN KEY (author_id) REFERENCES authors(id)
        );
        INSERT INTO authors (id, name) VALUES (1, 'Пушкин'), (2, 'Гоголь');
        INSERT INTO books (id, title, author_id) VALUES (1, 'Онегин', 1), (2, 'Нос', 2);
        """
    )
    conn.commit()
    conn.close()
    return settings


# ------------------------------------------------------------- классификация SQL


def test_is_write_sql():
    assert not is_write_sql("SELECT * FROM t")
    assert not is_write_sql("  -- коммент\n select 1")
    assert not is_write_sql("PRAGMA table_info(t)")
    assert not is_write_sql("EXPLAIN QUERY PLAN SELECT 1")
    assert is_write_sql("INSERT INTO t VALUES (1)")
    assert is_write_sql("DROP TABLE t")
    assert is_write_sql("UPDATE t SET x=1")
    # CTE, завершающийся записью, — это запись.
    assert is_write_sql("WITH c AS (SELECT 1) INSERT INTO t SELECT * FROM c")
    assert not is_write_sql("WITH c AS (SELECT 1) SELECT * FROM c")


# ------------------------------------------------------------------- инструменты


@pytest.mark.asyncio
async def test_schema(db):
    result = await DbSchemaTool().run(DbSchemaTool.Args(path="shop.db"), ToolContext(settings=db))
    assert "authors" in result.content and "books" in result.content
    assert "author_id → authors.id" in result.content
    assert "2 строк" in result.content  # число строк


@pytest.mark.asyncio
async def test_query_select(db):
    result = await DbQueryTool().run(
        DbQueryTool.Args(path="shop.db", sql="SELECT name FROM authors ORDER BY id"),
        ToolContext(settings=db),
    )
    assert "Пушкин" in result.content and "Гоголь" in result.content


@pytest.mark.asyncio
async def test_query_join(db):
    result = await DbQueryTool().run(
        DbQueryTool.Args(
            path="shop.db",
            sql="SELECT b.title, a.name FROM books b JOIN authors a ON a.id=b.author_id",
        ),
        ToolContext(settings=db),
    )
    assert "Онегин" in result.content and "Пушкин" in result.content


@pytest.mark.asyncio
async def test_readonly_blocks_write_via_select_path(db):
    """SELECT-путь открывает БД read-only: даже если прислать запись как 'select'-подобное — не пройдёт."""
    # Прямая запись классифицируется как write и потребовала бы подтверждения;
    # проверяем, что read-only соединение действительно не даёт писать.
    from core.database import open_readonly

    conn = open_readonly(db.workspace / "shop.db")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO authors (id, name) VALUES (3, 'x')")
    conn.close()


def test_query_write_needs_approval(db):
    tool = DbQueryTool()
    ctx = ToolContext(settings=db)
    assert tool.auto_verdict(tool.Args(path="shop.db", sql="SELECT 1"), ctx) == "allow"
    assert tool.auto_verdict(tool.Args(path="shop.db", sql="DELETE FROM books"), ctx) == "ask"


@pytest.mark.asyncio
async def test_query_write_actually_writes(db):
    """Изменяющий запрос (после «подтверждения») реально пишет через RW-подключение."""
    result = await DbQueryTool().run(
        DbQueryTool.Args(path="shop.db", sql="INSERT INTO authors (id, name) VALUES (3, 'Чехов')"),
        ToolContext(settings=db),
    )
    assert result.ok
    check = await DbQueryTool().run(
        DbQueryTool.Args(path="shop.db", sql="SELECT name FROM authors WHERE id=3"),
        ToolContext(settings=db),
    )
    assert "Чехов" in check.content


@pytest.mark.asyncio
async def test_diagram(db):
    result = await DbDiagramTool().run(DbDiagramTool.Args(path="shop.db"), ToolContext(settings=db))
    assert "```mermaid" in result.content
    assert "erDiagram" in result.content
    assert "books" in result.content and "authors" in result.content
    assert "}o--||" in result.content  # связь по внешнему ключу


@pytest.mark.asyncio
async def test_bad_sql(db):
    result = await DbQueryTool().run(
        DbQueryTool.Args(path="shop.db", sql="SELECT * FROM nonexistent"),
        ToolContext(settings=db),
    )
    assert not result.ok
    assert "Ошибка SQL" in result.content
