"""Graphics stack wiring: Mermaid vendored + hooked, math self-contained,
inline widget tools present. These guard against silently losing a feature
(the actual rendering is verified live in the browser)."""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"


def test_mermaid_vendored_and_exposes_global():
    js = STATIC / "vendor" / "mermaid" / "mermaid.min.js"
    assert js.is_file(), "mermaid.min.js должен быть вшит (vendor/mermaid)"
    tail = js.read_text(encoding="utf-8", errors="replace")[-4000:]
    # Сборка выставляет глобаль — значит грузится обычным <script>, без ESM.
    assert 'globalThis["mermaid"]' in tail or "globalThis.mermaid" in tail


def test_index_loads_mermaid():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "vendor/mermaid/mermaid.min.js" in html


def test_redesign_renders_mermaid_blocks():
    js = (STATIC / "redesign.js").read_text(encoding="utf-8")
    # Блоки ```mermaid``` рисуются как диаграмма, а не как код.
    assert 'lang === "mermaid"' in js
    assert "renderMermaidBlock" in js
    assert "mermaid.render" in js


def test_math_js_is_self_contained():
    # math.js не должен зависеть от внешней escapeHTML (её нет в redesign.js).
    src = (STATIC / "math.js").read_text(encoding="utf-8")
    assert "mathEscHtml" in src
    assert "escapeHTML(" not in src, "math.js не должен звать несуществующую escapeHTML"


def test_widget_width_breakout_css():
    css = (STATIC / "redesign.layout.css").read_text(encoding="utf-8")
    # Виджеты меряются от ленты (container query) и выходят за колонку чтения.
    assert "container-type: inline-size" in css
    assert "cqw" in css
    # Простые SVG-карточки остаются в узкой колонке.
    assert ".widget-fig:has(.widget-frame.graphic)" in css


def test_canvas_graphic_tools_exist():
    from core.tools import build_default_registry

    names = {t.name for t in build_default_registry().all()}
    for tool in ("show_graphic", "show_interactive", "show_ui"):
        assert tool in names, f"инструмент графики {tool} должен быть зарегистрирован"
