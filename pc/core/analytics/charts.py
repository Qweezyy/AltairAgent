"""Интерактивные графики как самодостаточный HTML.

Почему так: график — это результат, который пользователь захочет открыть,
сохранить и показать другим. Значит файл должен работать сам по себе, без
интернета и без соседних файлов. Поэтому движок Plotly встраивается прямо в
HTML, а фигура собирается обычным JSON'ом — тяжёлый питоновский пакет plotly
не нужен, достаточно вендоренного `plotly-basic.min.js`.

Строить фигуру на Python просто: трасса — это словарь, layout — словарь,
Plotly.newPlot(div, traces, layout) их рисует.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger("analytics.charts")

#: Вендоренный движок. Одна облегчённая сборка на line/bar/pie/scatter.
PLOTLY_JS = Path(__file__).resolve().parent.parent.parent / "static" / "vendor" / "plotly" / "plotly-basic.min.js"

CHART_TYPES = ("line", "bar", "pie", "scatter", "area")

#: Тёплая графитово-охряная палитра — та же, что в интерфейсе.
PALETTE = ["#c8863c", "#3c7a8c", "#a8556b", "#6b8e5a", "#8c6d3f", "#4a6b8a", "#b0724a", "#7a7f9a"]

MAX_POINTS = 5000
MAX_SERIES = 12


class ChartError(Exception):
    """График не удалось построить (понятная пользователю причина)."""


@dataclass(slots=True)
class Series:
    """Один ряд данных на графике."""

    name: str
    y: list[float]
    #: Свои X (иначе берутся общие для всего графика).
    x: list | None = None


@dataclass(slots=True)
class Chart:
    """Описание графика до превращения в HTML."""

    kind: str
    title: str = ""
    x: list = field(default_factory=list)
    series: list[Series] = field(default_factory=list)
    x_label: str = ""
    y_label: str = ""
    #: Для круговой диаграммы: подписи и значения.
    labels: list[str] = field(default_factory=list)
    values: list[float] = field(default_factory=list)


def _validate(chart: Chart) -> None:
    if chart.kind not in CHART_TYPES:
        raise ChartError(f"Тип '{chart.kind}' не поддерживается. Доступны: {', '.join(CHART_TYPES)}.")

    if chart.kind == "pie":
        if not chart.labels or not chart.values:
            raise ChartError("Для круговой диаграммы нужны labels и values.")
        if len(chart.labels) != len(chart.values):
            raise ChartError(
                f"labels и values разной длины: {len(chart.labels)} и {len(chart.values)}."
            )
        if len(chart.labels) > MAX_POINTS:
            raise ChartError(f"Слишком много секторов (>{MAX_POINTS}).")
        return

    if not chart.series:
        raise ChartError("Нет ни одного ряда данных (series).")
    if len(chart.series) > MAX_SERIES:
        raise ChartError(f"Слишком много рядов ({len(chart.series)} при лимите {MAX_SERIES}).")

    for s in chart.series:
        if not s.y:
            raise ChartError(f"Ряд «{s.name}» пуст.")
        if len(s.y) > MAX_POINTS:
            raise ChartError(f"В ряду «{s.name}» слишком много точек (>{MAX_POINTS}).")
        own_x = s.x if s.x is not None else chart.x
        if own_x and len(own_x) != len(s.y):
            raise ChartError(
                f"В ряду «{s.name}» длина X ({len(own_x)}) не совпадает с Y ({len(s.y)})."
            )


def _traces(chart: Chart) -> list[dict]:
    """Собирает список трасс Plotly из описания графика."""
    if chart.kind == "pie":
        return [
            {
                "type": "pie",
                "labels": chart.labels,
                "values": chart.values,
                "marker": {"colors": PALETTE},
                "textinfo": "label+percent",
                "hovertemplate": "%{label}: %{value} (%{percent})<extra></extra>",
            }
        ]

    mode = {"line": "lines", "scatter": "markers", "area": "lines"}.get(chart.kind, "lines")
    traces = []
    for index, s in enumerate(chart.series):
        color = PALETTE[index % len(PALETTE)]
        x = s.x if s.x is not None else (chart.x or list(range(1, len(s.y) + 1)))
        trace: dict = {"name": s.name, "x": x, "y": s.y}
        if chart.kind == "bar":
            trace.update({"type": "bar", "marker": {"color": color}})
        else:
            trace.update(
                {"type": "scatter", "mode": mode, "line": {"color": color, "width": 2},
                 "marker": {"color": color}}
            )
            if chart.kind == "area":
                trace["fill"] = "tozeroy"
        traces.append(trace)
    return traces


def _layout(chart: Chart) -> dict:
    layout: dict = {
        "title": {"text": chart.title, "font": {"size": 18}},
        "font": {"family": "Inter, system-ui, sans-serif", "color": "#2b2724"},
        "paper_bgcolor": "#faf7f2",
        "plot_bgcolor": "#faf7f2",
        "margin": {"t": 56, "r": 24, "b": 56, "l": 64},
        "colorway": PALETTE,
        "showlegend": len(chart.series) > 1 or chart.kind == "pie",
        "legend": {"orientation": "h", "y": -0.2},
    }
    if chart.kind != "pie":
        axis = {"gridcolor": "#e8e0d4", "zerolinecolor": "#d8cdbc", "linecolor": "#d8cdbc"}
        layout["xaxis"] = {**axis, "title": {"text": chart.x_label}}
        layout["yaxis"] = {**axis, "title": {"text": chart.y_label}}
    return layout


def build_chart_html(chart: Chart) -> str:
    """Возвращает самодостаточный HTML с интерактивным графиком."""
    _validate(chart)

    try:
        engine = PLOTLY_JS.read_text(encoding="utf-8")
    except OSError as exc:
        raise ChartError(
            f"Не найден движок графиков ({PLOTLY_JS.name}). Переустановите приложение."
        ) from exc

    figure = {"data": _traces(chart), "layout": _layout(chart)}
    figure_json = json.dumps(figure, ensure_ascii=False)
    # Данные попадают внутрь <script>. Если в подписи есть «</script>», браузер
    # закроет тег прямо там — график сломается, а то и выполнится чужой код.
    # «<\/» для JS-строки эквивалентно «</», но тег уже не закрывает.
    figure_json = figure_json.replace("</", "<\\/")
    title = chart.title or "График"

    # config: убираем логотип Plotly и лишние кнопки, оставляем зум и скачивание.
    return (
        "<!DOCTYPE html><html lang=\"ru\"><head><meta charset=\"UTF-8\">"
        f"<title>{_escape(title)}</title>"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<style>html,body{margin:0;height:100%;background:#faf7f2}#chart{width:100%;height:100vh}</style>"
        f"<script>{engine}</script></head><body>"
        "<div id=\"chart\"></div>"
        "<script>"
        f"var fig={figure_json};"
        "Plotly.newPlot('chart',fig.data,fig.layout,"
        "{responsive:true,displaylogo:false,"
        "modeBarButtonsToRemove:['lasso2d','select2d']});"
        "</script></body></html>"
    )


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
