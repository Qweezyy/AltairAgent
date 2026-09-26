"""SQLite: интроспекция схемы, запросы, ER-диаграмма.

Чтение открывается в режиме read-only (`file:...?mode=ro`): случайная правка
данных исключена на уровне подключения. Запись — отдельным путём и только с
подтверждения (см. инструмент).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

#: Первое значимое слово этих запросов = только чтение.
_READ_KEYWORDS = {"select", "pragma", "explain", "with", "values"}
#: Явно изменяющие данные/схему.
_WRITE_KEYWORDS = {
    "insert", "update", "delete", "replace", "create", "drop", "alter",
    "truncate", "attach", "detach", "vacuum", "reindex", "analyze",
}


def _first_keyword(sql: str) -> str:
    # Убираем ведущие комментарии и пробелы, берём первое слово.
    text = re.sub(r"^\s*(--[^\n]*\n|/\*.*?\*/|\s)+", "", sql, flags=re.DOTALL)
    match = re.match(r"[a-zA-Z_]+", text.strip())
    return match.group(0).lower() if match else ""


def is_write_sql(sql: str) -> bool:
    """Меняет ли запрос данные/схему. При сомнении считаем, что да (безопаснее)."""
    keyword = _first_keyword(sql)
    if keyword == "with":
        # CTE может завершаться INSERT/UPDATE/DELETE — ищем их в теле.
        return bool(re.search(r"\b(insert|update|delete|replace)\b", sql, re.IGNORECASE))
    if keyword in _WRITE_KEYWORDS:
        return True
    if keyword in _READ_KEYWORDS:
        return False
    return True  # неизвестное слово — не рискуем, трактуем как запись


def open_readonly(path: Path) -> sqlite3.Connection:
    """Открывает БД только на чтение. Правка данных через это подключение невозможна."""
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def open_readwrite(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


@dataclass
class Column:
    name: str
    type: str
    pk: bool
    notnull: bool


@dataclass
class ForeignKey:
    column: str
    ref_table: str
    ref_column: str


@dataclass
class Table:
    name: str
    columns: list[Column] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    row_count: int = 0


def _tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]


def _table(conn: sqlite3.Connection, name: str) -> Table:
    table = Table(name=name)
    for row in conn.execute(f'PRAGMA table_info("{name}")').fetchall():
        table.columns.append(
            Column(
                name=row["name"],
                type=row["type"] or "",
                pk=bool(row["pk"]),
                notnull=bool(row["notnull"]),
            )
        )
    for row in conn.execute(f'PRAGMA foreign_key_list("{name}")').fetchall():
        table.foreign_keys.append(
            ForeignKey(column=row["from"], ref_table=row["table"], ref_column=row["to"])
        )
    try:
        table.row_count = conn.execute(f'SELECT COUNT(*) AS c FROM "{name}"').fetchone()["c"]
    except sqlite3.Error:
        table.row_count = -1
    return table


def schema_overview(conn: sqlite3.Connection) -> list[Table]:
    """Полная структура БД: таблицы, колонки, ключи, число строк."""
    return [_table(conn, name) for name in _tables(conn)]


def run_query(conn: sqlite3.Connection, sql: str, *, limit: int = 200) -> tuple[list[str], list[tuple]]:
    """Выполняет запрос. Для SELECT возвращает (колонки, строки, не больше limit)."""
    cursor = conn.execute(sql)
    if cursor.description is None:
        conn.commit()
        return [], []
    columns = [d[0] for d in cursor.description]
    rows = cursor.fetchmany(limit)
    return columns, [tuple(r) for r in rows]


def mermaid_er(tables: list[Table]) -> str:
    """Строит Mermaid erDiagram по таблицам и внешним ключам."""
    lines = ["erDiagram"]
    for table in tables:
        lines.append(f"    {_safe(table.name)} {{")
        for col in table.columns:
            mark = "PK" if col.pk else ""
            typ = _safe(col.type or "TEXT")
            lines.append(f"        {typ} {_safe(col.name)} {mark}".rstrip())
        lines.append("    }")
    for table in tables:
        for fk in table.foreign_keys:
            lines.append(
                f"    {_safe(table.name)} }}o--|| {_safe(fk.ref_table)} : {_safe(fk.column)}"
            )
    return "\n".join(lines)


def _safe(name: str) -> str:
    """Mermaid не любит спецсимволы в идентификаторах — оставляем безопасные."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name) or "x"
