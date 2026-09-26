"""Аналитика больших данных через DuckDB: SQL-запросы и авто-EDA."""

from __future__ import annotations

import pytest

from core.analytics.duckdb_eda import is_read_only
from core.tools.base import ToolContext
from core.tools.builtin.data_tools import ProfileDataTool, QueryDataTool

pytest.importorskip("duckdb")

_CSV = (
    "city,amount,category,note\n"
    "Москва,1500,еда,\n"
    "Москва,300,транспорт,\n"
    "Питер,999,еда,\n"
    "Питер,,транспорт,\n"  # пропуск в amount
    "Казань,50,еда,\n"
)


@pytest.fixture()
def csv_file(settings):
    path = settings.workspace / "spend.csv"
    path.write_text(_CSV, encoding="utf-8")
    return settings


# ------------------------------------------------------------ классификация SQL


def test_is_read_only():
    assert is_read_only("SELECT * FROM data")
    assert is_read_only("  WITH c AS (SELECT 1) SELECT * FROM c")
    assert is_read_only("SUMMARIZE data")
    assert not is_read_only("DELETE FROM data")
    assert not is_read_only("COPY data TO 'x.csv'")
    assert not is_read_only("CREATE TABLE t AS SELECT 1")


# ------------------------------------------------------------------- query_data


@pytest.mark.asyncio
async def test_query_aggregation(csv_file):
    result = await QueryDataTool().run(
        QueryDataTool.Args(
            path="spend.csv",
            sql="SELECT category, SUM(amount) AS total FROM data GROUP BY category ORDER BY total DESC",
        ),
        ToolContext(settings=csv_file),
    )
    assert result.ok
    assert "еда" in result.content and "транспорт" in result.content


@pytest.mark.asyncio
async def test_query_rejects_write(csv_file):
    result = await QueryDataTool().run(
        QueryDataTool.Args(path="spend.csv", sql="DELETE FROM data"),
        ToolContext(settings=csv_file),
    )
    assert not result.ok
    assert "только читающие" in result.content


@pytest.mark.asyncio
async def test_query_bad_sql(csv_file):
    result = await QueryDataTool().run(
        QueryDataTool.Args(path="spend.csv", sql="SELECT nope FROM data"),
        ToolContext(settings=csv_file),
    )
    assert not result.ok
    assert "Ошибка запроса" in result.content


# ------------------------------------------------------------------ profile_data


@pytest.mark.asyncio
async def test_profile_reports_columns_and_issues(csv_file):
    result = await ProfileDataTool().run(
        ProfileDataTool.Args(path="spend.csv"), ToolContext(settings=csv_file)
    )
    assert result.ok
    assert "строк 5" in result.content
    assert "city" in result.content and "amount" in result.content
    # amount имеет пропуск -> должна отметиться доля пропусков.
    assert "пропусков" in result.content


@pytest.mark.asyncio
async def test_profile_flags_constant_column(settings):
    path = settings.workspace / "const.csv"
    path.write_text("a,b\n1,x\n2,x\n3,x\n", encoding="utf-8")
    result = await ProfileDataTool().run(
        ProfileDataTool.Args(path="const.csv"), ToolContext(settings=settings)
    )
    assert "константа" in result.content
