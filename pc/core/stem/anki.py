"""Колоды Anki (.apkg) из пар «вопрос — ответ».

Зачем в агенте: конспект прочитывается один раз и забывается, а карточки с
интервальным повторением — единственный способ удержать материал. Агент уже
разобрал тему, значит и карточки может собрать он.

Файл .apkg — это zip с базой SQLite. Собирается библиотекой genanki; если её
нет, вместо падения выдаётся понятная просьба её установить.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger("stem.anki")

#: Идентификаторы модели и колоды должны быть постоянными: при повторном
#: импорте Anki обновит карточки, а не создаст вторую копию колоды.
MODEL_ID = 1607392319
MODEL_NAME = "Локальный ИИ-агент: базовая"

MAX_CARDS = 500

CSS = """
.card {
  font-family: -apple-system, Segoe UI, sans-serif;
  font-size: 20px;
  text-align: left;
  color: #2b2724;
  background: #faf7f2;
  padding: 16px;
}
.tags { color: #9a8f83; font-size: 13px; margin-top: 12px; }
code, pre { font-family: Consolas, monospace; background: #efe9e0; padding: 2px 4px; }
"""


class AnkiError(Exception):
    """Колоду собрать не удалось."""


@dataclass(slots=True)
class Card:
    question: str
    answer: str
    tags: list[str] = field(default_factory=list)


def _clean(text: str) -> str:
    """Anki хранит поля как HTML: переносы строк иначе схлопнутся в одну."""
    text = (text or "").strip()
    return re.sub(r"\n{2,}", "<br><br>", text).replace("\n", "<br>")


def build_deck(cards: list[Card], deck_name: str, destination: Path) -> Path:
    """Собирает .apkg и возвращает путь к нему."""
    if not cards:
        raise AnkiError("Нет ни одной карточки — колоду собирать не из чего.")
    if len(cards) > MAX_CARDS:
        raise AnkiError(f"Слишком много карточек: {len(cards)} при лимите {MAX_CARDS}.")

    empty = [index for index, card in enumerate(cards, 1) if not card.question or not card.answer]
    if empty:
        raise AnkiError(
            f"У карточек {', '.join(map(str, empty[:10]))} пустой вопрос или ответ. "
            "Пустая карточка бесполезна при повторении."
        )

    try:
        import genanki
    except ImportError as exc:
        raise AnkiError(
            "Для колод Anki нужен пакет 'genanki'. Установите: pip install genanki"
        ) from exc

    model = genanki.Model(
        MODEL_ID,
        MODEL_NAME,
        fields=[{"name": "Вопрос"}, {"name": "Ответ"}],
        templates=[
            {
                "name": "Карточка",
                "qfmt": "{{Вопрос}}",
                "afmt": '{{FrontSide}}<hr id="answer">{{Ответ}}',
            }
        ],
        css=CSS,
    )

    # Идентификатор колоды выводим из названия: одна и та же тема при
    # повторной генерации не должна плодить дубликаты в Anki.
    deck_id = 2_000_000_000 + (abs(hash(deck_name)) % 500_000_000)
    deck = genanki.Deck(deck_id, deck_name)

    for card in cards:
        deck.add_note(
            genanki.Note(
                model=model,
                fields=[_clean(card.question), _clean(card.answer)],
                tags=[tag.replace(" ", "_") for tag in card.tags if tag],
            )
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    genanki.Package(deck).write_to_file(str(destination))
    logger.info("Колода Anki собрана: %s (%d карточек)", destination, len(cards))
    return destination


def parse_cards(raw: list[dict]) -> list[Card]:
    """Приводит карточки из аргументов инструмента к внутреннему виду."""
    cards: list[Card] = []
    for item in raw or []:
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = [part.strip() for part in tags.split(",")]
        cards.append(
            Card(
                question=str(item.get("question") or "").strip(),
                answer=str(item.get("answer") or "").strip(),
                tags=[str(tag).strip() for tag in tags if str(tag).strip()],
            )
        )
    return cards
