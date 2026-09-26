"""Аналитика больших табличных данных через DuckDB — без загрузки в память.

DuckDB читает CSV/Parquet/JSON прямо с диска колоночным движком: гигабайтный файл
не тянется в оперативку целиком, а сканируется по нужным колонкам. Это позволяет
делать SQL-аналитику и авто-разведку (EDA) над данными, которые не влезли бы в
pandas.

Файл подключается как представление `data`, поэтому агент пишет обычный SQL:
`SELECT ... FROM data WHERE ...`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Только читающие запросы — источник данных не должен меняться этим инструментом.
_READ_OK = {"select", "with", "explain", "describe", "summarize", "pragma", "show", "values"}


class DuckDBUnavailable(RuntimeError):
    """DuckDB не установлен."""


def _connect() -> Any:
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - зависит от окружения
        raise DuckDBUnavailable(
            "DuckDB не установлен. Поставьте: pip install duckdb"
        ) from exc
    return duckdb.connect(database=":memory:")


def _reader(path: Path) -> str:
    """Функция чтения DuckDB под формат файла."""
    suffix = path.suffix.lower()
    escaped = str(path).replace("\\", "/").replace("'", "''")
    if suffix in (".parquet", ".pq"):
        return f"read_parquet('{escaped}')"
    if suffix in (".json", ".ndjson", ".jsonl"):
        return f"read_json_auto('{escaped}')"
    # csv/tsv/txt и всё прочее — автоопределение разделителя и типов.
    return f"read_csv_auto('{escaped}', sample_size=-1)"


def _register(conn: Any, path: Path) -> None:
    conn.execute(f"CREATE OR REPLACE VIEW data AS SELECT * FROM {_reader(path)}")


def is_read_only(sql: str) -> bool:
    text = re.sub(r"^\s*(--[^\n]*\n|/\*.*?\*/|\s)+", "", sql, flags=re.DOTALL)
    match = re.match(r"[a-zA-Z_]+", text.strip())
    return bool(match) and match.group(0).lower() in _READ_OK


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]


def run_query(path: Path, sql: str, *, limit: int = 200) -> QueryResult:
    """Выполняет SQL над файлом (файл доступен как таблица `data`)."""
    conn = _connect()
    try:
        _register(conn, path)
        cursor = conn.execute(sql)
        columns = [d[0] for d in cursor.description] if cursor.description else []
        rows = cursor.fetchmany(limit) if columns else []
        return QueryResult(columns=columns, rows=[tuple(r) for r in rows])
    finally:
        conn.close()


@dataclass
class ColumnProfile:
    name: str
    type: str
    null_pct: float
    distinct: int
    stats: dict[str, Any]  # min/max/avg/std/квартили — что применимо


@dataclass
class DataProfile:
    rows: int
    columns: list[ColumnProfile]
    issues: list[str]


def profile(path: Path) -> DataProfile:
    """Авто-разведка данных (EDA): типы, пропуски, уникальность, статистика, проблемы."""
    conn = _connect()
    try:
        _register(conn, path)
        total = conn.execute("SELECT COUNT(*) FROM data").fetchone()[0]

        # SUMMARIZE — встроенная сводка DuckDB по всем колонкам разом.
        cur = conn.execute("SUMMARIZE data")
        names = [d[0] for d in cur.description]
        rows = cur.fetchall()
    finally:
        conn.close()

    columns: list[ColumnProfile] = []
    issues: list[str] = []
    for row in rows:
        rec = dict(zip(names, row, strict=False))
        col = str(rec.get("column_name", "?"))
        null_pct = _to_float(rec.get("null_percentage"))
        distinct = int(_to_float(rec.get("approx_unique")))
        stats = {
            key: rec.get(key)
            for key in ("min", "max", "avg", "std", "q25", "q50", "q75")
            if rec.get(key) not in (None, "")
        }
        columns.append(
            ColumnProfile(
                name=col,
                type=str(rec.get("column_type", "")),
                null_pct=null_pct,
                distinct=distinct,
                stats=stats,
            )
        )
        # Признаки проблем в данных.
        if null_pct >= 50:
            issues.append(f"«{col}»: пропущено {null_pct:.0f}% значений")
        if distinct <= 1 and total > 1:
            issues.append(f"«{col}»: одно и то же значение во всех строках (константа)")

    return DataProfile(rows=int(total), columns=columns, issues=issues)


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
