"""Реестр источников: нумерация, дедупликация, список литературы.

Зачем: отчёт без ссылок бесполезен — проверить его нельзя. Каждый факт должен
опираться на источник с номером, а номера обязаны быть устойчивыми на
протяжении всего исследования.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse, urlunparse

#: Параметры, которые не влияют на содержимое страницы, но мешают дедупликации.
TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "yclid", "_openstat", "ref=")


@dataclass(slots=True)
class Source:
    """Один источник исследования."""

    index: int
    url: str
    title: str = ""
    snippet: str = ""
    #: Извлечённый текст (пусто, пока страницу не открывали).
    text: str = ""
    error: str = ""

    @property
    def domain(self) -> str:
        return urlparse(self.url).netloc.removeprefix("www.")

    @property
    def is_read(self) -> bool:
        return bool(self.text)

    def citation(self) -> str:
        title = self.title or self.domain or self.url
        return f"[{self.index}] {title} — {self.url}"


def normalize_url(url: str) -> str:
    """Приводит ссылку к каноническому виду для сравнения.

    Убирает рекламные метки, якорь, завершающий слэш и разницу http/https:
    три ссылки на одну статью не должны занять три места в отчёте.
    """
    parsed = urlparse(url.strip())
    query = "&".join(
        part
        for part in parsed.query.split("&")
        if part and not any(part.lower().startswith(bad) for bad in TRACKING_PARAMS)
    )
    path = parsed.path.rstrip("/") or "/"
    netloc = parsed.netloc.lower().removeprefix("www.")
    return urlunparse(("https", netloc, path, "", query, ""))


@dataclass(slots=True)
class SourceRegistry:
    """Хранит источники в порядке появления и не допускает дублей."""

    sources: list[Source] = field(default_factory=list)
    _seen: dict[str, Source] = field(default_factory=dict, repr=False)

    def add(self, url: str, title: str = "", snippet: str = "") -> Source:
        """Добавляет источник или возвращает уже известный с тем же адресом."""
        key = normalize_url(url)
        existing = self._seen.get(key)
        if existing is not None:
            # Заголовок и описание могли прийти позже и быть содержательнее.
            if title and not existing.title:
                existing.title = title
            if snippet and not existing.snippet:
                existing.snippet = snippet
            return existing

        source = Source(index=len(self.sources) + 1, url=url.strip(), title=title, snippet=snippet)
        self.sources.append(source)
        self._seen[key] = source
        return source

    def by_index(self, index: int) -> Source | None:
        return next((s for s in self.sources if s.index == index), None)

    def read_sources(self) -> list[Source]:
        return [s for s in self.sources if s.is_read]

    def domains(self) -> set[str]:
        return {s.domain for s in self.sources}

    def bibliography(self, only_read: bool = True) -> str:
        """Список литературы для конца отчёта."""
        items = self.read_sources() if only_read else self.sources
        return "\n".join(source.citation() for source in items)

    def __len__(self) -> int:
        return len(self.sources)
