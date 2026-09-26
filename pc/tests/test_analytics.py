"""Аналитика: графики и разбор банковских выписок."""

from __future__ import annotations

import pytest

from core.analytics.charts import Chart, ChartError, Series, build_chart_html
from core.analytics.finance import StatementError, analyze_statement, categorize

# --------------------------------------------------------------- графики


@pytest.fixture
def stub_engine(monkeypatch, tmp_path):
    """Подменяет движок Plotly крошечной заглушкой.

    Реальный движок — минифицированный мегабайт, в котором есть и «src=http»,
    и «<script». Проверять по нему «нет внешних ссылок» нельзя: подстрока
    найдётся в самом движке, ассерт упадёт, а pytest повесится, пытаясь
    отрендерить мегабайт в трейсбек. Поэтому обёртку тестируем на заглушке.
    """
    import core.analytics.charts as charts_module

    fake = tmp_path / "engine.js"
    fake.write_text("/* fake plotly */ var Plotly={newPlot:function(){}};", encoding="utf-8")
    monkeypatch.setattr(charts_module, "PLOTLY_JS", fake)
    return fake


def test_real_engine_is_embedded():
    """Настоящий движок вшит в файл, а не подключён ссылкой (проверки позитивные)."""
    html = build_chart_html(
        Chart(kind="line", title="Продажи", x=["янв", "фев"], series=[Series(name="2025", y=[10, 20])])
    )
    assert "Plotly.newPlot" in html
    assert "plotly.js" in html  # копирайт движка присутствует
    assert len(html) > 500_000, "движок должен быть внутри файла, а не ссылкой"


def test_chart_wrapper_loads_engine_inline(stub_engine):
    """Обёртка встраивает движок инлайном — ни одной внешней ссылки."""
    html = build_chart_html(
        Chart(kind="line", title="Продажи", x=["янв", "фев"], series=[Series(name="2025", y=[10, 20])])
    )
    assert "<script src=" not in html, "движок должен быть инлайн, а не ссылкой"
    assert "http://" not in html and "https://" not in html
    assert "fake plotly" in html  # заглушка вшита
    assert "<title>Продажи</title>" in html


def test_pie_chart_needs_labels_and_values():
    with pytest.raises(ChartError, match="labels и values"):
        build_chart_html(Chart(kind="pie", title="Пусто"))


def test_series_x_length_must_match_y():
    """Ряд с разной длиной X и Y — почти всегда ошибка данных, а не задумка."""
    chart = Chart(kind="bar", x=["a", "b"], series=[Series(name="s", y=[1, 2, 3])])
    with pytest.raises(ChartError, match="не совпадает"):
        build_chart_html(chart)


def test_unknown_chart_type_lists_options():
    with pytest.raises(ChartError, match="line"):
        build_chart_html(Chart(kind="спираль", series=[Series(name="s", y=[1])]))


def test_title_is_escaped_against_injection(stub_engine):
    """Заголовок попадает и в <title>, и в JSON фигуры — ни там, ни там он не
    должен закрывать <script> или ломать разметку."""
    html = build_chart_html(
        Chart(kind="bar", title="<script>alert(1)</script>", series=[Series(name="s", y=[1])])
    )
    # В <title> — экранированные угловые скобки.
    assert "&lt;script&gt;" in html
    # Закрывающий тег из данных обезврежен: точной строки инъекции нет, а
    # опасное «</script>» превратилось в безвредное «<\/script>».
    assert "<script>alert(1)</script>" not in html
    assert "<\\/script>" in html


# ----------------------------------------------------------- категоризация


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Оплата в ПЯТЁРОЧКА №123", "Продукты"),
        ("Яндекс.Такси поездка", "Транспорт"),
        ("Перевод по СБП Иванову", "Переводы"),
        ("Аптека Ригла", "Здоровье"),
        ("Netflix подписка", "Развлечения"),
        ("Начисление заработной платы", "Зарплата"),
        ("Нечто непонятное 777", "Прочее"),
    ],
)
def test_categorization_by_keywords(description, expected):
    assert categorize(description) == expected


# --------------------------------------------------------------- выписки


CSV_RU = (
    "Дата;Сумма;Описание\n"
    "2026-03-01;50000,00;Начисление зарплаты\n"
    "2026-03-02;-1200,50;Пятёрочка продукты\n"
    "2026-03-03;-800,00;Яндекс.Такси\n"
    "2026-03-05;-3000,00;Аптека большой чек\n"
    "2026-03-10;-450,00;Кафе кофе\n"
)


def _bytes(text: str) -> bytes:
    return text.encode("utf-8")


def test_statement_totals_are_exact():
    summary = analyze_statement(_bytes(CSV_RU), "выписка.csv")

    assert summary.income == 50000.0
    assert summary.expense == -5450.5
    assert summary.balance == 44549.5
    assert len(summary.transactions) == 5


def test_statement_groups_by_category():
    summary = analyze_statement(_bytes(CSV_RU), "выписка.csv")

    assert summary.by_category["Продукты"] == -1200.5
    assert summary.by_category["Зарплата"] == 50000.0
    assert "Здоровье" in summary.by_category


def test_statement_finds_biggest_expenses():
    summary = analyze_statement(_bytes(CSV_RU), "выписка.csv", top=2)

    assert len(summary.top_expenses) == 2
    assert summary.top_expenses[0].amount == -3000.0  # аптека — крупнейшая трата
    assert "Аптека" in summary.top_expenses[0].description


def test_statement_monthly_breakdown():
    summary = analyze_statement(_bytes(CSV_RU), "выписка.csv")
    assert summary.by_month["2026-03"] == 44549.5


def test_amount_with_thousands_separator_and_parentheses():
    """«(1 200,00)» — это −1200, а «1 200.50» с точкой-разделителем тысяч — 1200.5."""
    csv = "Дата,Сумма,Описание\n2026-01-01,\"(1 200,00)\",Возврат\n2026-01-02,\"50 000,00\",Зарплата\n"
    summary = analyze_statement(_bytes(csv), "s.csv")
    amounts = sorted(tx.amount for tx in summary.transactions)
    assert amounts == [-1200.0, 50000.0]


def test_statement_without_amount_column_is_refused():
    csv = "Дата;Заметка\n2026-01-01;просто текст\n"
    with pytest.raises(StatementError, match="колонку с суммой"):
        analyze_statement(_bytes(csv), "s.csv")


def test_english_headers_are_recognized():
    csv = "Date,Amount,Description\n2026-02-01,-15.50,Coffee shop\n2026-02-02,3000,Salary payment\n"
    summary = analyze_statement(_bytes(csv), "bank.csv")
    assert summary.income == 3000.0
    assert round(summary.expense, 2) == -15.5


def test_expense_chart_excludes_income():
    """На диаграмме расходов доходам не место — иначе картина искажается."""
    summary = analyze_statement(_bytes(CSV_RU), "выписка.csv")
    chart = summary.expense_chart()
    assert "Зарплата" not in chart.labels
    assert "Продукты" in chart.labels
    assert all(v > 0 for v in chart.values), "значения на pie должны быть положительными"


def test_windows_encoding_statement():
    summary = analyze_statement(CSV_RU.encode("cp1251"), "выписка.csv")
    assert summary.income == 50000.0
