"""Аналитика: графики и разбор финансовых выписок.

Модуль отвечает за «показать данные», а не «посчитать за пользователя». Логика
здесь чистая и тестируемая; инструменты в `core/tools/builtin/analytics_tools.py`
— тонкие обёртки над ней.
"""

from __future__ import annotations

from core.analytics.charts import ChartError, build_chart_html
from core.analytics.finance import StatementError, analyze_statement

__all__ = ["ChartError", "StatementError", "analyze_statement", "build_chart_html"]
