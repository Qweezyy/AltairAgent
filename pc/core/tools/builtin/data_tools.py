"""Инструменты аналитики больших данных через DuckDB: SQL и авто-разведка (EDA).

Работают с CSV/Parquet/JSON прямо с диска, не загружая файл в память целиком —
подходят для гигабайтных таблиц, которые не влезли бы в обычный разбор.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from core.analytics.duckdb_eda import (
    DuckDBUnavailable,
    is_read_only,
    profile,
    run_query,
)
from core.security.paths import resolve_path
from core.tools.base import Tool, ToolContext, ToolResult

_MAX_ROWS = 100
_MAX_CELL = 200


def _table(columns: list[str], rows: list[tuple], limit: int) -> str:
    def cell(v: object) -> str:
        text = "NULL" if v is None else str(v)
        return text if len(text) <= _MAX_CELL else text[: _MAX_CELL - 1] + "…"

    widths = [len(c) for c in columns]
    srows = []
    for row in rows:
        cells = [cell(v) for v in row]
        srows.append(cells)
        for i, c in enumerate(cells):
            widths[i] = min(max(widths[i], len(c)), 40)

    def fmt(cells: list[str]) -> str:
        return " | ".join(c.ljust(widths[i])[: widths[i]] for i, c in enumerate(cells))

    out = [fmt(columns), "-+-".join("-" * w for w in widths)] + [fmt(r) for r in srows]
    tail = f"\n({len(rows)} строк" + (f", показаны первые {limit}" if len(rows) >= limit else "") + ")"
    return "\n".join(out) + tail


class QueryDataArgs(BaseModel):
    path: str = Field(description="Путь к файлу данных в рабочей папке (CSV, Parquet, JSON)")
    sql: str = Field(
        description="SQL-запрос; файл доступен как таблица data, например «SELECT city, COUNT(*) FROM data GROUP BY city»"
    )
    limit: int = Field(default=100, ge=1, le=1000, description="Максимум строк в ответе")


class QueryDataTool(Tool):
    name = "query_data"
    description = (
        "Выполняет SQL над файлом данных (CSV/Parquet/JSON) движком DuckDB — без загрузки файла в "
        "память, поэтому годится и для гигабайтных таблиц. Файл доступен как таблица data. Только "
        "чтение (SELECT/WITH/EXPLAIN/…). Пример: SELECT category, SUM(amount) FROM data GROUP BY category."
    )
    Args = QueryDataArgs
    category = "read"
    timeout = 120.0

    async def run(self, args: QueryDataArgs, ctx: ToolContext) -> ToolResult:
        if not is_read_only(args.sql):
            return ToolResult.fail(
                "Разрешены только читающие запросы (SELECT/WITH/EXPLAIN/DESCRIBE/SUMMARIZE). "
                "Изменять исходные данные этим инструментом нельзя."
            )
        path = resolve_path(args.path, settings=ctx.settings, must_exist=True, must_be_file=True)
        limit = min(args.limit, _MAX_ROWS)

        def work() -> ToolResult:
            result = run_query(path, args.sql, limit=limit)
            if not result.columns:
                return ToolResult(content="Запрос выполнен, данных на возврат нет.")
            return ToolResult(content=_table(result.columns, result.rows, limit))

        try:
            return await asyncio.to_thread(work)
        except DuckDBUnavailable as exc:
            return ToolResult.fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - ошибку SQL/чтения показываем как есть
            return ToolResult.fail(f"Ошибка запроса: {str(exc)[:400]}")


class ProfileDataArgs(BaseModel):
    path: str = Field(description="Путь к файлу данных в рабочей папке (CSV, Parquet, JSON)")


class ProfileDataTool(Tool):
    name = "profile_data"
    description = (
        "Авто-разведка данных (EDA) файла CSV/Parquet/JSON через DuckDB: число строк, по каждой "
        "колонке — тип, доля пропусков, число уникальных, статистика (min/max/среднее/квартили), "
        "и найденные проблемы (много пропусков, колонки-константы). Вызывай ПЕРВЫМ при работе с "
        "незнакомым набором данных, чтобы понять его устройство перед запросами."
    )
    Args = ProfileDataArgs
    category = "read"
    timeout = 120.0

    async def run(self, args: ProfileDataArgs, ctx: ToolContext) -> ToolResult:
        path = resolve_path(args.path, settings=ctx.settings, must_exist=True, must_be_file=True)

        def work() -> str:
            data = profile(path)
            lines = [f"Файл {path.name}: строк {data.rows:,}, колонок {len(data.columns)}".replace(",", " "), ""]
            for col in data.columns:
                bits = [f"тип {col.type}", f"уникальных {col.distinct}"]
                if col.null_pct > 0:
                    bits.append(f"пропусков {col.null_pct:.0f}%")
                stat = col.stats
                if "min" in stat and "max" in stat:
                    bits.append(f"диапазон {stat['min']}…{stat['max']}")
                if "avg" in stat:
                    bits.append(f"среднее {stat['avg']}")
                lines.append(f"■ {col.name}: " + ", ".join(bits))
            if data.issues:
                lines.append("")
                lines.append("⚠️ На что обратить внимание:")
                lines += [f"  • {i}" for i in data.issues]
            return "\n".join(lines)

        try:
            return ToolResult(content=await asyncio.to_thread(work))
        except DuckDBUnavailable as exc:
            return ToolResult.fail(str(exc))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"Не удалось разобрать данные: {str(exc)[:400]}")
