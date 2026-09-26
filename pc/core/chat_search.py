"""Поиск по сохранённым чатам — этому и другим.

Агенту нужно уметь вспомнить, «где мы обсуждали настройку прокси» или «что я
отвечал про формат отчёта». Эмбеддинги для этого избыточны: чаты — это текст,
а полнотекстовый поиск по нему быстрый, офлайновый и понятный. Читаем JSON
сессий напрямую (без разбора в объекты) — так поиск по сотне чатов мгновенный.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger("chat_search")

_WORD_RE = re.compile(r"[\w\-]{2,}", re.UNICODE)
SNIPPET_RADIUS = 90


@dataclass(slots=True)
class ChatHit:
    session_id: str
    title: str
    role: str  # «запрос» | «ответ»
    snippet: str
    updated_at: float
    score: float


def _snippet(text: str, terms: set[str]) -> str:
    """Кусок текста вокруг первого совпавшего слова."""
    low = text.lower()
    pos = -1
    for term in terms:
        found = low.find(term)
        if found != -1 and (pos == -1 or found < pos):
            pos = found
    if pos == -1:
        return text[: SNIPPET_RADIUS * 2].strip()
    start = max(0, pos - SNIPPET_RADIUS)
    end = min(len(text), pos + SNIPPET_RADIUS)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end].strip()}{suffix}"


def _searchable_entries(data: dict) -> list[tuple[str, str]]:
    """Пары (роль, текст) из ленты чата: запросы пользователя и ответы агента."""
    entries: list[tuple[str, str]] = []
    for item in data.get("timeline", []):
        kind = item.get("kind")
        if kind == "user":
            entries.append(("запрос", str(item.get("text", ""))))
        elif kind == "answer":
            entries.append(("ответ", str(item.get("text", ""))))
    # Если ленты нет (старый чат) — берём сообщения модели.
    if not entries:
        for msg in data.get("messages", []):
            role = msg.get("role")
            content = msg.get("content")
            if role in ("user", "assistant") and isinstance(content, str):
                entries.append(("запрос" if role == "user" else "ответ", content))
    return entries


def search_chats(
    storage_dir: Path,
    query: str,
    *,
    current_session_id: str = "",
    scope: str = "all",
    limit: int = 8,
) -> list[ChatHit]:
    """Ищет запрос по чатам. scope: 'all' — все чаты, 'current' — только текущий."""
    terms = {w.lower() for w in _WORD_RE.findall(query or "")}
    if not terms:
        return []

    hits: list[ChatHit] = []
    try:
        files = sorted(storage_dir.glob("*.json"))
    except OSError:
        return []

    for file in files:
        if file.name.endswith(".tmp"):
            continue
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue

        session_id = data.get("id", file.stem)
        if scope == "current" and current_session_id and session_id != current_session_id:
            continue

        title = data.get("title", "Без названия")
        updated = float(data.get("updated_at", 0) or 0)

        for role, text in _searchable_entries(data):
            low = text.lower()
            overlap = sum(1 for term in terms if term in low)
            if not overlap:
                continue
            hits.append(
                ChatHit(
                    session_id=session_id,
                    title=title,
                    role=role,
                    snippet=_snippet(text, terms),
                    updated_at=updated,
                    score=overlap + updated / 1e13,  # совпадения важнее, при равенстве — свежее
                )
            )

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]
