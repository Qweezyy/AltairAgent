"""Инструменты аналитики: интерактивные графики и разбор выписок."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from core.analytics.charts import Chart, ChartError, Series, build_chart_html
from core.analytics.finance import StatementError, analyze_statement
from core.errors import ToolError
from core.events import ArtifactCreated
from core.security.paths import resolve_path, safe_relpath
from core.tools.base import Tool, ToolContext

# ------------------------------------------------------------ create_chart


class SeriesModel(BaseModel):
    name: str = Field(description="Название ряда (для легенды)")
    y: list[float] = Field(description="Значения по оси Y")
    x: list[str | float] | None = Field(default=None, description="Свои X, если отличаются от общих")


class CreateChartArgs(BaseModel):
    kind: Literal["line", "bar", "pie", "scatter", "area"] = Field(description="Тип графика")
    title: str = Field(default="", description="Заголовок графика")
    path: str = Field(default="", description="Куда сохранить .html (пусто — по заголовку)")
    x: list[str | float] = Field(default_factory=list, description="Общие подписи по оси X")
    series: list[SeriesModel] = Field(default_factory=list, description="Ряды данных (для всех типов, кроме pie)")
    x_label: str = Field(default="", description="Подпись оси X")
    y_label: str = Field(default="", description="Подпись оси Y")
    labels: list[str] = Field(default_factory=list, description="Для pie: подписи секторов")
    values: list[float] = Field(default_factory=list, description="Для pie: значения секторов")


class CreateChartTool(Tool):
    name = "create_chart"
    description = (
        "Строит интерактивный график (линия, столбцы, круговая, точки, область) и сохраняет "
        "самодостаточный HTML — он открывается в превью и в любом браузере, работает без интернета. "
        "Для линий/столбцов передавай series (ряды по Y) и, при желании, общие x. "
        "Для круговой (pie) передавай labels и values. Используй после расчётов, чтобы показать данные наглядно."
    )
    Args = CreateChartArgs
    category = "edit"
    timeout = 30.0

    async def run(self, args: CreateChartArgs, ctx: ToolContext) -> str:
        chart = Chart(
            kind=args.kind,
            title=args.title,
            x=list(args.x),
            series=[Series(name=s.name, y=s.y, x=s.x) for s in args.series],
            x_label=args.x_label,
            y_label=args.y_label,
            labels=list(args.labels),
            values=list(args.values),
        )
        try:
            html = build_chart_html(chart)
        except ChartError as exc:
            raise ToolError(str(exc)) from exc

        name = args.path.strip() or f"{_safe_name(args.title or args.kind)}.html"
        if not name.lower().endswith(".html"):
            name += ".html"
        destination = resolve_path(name, settings=ctx.settings)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(html, encoding="utf-8")

        relative = safe_relpath(destination, ctx.settings)
        await ctx.emitter(
            ArtifactCreated(
                path=relative,
                name=destination.name,
                kind="html",
                size_bytes=destination.stat().st_size,
            )
        )
        return f"График сохранён: {relative}. Откроется в превью (вкладка «Страница»)."


# -------------------------------------------------------- analyze_statement


class AnalyzeStatementArgs(BaseModel):
    path: str = Field(description="Путь к выписке в рабочей папке (CSV или XLSX)")
    top: int = Field(default=5, ge=1, le=20, description="Сколько крупнейших трат показать")
    chart: bool = Field(default=True, description="Построить график расходов по категориям")


class AnalyzeStatementTool(Tool):
    name = "analyze_statement"
    description = (
        "Разбирает банковскую выписку (CSV или XLSX): распределяет операции по категориям, "
        "считает доходы, расходы и итог, находит крупнейшие траты и сводит по месяцам. "
        "Колонки определяет по заголовкам, поэтому подходит для выписок разных банков. "
        "По умолчанию строит круговую диаграмму расходов."
    )
    Args = AnalyzeStatementArgs
    category = "read"
    timeout = 60.0

    async def run(self, args: AnalyzeStatementArgs, ctx: ToolContext) -> str:
        path = resolve_path(args.path, settings=ctx.settings)
        if not path.is_file():
            raise ToolError(f"Файл не найден: {args.path}")

        try:
            summary = analyze_statement(path.read_bytes(), path.name, top=args.top)
        except StatementError as exc:
            raise ToolError(str(exc)) from exc

        result = summary.to_text()

        if args.chart and summary.by_category:
            try:
                html = build_chart_html(summary.expense_chart())
                chart_path = resolve_path(f"{path.stem}_расходы.html", settings=ctx.settings)
                chart_path.write_text(html, encoding="utf-8")
                relative = safe_relpath(chart_path, ctx.settings)
                await ctx.emitter(
                    ArtifactCreated(
                        path=relative,
                        name=chart_path.name,
                        kind="html",
                        size_bytes=chart_path.stat().st_size,
                    )
                )
                result += f"\n\nДиаграмма расходов: {relative}"
            except ChartError:
                # График — приятное дополнение; его сбой не должен ронять анализ.
                pass

        return result


def _safe_name(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in text).strip()
    return (cleaned or "график").replace(" ", "_")[:60]


__all__ = ["AnalyzeStatementTool", "CreateChartTool"]
