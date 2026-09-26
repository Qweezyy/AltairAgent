"""Разбор банковской выписки: категории, итоги, крупные траты, помесячно.

Зачем в агенте: выписка приходит таблицей на сотни строк, и вручную свести её
в «сколько ушло на еду в марте» — полдня. Здесь считается всё, а модель потом
объясняет и советует. Считает код: ошибиться в сумме, разбирая финансы,
недопустимо.

Формат выписок у банков разный, поэтому колонки определяются по заголовкам, а
не по фиксированным позициям. Категория — по ключевым словам в описании; список
правил открыт для расширения.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from core.analytics.charts import Chart, Series
from core.logging_setup import get_logger

logger = get_logger("analytics.finance")


class StatementError(Exception):
    """Выписку не удалось разобрать (понятная причина)."""


#: Как называются нужные колонки у разных банков. Сравнение без учёта регистра.
DATE_HEADERS = ("дата", "date", "дата операции", "дата платежа", "transaction date")
AMOUNT_HEADERS = ("сумма", "amount", "сумма операции", "сумма в валюте счёта", "value")
DESC_HEADERS = ("описание", "description", "назначение", "детали", "получатель", "merchant", "narrative")

#: Категория -> ключевые слова в описании (нижним регистром). Порядок важен:
#: первое совпадение выигрывает, поэтому специфичное идёт раньше общего.
CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Зарплата", ("зарплат", "заработн", "salary", "аванс", "payroll")),
    ("Переводы", ("перевод", "transfer", "p2p", "c2c")),
    ("Продукты", ("пятёроч", "пятероч", "магнит", "перекрёст", "перекрест", "ашан", "лента",
                  "продукт", "супермаркет", "grocery", "spar", "metro")),
    ("Кафе и рестораны", ("кафе", "ресторан", "кофе", "coffee", "макдон", "kfc", "бургер",
                          "restaurant", "cafe", "додо", "суши", "пицц")),
    ("Транспорт", ("метро", "автобус", "такси", "taxi", "uber", "яндекс.такси", "заправ",
                   "азс", "лукойл", "бензин", "fuel", "transport", "тройка")),
    ("Связь и интернет", ("мтс", "билайн", "мегафон", "теле2", "tele2", "интернет", "ростелеком",
                          "связь", "mobile", "internet")),
    ("Жильё и ЖКХ", ("жкх", "квартплат", "аренд", "ипотек", "коммунал", "электроэнерг", "газпром")),
    ("Здоровье", ("аптек", "pharmacy", "клиник", "больниц", "медиц", "стоматолог", "здоров")),
    ("Развлечения", ("кино", "театр", "netflix", "spotify", "подписк", "игр", "steam", "concert")),
    ("Одежда", ("одежд", "обув", "zara", "uniqlo", "h&m", "wildberries", "ozon", "lamoda")),
    ("Снятие наличных", ("снятие", "банкомат", "atm", "cash", "выдача наличных")),
)


@dataclass(slots=True)
class Transaction:
    date: str
    amount: float
    description: str
    category: str = ""

    @property
    def month(self) -> str:
        """Год-месяц для помесячной сводки; пусто, если дату не разобрать."""
        match = re.search(r"(\d{4})[-./](\d{2})", self.date) or re.search(
            r"(\d{2})[-./](\d{2})[-./](\d{4})", self.date
        )
        if not match:
            return ""
        groups = match.groups()
        return f"{groups[0]}-{groups[1]}" if len(groups[0]) == 4 else f"{groups[2]}-{groups[1]}"


@dataclass(slots=True)
class StatementSummary:
    transactions: list[Transaction]
    income: float = 0.0
    expense: float = 0.0
    by_category: dict[str, float] = field(default_factory=dict)
    by_month: dict[str, float] = field(default_factory=dict)
    top_expenses: list[Transaction] = field(default_factory=list)
    currency: str = ""

    @property
    def balance(self) -> float:
        return round(self.income + self.expense, 2)

    def expense_chart(self) -> Chart:
        """Круговая диаграмма расходов по категориям."""
        items = sorted(self.by_category.items(), key=lambda kv: kv[1])  # расходы отрицательны
        items = [(name, -total) for name, total in items if total < 0]
        return Chart(
            kind="pie",
            title="Расходы по категориям",
            labels=[name for name, _ in items],
            values=[round(total, 2) for _, total in items],
        )

    def monthly_chart(self) -> Chart:
        """Столбчатый график баланса по месяцам."""
        months = sorted(self.by_month)
        return Chart(
            kind="bar",
            title="Баланс по месяцам",
            x=months,
            series=[Series(name="Баланс", y=[round(self.by_month[m], 2) for m in months])],
            x_label="Месяц",
            y_label="Сумма",
        )

    def to_text(self) -> str:
        lines = [
            f"Операций: {len(self.transactions)}",
            f"Поступления: {self.income:,.2f}".replace(",", " "),
            f"Расходы: {self.expense:,.2f}".replace(",", " "),
            f"Итог: {self.balance:,.2f}".replace(",", " "),
            "",
            "Расходы по категориям:",
        ]
        spent = sorted(
            ((name, total) for name, total in self.by_category.items() if total < 0),
            key=lambda kv: kv[1],
        )
        for name, total in spent:
            lines.append(f"  {name}: {-total:,.2f}".replace(",", " "))

        if self.top_expenses:
            lines.append("")
            lines.append("Крупнейшие траты:")
            for tx in self.top_expenses:
                lines.append(f"  {tx.date} · {-tx.amount:,.2f}".replace(",", " ") + f" · {tx.description[:60]}")
        return "\n".join(lines)


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _find_column(headers: list[str], candidates: tuple[str, ...]) -> int:
    """Индекс колонки по списку возможных заголовков (сначала точное, потом вхождение)."""
    lowered = [h.strip().lower() for h in headers]
    for candidate in candidates:
        if candidate in lowered:
            return lowered.index(candidate)
    for index, header in enumerate(lowered):
        if any(candidate in header for candidate in candidates):
            return index
    return -1


def _parse_amount(raw: str) -> float | None:
    """Разбирает сумму: убирает пробелы-разделители тысяч, валюту, запятую как точку."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # Знак минуса иногда записан в скобках: (1 200,00)
    negative = text.startswith("(") and text.endswith(")")
    text = re.sub(r"[^\d,.\-]", "", text.replace(" ", ""))
    if not text or text in ("-", ".", ","):
        return None
    # Если есть и запятая, и точка — точка это разделитель тысяч, запятая — дробь.
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


def categorize(description: str) -> str:
    """Категория операции по ключевым словам описания."""
    low = description.lower()
    for name, keywords in CATEGORY_RULES:
        if any(word in low for word in keywords):
            return name
    return "Прочее"


def analyze_statement(data: bytes, name: str, *, top: int = 5) -> StatementSummary:
    """Разбирает выписку (CSV или XLSX) и сводит её в отчёт."""
    suffix = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if suffix in ("xlsx", "xlsm"):
        rows = _read_xlsx_rows(data)
    else:
        rows = _read_csv_rows(_decode(data))

    if not rows:
        raise StatementError("Файл пуст или не содержит таблицы.")

    headers = rows[0]
    date_col = _find_column(headers, DATE_HEADERS)
    amount_col = _find_column(headers, AMOUNT_HEADERS)
    desc_col = _find_column(headers, DESC_HEADERS)

    if amount_col == -1:
        raise StatementError(
            "Не нашёл колонку с суммой. Ожидались заголовки вроде «Сумма», «Amount». "
            f"Найдены колонки: {', '.join(headers)}."
        )

    transactions: list[Transaction] = []
    for row in rows[1:]:
        if amount_col >= len(row):
            continue
        amount = _parse_amount(row[amount_col])
        if amount is None or amount == 0:
            continue
        date = row[date_col].strip() if 0 <= date_col < len(row) else ""
        desc = row[desc_col].strip() if 0 <= desc_col < len(row) else ""
        transactions.append(Transaction(date=date, amount=amount, description=desc, category=categorize(desc)))

    if not transactions:
        raise StatementError("В файле не нашлось ни одной операции с суммой.")

    return _summarize(transactions, top)


def _summarize(transactions: list[Transaction], top: int) -> StatementSummary:
    summary = StatementSummary(transactions=transactions)
    for tx in transactions:
        if tx.amount > 0:
            summary.income += tx.amount
        else:
            summary.expense += tx.amount
        summary.by_category[tx.category] = round(summary.by_category.get(tx.category, 0.0) + tx.amount, 2)
        month = tx.month
        if month:
            summary.by_month[month] = round(summary.by_month.get(month, 0.0) + tx.amount, 2)

    summary.income = round(summary.income, 2)
    summary.expense = round(summary.expense, 2)
    summary.top_expenses = sorted(
        (tx for tx in transactions if tx.amount < 0), key=lambda t: t.amount
    )[:top]
    return summary


def _read_csv_rows(text: str) -> list[list[str]]:
    # Разделитель у банков разный: пробуем определить по первой строке.
    sample = text[:2000]
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [row for row in reader if any(cell.strip() for cell in row)]


def _read_xlsx_rows(data: bytes) -> list[list[str]]:
    try:
        import openpyxl
    except ImportError as exc:
        raise StatementError(
            "Для выписок Excel нужен пакет 'openpyxl'. Установите: pip install openpyxl"
        ) from exc

    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise StatementError(f"Не удалось открыть книгу Excel: {exc}") from exc

    try:
        sheet = book.worksheets[0]
        rows = []
        for row in sheet.iter_rows(values_only=True):
            cells = ["" if cell is None else str(cell) for cell in row]
            if any(cell.strip() for cell in cells):
                rows.append(cells)
        return rows
    finally:
        book.close()
