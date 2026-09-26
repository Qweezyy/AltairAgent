"""Работа с базами данных: интроспекция схемы, безопасные запросы, ER-диаграммы.

Пока поддержан SQLite (встроен в Python, ничего ставить не надо) — он покрывает
большинство локальных приложений. Интерфейс оставляет место для других СУБД
через драйверы, если понадобится.
"""

from core.database.sqlite_db import (
    is_write_sql,
    mermaid_er,
    open_readonly,
    run_query,
    schema_overview,
)

__all__ = [
    "is_write_sql",
    "mermaid_er",
    "open_readonly",
    "run_query",
    "schema_overview",
]
