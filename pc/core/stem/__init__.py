"""Учебный блок: точная математика, библиография и карточки для повторения.

Модель уверенно ошибается в арифметике и путает знаки в преобразованиях,
поэтому считает здесь не она, а SymPy: агент только формулирует задачу и
объясняет результат.
"""

from __future__ import annotations

from core.stem.bibliography import Reference, format_gost, parse_reference
from core.stem.solver import SolveError, solve_problem

__all__ = ["Reference", "SolveError", "format_gost", "parse_reference", "solve_problem"]
