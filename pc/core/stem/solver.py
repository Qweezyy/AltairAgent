"""Точные вычисления через SymPy: уравнения, производные, интегралы, матрицы.

Зачем не доверять модели: она правдоподобно «решает» и при этом теряет знак,
путает корни и округляет там, где нельзя. Здесь считает библиотека, а модель
только ставит задачу и объясняет ответ — тогда ошибку видно сразу, а не через
три шага рассуждений.

Разбор выражений намеренно ограничен: `sympify` умеет исполнять произвольный
код, поэтому строка сначала проходит через белый список имён.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.logging_setup import get_logger

logger = get_logger("stem.solver")


class SolveError(Exception):
    """Задачу не удалось решить — с объяснением, что именно не вышло."""


#: Что разрешено писать в выражении. Всё остальное отсекается до разбора,
#: потому что sympify исполняет переданную строку как код Python.
ALLOWED_CHARS = re.compile(r"^[0-9a-zA-Zа-яА-Я_+\-*/^().,=<>! \[\]{}:'\"|&%\n]*$")

#: Имена, которые запрещены явно: даже внутри белого списка символов они
#: открывают доступ к интерпретатору.
FORBIDDEN = (
    "__",
    "import",
    "eval",
    "exec",
    "open",
    "compile",
    "globals",
    "locals",
    "getattr",
    "setattr",
    "lambda",
    "os.",
    "sys.",
    "subprocess",
)

OPERATIONS = (
    "solve",
    "simplify",
    "expand",
    "factor",
    "derivative",
    "integral",
    "limit",
    "series",
    "matrix",
    "evaluate",
)


@dataclass(slots=True)
class Solution:
    """Результат вычисления."""

    operation: str
    expression: str
    result: str
    latex: str = ""
    steps: list[str] = field(default_factory=list)
    numeric: str = ""

    def to_text(self) -> str:
        parts = [f"Задача: {self.operation} — {self.expression}"]
        if self.steps:
            parts.append("Ход решения:\n" + "\n".join(f"  {line}" for line in self.steps))
        parts.append(f"Ответ: {self.result}")
        if self.numeric and self.numeric != self.result:
            parts.append(f"Численно: {self.numeric}")
        if self.latex:
            parts.append(f"LaTeX: {self.latex}")
        return "\n".join(parts)


def _guard(text: str) -> str:
    """Проверяет строку до разбора: sympify исполняет то, что ему дали."""
    text = (text or "").strip()
    if not text:
        raise SolveError("Пустое выражение.")
    if len(text) > 2000:
        raise SolveError("Выражение слишком длинное (лимит 2000 символов).")
    lowered = text.lower()
    for bad in FORBIDDEN:
        if bad in lowered:
            raise SolveError(f"В выражении запрещённая конструкция: '{bad}'.")
    if not ALLOWED_CHARS.match(text):
        raise SolveError("В выражении есть недопустимые символы.")
    return text


def _parse(text: str):
    """Разбирает выражение SymPy с привычной математической записью."""
    from sympy.parsing.sympy_parser import (
        convert_xor,
        implicit_multiplication_application,
        parse_expr,
        standard_transformations,
    )

    transformations = standard_transformations + (
        implicit_multiplication_application,  # «2x» вместо «2*x»
        convert_xor,  # «^» как степень, а не «исключающее или»
    )
    try:
        return parse_expr(_guard(text), transformations=transformations, evaluate=True)
    except SolveError:
        raise
    except Exception as exc:  # noqa: BLE001 - разбор пользовательской строки
        raise SolveError(f"Не удалось разобрать выражение «{text}»: {exc}") from exc


def _symbol(name: str):
    import sympy

    return sympy.Symbol(_guard(name or "x"))


def solve_problem(
    operation: str,
    expression: str,
    *,
    variable: str = "x",
    point: str = "",
    lower: str = "",
    upper: str = "",
    order: int = 1,
) -> Solution:
    """Считает задачу указанного типа. Кидает SolveError с понятной причиной."""
    operation = (operation or "").strip().lower()
    if operation not in OPERATIONS:
        raise SolveError(f"Неизвестная операция '{operation}'. Доступны: {', '.join(OPERATIONS)}.")

    import sympy

    handler = {
        "solve": _solve_equation,
        "simplify": lambda e, **kw: (sympy.simplify(e), []),
        "expand": lambda e, **kw: (sympy.expand(e), []),
        "factor": lambda e, **kw: (sympy.factor(e), []),
        "derivative": _derivative,
        "integral": _integral,
        "limit": _limit,
        "series": _series,
        "matrix": _matrix,
        "evaluate": lambda e, **kw: (e.doit() if hasattr(e, "doit") else e, []),
    }[operation]

    expr = _parse(expression) if operation != "matrix" else expression
    try:
        value, steps = handler(
            expr, variable=variable, point=point, lower=lower, upper=upper, order=order
        )
    except SolveError:
        raise
    except Exception as exc:  # noqa: BLE001 - библиотека сообщает о своей проблеме
        raise SolveError(f"{type(exc).__name__}: {exc}") from exc

    numeric = ""
    try:
        approximation = sympy.N(value, 8)
        if str(approximation) != str(value):
            numeric = str(approximation)
    except Exception:  # noqa: BLE001 - численного вида может не быть
        numeric = ""

    return Solution(
        operation=operation,
        expression=str(expression),
        result=str(value),
        latex=sympy.latex(value),
        steps=steps,
        numeric=numeric,
    )


# ------------------------------------------------------------- операции


def _solve_equation(expr, *, variable: str = "x", **_: Any):
    import sympy

    symbol = _symbol(variable)
    # «x^2 = 4» и «x^2 - 4» — одно и то же уравнение; приводим к одному виду.
    equation = expr
    if isinstance(expr, sympy.Eq):
        equation = expr.lhs - expr.rhs

    roots = sympy.solve(equation, symbol, dict=False)
    if not roots:
        raise SolveError(
            f"Уравнение не имеет решений относительно '{variable}' "
            "(или SymPy не смог их найти в замкнутом виде)."
        )
    steps = [f"Приводим к виду f({variable}) = 0: {sympy.simplify(equation)} = 0"]
    return roots, steps


def _derivative(expr, *, variable: str = "x", order: int = 1, **_: Any):
    import sympy

    symbol = _symbol(variable)
    order = max(1, min(int(order or 1), 10))
    result = sympy.diff(expr, symbol, order)
    steps = [f"Дифференцируем по '{variable}'" + (f", порядок {order}" if order > 1 else "")]
    return sympy.simplify(result), steps


def _integral(expr, *, variable: str = "x", lower: str = "", upper: str = "", **_: Any):
    import sympy

    symbol = _symbol(variable)
    if lower and upper:
        result = sympy.integrate(expr, (symbol, _parse(lower), _parse(upper)))
        steps = [f"Определённый интеграл по '{variable}' от {lower} до {upper}"]
    else:
        result = sympy.integrate(expr, symbol)
        steps = [f"Неопределённый интеграл по '{variable}' (константу C дописать вручную)"]
    if result.has(sympy.Integral):
        raise SolveError("Интеграл не берётся в элементарных функциях.")
    return result, steps


def _limit(expr, *, variable: str = "x", point: str = "0", **_: Any):
    import sympy

    symbol = _symbol(variable)
    target = _parse(point or "0")
    return sympy.limit(expr, symbol, target), [f"Предел при {variable} → {point or '0'}"]


def _series(expr, *, variable: str = "x", point: str = "0", order: int = 6, **_: Any):
    import sympy

    symbol = _symbol(variable)
    target = _parse(point or "0")
    order = max(1, min(int(order or 6), 12))
    result = sympy.series(expr, symbol, target, order).removeO()
    return result, [f"Разложение в ряд в точке {point or '0'} до порядка {order}"]


def _matrix(raw: Any, **_: Any):
    """Матрица задаётся как [[1,2],[3,4]]: считаем определитель и обратную."""
    import sympy

    parsed = _parse(str(raw))
    try:
        matrix = sympy.Matrix(parsed)
    except Exception as exc:  # noqa: BLE001
        raise SolveError(f"Не похоже на матрицу: {exc}") from exc

    steps = [f"Размер: {matrix.rows}×{matrix.cols}", f"Ранг: {matrix.rank()}"]
    if matrix.rows == matrix.cols:
        determinant = matrix.det()
        steps.append(f"Определитель: {determinant}")
        if determinant != 0:
            steps.append(f"Обратная матрица: {matrix.inv()}")
        else:
            steps.append("Определитель равен нулю — обратной матрицы не существует.")
    return matrix, steps
