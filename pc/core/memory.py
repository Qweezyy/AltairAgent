"""Долгосрочная память агента: факты о пользователе и проектах между чатами.

Зачем без эмбеддингов: тяжёлая векторная БД раздула бы локальное приложение в
разы, а её главный выигрыш — поиск по смыслу — агент добирает сам, формулируя
запрос по-разному. Здесь память проще и честнее: агент явно сохраняет короткие
факты, а они хранятся обычным JSON, который пользователь может открыть и стереть.

Память общая для всех чатов (лежит в папке данных), поэтому агент помнит стек,
предпочтения и цели между сессиями, а не начинает каждый раз с нуля.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from core.fs_atomic import safe_replace
from core.logging_setup import get_logger

logger = get_logger("memory")

#: Категории фактов. user/preference почти всегда уместны и подаются в промпт
#: всегда; project/fact — только когда совпадают с темой запроса.
CATEGORIES = ("user", "preference", "project", "fact")
ALWAYS_RELEVANT = ("user", "preference")

MAX_FACTS = 1000
#: Сколько фактов максимум вставляем в системный промпт (чтобы не раздувать).
INJECT_LIMIT = 20

_WORD_RE = re.compile(r"[\w\-]{3,}", re.UNICODE)


@dataclass(slots=True)
class Fact:
    id: str
    text: str
    category: str
    created_at: float
    session_id: str = ""

    def keywords(self) -> set[str]:
        return {w.lower() for w in _WORD_RE.findall(self.text)}


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


class MemoryStore:
    """Хранилище фактов на диске (общее для всех чатов)."""

    def __init__(self, base_dir: Path) -> None:
        self.path = base_dir / "memory.json"
        self._facts: list[Fact] = self._load()

    # ------------------------------------------------------------------

    def remember(self, text: str, category: str = "fact", session_id: str = "") -> Fact | None:
        """Сохраняет факт. Возвращает его; None — если это дубль пустого/повтора."""
        text = text.strip()
        if not text:
            return None
        if category not in CATEGORIES:
            category = "fact"

        normalized = _normalize(text)
        for existing in self._facts:
            if _normalize(existing.text) == normalized:
                return existing  # уже помним — не плодим дубли

        fact = Fact(
            id=uuid.uuid4().hex[:12],
            text=text,
            category=category,
            created_at=time.time(),
            session_id=session_id,
        )
        self._facts.append(fact)
        if len(self._facts) > MAX_FACTS:
            self._facts = self._facts[-MAX_FACTS:]
        self._save()
        return fact

    def recall(self, query: str, limit: int = 10) -> list[Fact]:
        """Ищет факты по ключевым словам запроса, релевантные — выше."""
        terms = {w.lower() for w in _WORD_RE.findall(query or "")}
        if not terms:
            return self._recent(limit)

        scored: list[tuple[float, Fact]] = []
        for fact in self._facts:
            overlap = len(terms & fact.keywords())
            base = 2 if fact.category in ALWAYS_RELEVANT else 0
            if overlap or base:
                # Немного приоритета свежему.
                recency = fact.created_at / 1e10
                scored.append((overlap + base + recency, fact))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [fact for _, fact in scored[:limit]]

    def relevant_for(self, task: str, limit: int = INJECT_LIMIT) -> list[Fact]:
        """Факты для подстановки в системный промпт под конкретную задачу.

        Всегда берём user/preference (они уместны почти всегда), плюс
        project/fact, совпавшие с темой запроса.
        """
        terms = {w.lower() for w in _WORD_RE.findall(task or "")}
        always = [f for f in self._facts if f.category in ALWAYS_RELEVANT]
        matched = [
            f
            for f in self._facts
            if f.category not in ALWAYS_RELEVANT and terms & f.keywords()
        ]
        # Свежие важнее среди совпавших.
        matched.sort(key=lambda f: f.created_at, reverse=True)
        combined = always + matched
        return combined[:limit]

    def all(self) -> list[Fact]:
        return list(self._facts)

    def forget(self, fact_id: str) -> bool:
        before = len(self._facts)
        self._facts = [f for f in self._facts if f.id != fact_id]
        if len(self._facts) != before:
            self._save()
            return True
        return False

    def clear(self) -> int:
        count = len(self._facts)
        self._facts = []
        self._save()
        return count

    def prompt_section(self, task: str) -> str:
        """Раздел системного промпта с релевантной памятью."""
        facts = self.relevant_for(task)
        if not facts:
            return ""
        labels = {"user": "About the user", "preference": "Preference",
                  "project": "Project", "fact": "Fact"}
        lines = ["<memory>", "What you remember across chats; use it when relevant."]
        for fact in facts:
            lines.append(f"- [{labels.get(fact.category, fact.category)}] {fact.text}")
        lines.append("</memory>")
        return "\n".join(lines)

    # ------------------------------------------------------------------

    def _recent(self, limit: int) -> list[Fact]:
        return sorted(self._facts, key=lambda f: f.created_at, reverse=True)[:limit]

    def _load(self) -> list[Fact]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        facts = []
        for item in data.get("facts", []) if isinstance(data, dict) else []:
            try:
                facts.append(Fact(**item))
            except TypeError:
                continue
        return facts

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".json.tmp")
            temp.write_text(
                json.dumps({"facts": [asdict(f) for f in self._facts]}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            safe_replace(temp, self.path)
        except OSError:  # pragma: no cover
            logger.debug("Не удалось сохранить память", exc_info=True)
