"""Библиографические ссылки по ГОСТ Р 7.0.100–2018.

Зачем отдельный модуль: правила расстановки точек, тире и косых черт в ГОСТе
формальные, и модель их регулярно путает — то запятая вместо тире, то пропуск
области физической характеристики. Форматирование должно быть кодом, а не
воспоминанием модели.

Разбор источника из интернета вынесен наружу: сюда приходят уже готовые поля.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Типы источников, для которых схема описания различается.
KINDS = ("book", "article", "web", "thesis", "law")

_INITIALS_RE = re.compile(r"^([А-ЯЁA-Z][а-яёa-z\-]+)\s+([А-ЯЁA-Z])\.?\s*([А-ЯЁA-Z])?\.?$")


@dataclass(slots=True)
class Reference:
    """Один источник."""

    kind: str = "book"
    authors: list[str] = field(default_factory=list)
    title: str = ""
    #: Издательство, журнал или название сайта.
    source: str = ""
    year: str = ""
    city: str = ""
    pages: str = ""
    volume: str = ""
    issue: str = ""
    url: str = ""
    accessed: str = ""
    edition: str = ""

    def sort_key(self) -> tuple[str, str]:
        """Список литературы по ГОСТу — по алфавиту авторов, затем заглавий."""
        first = self.authors[0] if self.authors else ""
        return (first.lower(), self.title.lower())


def parse_reference(data: dict) -> Reference:
    """Собирает Reference из словаря, приводя типы к ожидаемым."""
    authors = data.get("authors") or []
    if isinstance(authors, str):
        # Модель часто присылает одну строку «Иванов И.И., Петров П.П.»
        authors = [part.strip() for part in re.split(r"[;,](?![\s]*[А-ЯЁA-Z]\.)", authors)]

    kind = str(data.get("kind") or "book").lower()
    if kind not in KINDS:
        kind = "book"

    return Reference(
        kind=kind,
        authors=[str(a).strip() for a in authors if str(a).strip()],
        title=str(data.get("title") or "").strip(),
        source=str(data.get("source") or "").strip(),
        year=str(data.get("year") or "").strip(),
        city=str(data.get("city") or "").strip(),
        pages=str(data.get("pages") or "").strip(),
        volume=str(data.get("volume") or "").strip(),
        issue=str(data.get("issue") or "").strip(),
        url=str(data.get("url") or "").strip(),
        accessed=str(data.get("accessed") or "").strip(),
        edition=str(data.get("edition") or "").strip(),
    )


def short_author(author: str) -> str:
    """«Иванов Иван Иванович» -> «Иванов И. И.» — форма для заголовка описания."""
    author = author.strip().rstrip(",")
    if not author:
        return ""

    # Уже в короткой форме: «Иванов И. И.»
    if re.search(r"[А-ЯЁA-Z]\.", author):
        return re.sub(r"\.\s*([А-ЯЁA-Z])", r". \1", author).strip()

    parts = author.split()
    if len(parts) == 1:
        return parts[0]
    initials = " ".join(f"{name[0].upper()}." for name in parts[1:3])
    return f"{parts[0]} {initials}"


def heading_form(author: str) -> str:
    """Заголовок описания: «Лутц, М.» — фамилия отделяется запятой."""
    short = short_author(author)
    parts = short.split(" ", 1)
    return f"{parts[0]}, {parts[1]}" if len(parts) == 2 else short


def direct_form(author: str) -> str:
    """Прямая форма для области ответственности: «М. Лутц»."""
    short = short_author(author)
    parts = short.split(" ", 1)
    return f"{parts[1]} {parts[0]}" if len(parts) == 2 else short


def _authors_tail(authors: list[str]) -> str:
    """Область ответственности после косой черты.

    По ГОСТу имена здесь идут в прямой форме («М. Лутц»), до четырёх авторов
    перечисляются полностью, при большем числе указывается первый и «[и др.]».
    """
    if not authors:
        return ""
    names = [direct_form(a) for a in authors]
    if len(names) > 4:
        return f"{names[0]} [и др.]"
    return ", ".join(names)


def format_gost(reference: Reference) -> str:
    """Библиографическая запись по ГОСТ Р 7.0.100–2018."""
    if not reference.title:
        raise ValueError("У источника нет названия — описание составить нельзя.")

    # Заголовок описания ставится, только когда автор один: у работы двух и
    # более авторов по ГОСТу заголовка нет, все имена уходят за косую черту.
    head = heading_form(reference.authors[0]) if len(reference.authors) == 1 else ""

    title = reference.title.rstrip(". ")
    responsibility = _authors_tail(reference.authors)
    title_block = f"{title} / {responsibility}" if responsibility else title

    builder = {
        "book": _book_tail,
        "article": _article_tail,
        "web": _web_tail,
        "thesis": _thesis_tail,
        "law": _law_tail,
    }[reference.kind]

    record = f"{head} {title_block}" if head else title_block
    tail = builder(reference)
    # У статей и сайтов область следом начинается с «//» — точка перед ней не
    # ставится, в отличие от книжного описания.
    separator = " " if tail.startswith("//") else ". — "
    return f"{record}{separator}{tail}".replace("..", ".").strip()


def _book_tail(reference: Reference) -> str:
    chunks = []
    if reference.edition:
        chunks.append(reference.edition.rstrip("."))
    place = " : ".join(part for part in (reference.city or "Б. м.", reference.source) if part)
    place_year = f"{place}, {reference.year}" if reference.year else place
    chunks.append(place_year)
    if reference.pages:
        chunks.append(f"{reference.pages} с")
    return ". — ".join(chunks) + "."


def _article_tail(reference: Reference) -> str:
    chunks = [f"// {reference.source}" if reference.source else "// [Б. и.]"]
    detail = []
    if reference.year:
        detail.append(reference.year)
    if reference.volume:
        detail.append(f"Т. {reference.volume}")
    if reference.issue:
        detail.append(f"№ {reference.issue}")
    if detail:
        chunks.append(". — ".join(detail))
    if reference.pages:
        chunks.append(f"С. {reference.pages}")
    return ". — ".join(chunks) + "."


def _web_tail(reference: Reference) -> str:
    chunks = []
    if reference.source:
        chunks.append(f"// {reference.source}")
    if reference.year:
        chunks.append(reference.year)
    chunks.append("Текст : электронный")
    if reference.url:
        chunks.append(f"URL: {reference.url}")
    if reference.accessed:
        chunks.append(f"(дата обращения: {reference.accessed})")
    return ". — ".join(chunks) + "."


def _thesis_tail(reference: Reference) -> str:
    chunks = ["дис. … канд. наук"]
    place = " : ".join(part for part in (reference.city or "Б. м.", reference.source) if part)
    chunks.append(f"{place}, {reference.year}" if reference.year else place)
    if reference.pages:
        chunks.append(f"{reference.pages} с")
    return ". — ".join(chunks) + "."


def _law_tail(reference: Reference) -> str:
    chunks = []
    if reference.source:
        chunks.append(f"// {reference.source}")
    if reference.year:
        chunks.append(reference.year)
    if reference.issue:
        chunks.append(f"№ {reference.issue}")
    if reference.url:
        chunks.append(f"URL: {reference.url}")
    if reference.accessed:
        chunks.append(f"(дата обращения: {reference.accessed})")
    return ". — ".join(chunks) + "."


def format_list(references: list[Reference]) -> str:
    """Нумерованный список литературы, отсортированный по алфавиту."""
    ordered = sorted(references, key=lambda r: r.sort_key())
    return "\n".join(f"{index}. {format_gost(ref)}" for index, ref in enumerate(ordered, 1))
