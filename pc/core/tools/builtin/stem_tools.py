"""Учебные инструменты: точная математика, ГОСТ-библиография, колоды Anki."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from core.errors import ToolError
from core.events import ArtifactCreated
from core.i18n import tr
from core.security.paths import resolve_path, safe_relpath
from core.stem.anki import AnkiError, build_deck, parse_cards
from core.stem.bibliography import Reference, format_gost, format_list, parse_reference
from core.stem.solver import OPERATIONS, SolveError, solve_problem
from core.tools.base import Tool, ToolContext

# ------------------------------------------------------------ solve_math


class SolveMathArgs(BaseModel):
    operation: Literal[
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
    ] = Field(description="Что сделать с выражением")
    expression: str = Field(
        description="Выражение или уравнение: 'x^2 - 4', 'Eq(x**2, 4)', '[[1,2],[3,4]]'"
    )
    variable: str = Field(default="x", description="Переменная, по которой считаем")
    point: str = Field(default="", description="Точка для предела или разложения в ряд")
    lower: str = Field(default="", description="Нижний предел интегрирования")
    upper: str = Field(default="", description="Верхний предел интегрирования")
    order: int = Field(default=1, ge=1, le=12, description="Порядок производной или ряда")


class SolveMathTool(Tool):
    name = "solve_math"
    description = (
        "Считает математику точно: решает уравнения, берёт производные и интегралы, "
        "упрощает выражения, находит пределы и разложения в ряд, работает с матрицами. "
        "Возвращает ответ, ход решения и запись в LaTeX. "
        "ВСЕГДА используй этот инструмент вместо счёта в уме: в уме модель ошибается со знаками, "
        "а здесь считает SymPy."
    )
    Args = SolveMathArgs
    category = "read"
    timeout = 60.0

    async def run(self, args: SolveMathArgs, ctx: ToolContext) -> str:
        try:
            solution = solve_problem(
                args.operation,
                args.expression,
                variable=args.variable,
                point=args.point,
                lower=args.lower,
                upper=args.upper,
                order=args.order,
            )
        except SolveError as exc:
            raise ToolError(str(exc)) from exc

        return solution.to_text()


# --------------------------------------------------------- bibliography


class ReferenceModel(BaseModel):
    kind: Literal["book", "article", "web", "thesis", "law"] = Field(
        default="book", description="Тип источника"
    )
    authors: list[str] = Field(default_factory=list, description="Авторы: «Иванов И. И.»")
    title: str = Field(description="Название работы")
    source: str = Field(default="", description="Издательство, журнал или название сайта")
    year: str = Field(default="", description="Год издания")
    city: str = Field(default="", description="Город издания")
    pages: str = Field(default="", description="Объём («248») или диапазон страниц («12-19»)")
    volume: str = Field(default="", description="Том")
    issue: str = Field(default="", description="Номер выпуска")
    url: str = Field(default="", description="Ссылка для электронных источников")
    accessed: str = Field(default="", description="Дата обращения: 15.08.2026")
    edition: str = Field(default="", description="Сведения об издании: «2-е изд., испр.»")


class BibliographyArgs(BaseModel):
    references: list[ReferenceModel] = Field(
        description="Источники для списка литературы", min_length=1, max_length=200
    )


class BibliographyTool(Tool):
    name = "format_bibliography"
    description = (
        "Оформляет список литературы по ГОСТ Р 7.0.100–2018: книги, статьи, "
        "электронные ресурсы, диссертации, нормативные акты. "
        "Сортирует по алфавиту и расставляет тире, двоеточия и косые черты по правилам. "
        "Используй, когда нужен список литературы для реферата, курсовой или диплома."
    )
    Args = BibliographyArgs
    category = "read"
    timeout = 30.0

    async def run(self, args: BibliographyArgs, ctx: ToolContext) -> str:
        references: list[Reference] = []
        for item in args.references:
            try:
                references.append(parse_reference(item.model_dump()))
            except ValueError as exc:
                raise ToolError(str(exc)) from exc

        try:
            listing = format_list(references)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

        return f"Список литературы (ГОСТ Р 7.0.100–2018):\n\n{listing}"


# ----------------------------------------------------------- anki deck


class CardModel(BaseModel):
    question: str = Field(description="Лицевая сторона карточки")
    answer: str = Field(description="Оборотная сторона карточки")
    tags: list[str] = Field(default_factory=list, description="Метки для фильтрации в Anki")


class AnkiArgs(BaseModel):
    deck_name: str = Field(description="Название колоды, как она будет видна в Anki")
    cards: list[CardModel] = Field(description="Карточки колоды", min_length=1, max_length=500)
    path: str = Field(
        default="",
        description="Куда сохранить .apkg. Пусто — рядом в рабочей папке по имени колоды.",
    )


class AnkiDeckTool(Tool):
    name = "create_anki_deck"
    description = (
        "Собирает колоду Anki (.apkg) из пар «вопрос — ответ» для интервального повторения. "
        "Файл открывается двойным щелчком в Anki и синхронизируется с телефоном. "
        "Хорошая карточка проверяет одну мысль: не переписывай в неё абзац конспекта."
    )
    Args = AnkiArgs
    category = "edit"
    timeout = 120.0

    def approval_reason(self, args: AnkiArgs) -> str:
        return tr("appr.anki", deck=args.deck_name, n=len(args.cards))

    async def run(self, args: AnkiArgs, ctx: ToolContext) -> str:
        name = args.path.strip() or f"{_safe_name(args.deck_name)}.apkg"
        if not name.lower().endswith(".apkg"):
            name += ".apkg"

        destination = resolve_path(name, settings=ctx.settings)
        cards = parse_cards([card.model_dump() for card in args.cards])

        try:
            build_deck(cards, args.deck_name.strip(), destination)
        except AnkiError as exc:
            raise ToolError(str(exc)) from exc

        relative = safe_relpath(destination, ctx.settings)
        await ctx.emitter(
            ArtifactCreated(
                path=relative,
                name=destination.name,
                kind="data",
                size_bytes=destination.stat().st_size,
            )
        )
        return (
            f"Колода «{args.deck_name}» собрана: {relative} "
            f"({len(cards)} карточек, {destination.stat().st_size / 1024:.0f} КБ). "
            "Откройте файл двойным щелчком — Anki импортирует её."
        )


def _safe_name(text: str) -> str:
    """Имя файла из названия колоды: без символов, запрещённых в путях."""
    cleaned = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in text).strip()
    return (cleaned or "колода").replace(" ", "_")[:60]


__all__ = ["OPERATIONS", "AnkiDeckTool", "BibliographyTool", "SolveMathTool", "format_gost"]
