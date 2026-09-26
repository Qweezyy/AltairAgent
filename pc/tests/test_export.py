"""Экспорт диалога в Markdown / HTML / PDF."""

from __future__ import annotations

from core import export


def _sample() -> dict:
    return {
        "title": "Мой диалог про Python",
        "model": "openrouter/auto",
        "updated_at": 1_700_000_000.0,
        "timeline": [
            {"kind": "user", "text": "Что такое рекурсия?"},
            {
                "kind": "answer",
                "text": "Рекурсия — это когда функция вызывает **саму себя**.\n\n"
                "```python\ndef f(n):\n    return f(n - 1)\n```",
                "steps": 2,
                "duration_ms": 3400,
                "cost_usd": 0.0012,
            },
            {"kind": "user", "text": "А второй вопрос"},
            {"kind": "answer", "text": "Второй ответ.", "steps": 1, "duration_ms": 900},
        ],
    }


def test_markdown_full_dialog():
    md = export.session_to_markdown(_sample())
    assert md.startswith("# Мой диалог про Python")
    assert "🧑 Что такое рекурсия?" in md
    assert "саму себя" in md
    assert "Второй ответ." in md
    # Мета-подпись под ответом.
    assert "шагов: 2" in md
    assert "$0.0012" in md


def test_markdown_single_turn():
    md = export.session_to_markdown(_sample(), turn=1)
    assert "Второй ответ." in md
    # Первый ответ и вопросы в режиме одного хода не попадают.
    assert "саму себя" not in md
    assert "🧑" not in md


def test_html_is_self_contained():
    html = export.session_to_html(_sample())
    assert html.startswith("<!DOCTYPE html>")
    assert "<style>" in html
    assert "<h1>Мой диалог про Python</h1>" in html
    # Код-блок превратился в <pre>, инлайн-жирный — в <strong>.
    assert "<pre><code>" in html
    assert "<strong>саму себя</strong>" in html
    # Заголовок markdown не задвоился в тело.
    assert html.count("Мой диалог про Python") <= 3


def test_html_escapes_content():
    data = _sample()
    data["timeline"][1]["text"] = "Опасно: <script>alert(1)</script>"
    html = export.session_to_html(data)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_safe_filename():
    assert export.safe_filename("Мой диалог: тест/2", "md") == "Мой диалог тест2.md"
    assert export.safe_filename("", "pdf") == "dialog.pdf"
    long = export.safe_filename("x" * 200, "html")
    assert len(long) <= len("html") + 1 + 60


def test_find_browser_returns_path_or_none():
    # Не падает независимо от того, установлен ли Edge/Chrome.
    result = export.find_print_browser()
    assert result is None or isinstance(result, str)
