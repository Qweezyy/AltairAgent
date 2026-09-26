"""Глубокое исследование: рендеринг страниц, разбор документов, сбор отчётов.

Модуль отвечает на вопрос «что вообще есть по теме», а не «покажи одну
страницу». Инструменты в `core/tools/builtin/research.py` — тонкие обёртки
над этим кодом, чтобы логику можно было тестировать без агента.
"""

from __future__ import annotations

from core.research.documents import DocumentError, extract_document, is_document
from core.research.sources import Source, SourceRegistry

__all__ = ["DocumentError", "Source", "SourceRegistry", "extract_document", "is_document"]
