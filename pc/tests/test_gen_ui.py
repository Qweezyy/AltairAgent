"""Строгая библиотека компонентов генеративного UI (core/gen_ui)."""

from __future__ import annotations

import pytest

from core.gen_ui import BLOCK_TYPES, MAX_BLOCKS, UISpecError, render_ui


def test_renders_all_component_types_without_error():
    spec = [
        {"type": "heading", "text": "Заголовок", "level": 2},
        {"type": "text", "text": "**жирный** и `код`"},
        {"type": "badge", "text": "новое", "variant": "success"},
        {"type": "callout", "text": "важно", "title": "Note", "variant": "warn"},
        {"type": "divider"},
        {"type": "stats", "items": [{"label": "A", "value": 1, "delta": "2", "trend": "up"}]},
        {"type": "keyvalue", "items": [{"key": "k", "value": "v"}]},
        {"type": "list", "items": ["a", "b"], "ordered": True},
        {"type": "table", "columns": ["c1"], "rows": [["r1"]]},
        {"type": "progress", "value": 30, "max": 60, "label": "ход"},
        {"type": "image", "src": "/files/a.png", "caption": "cap"},
        {"type": "video", "src": "/files/a.mp4"},
        {"type": "audio", "src": "/files/a.mp3"},
        {"type": "chart", "kind": "bar", "data": [{"label": "x", "value": 5}]},
        {"type": "chart", "kind": "line", "data": [{"label": "x", "value": 5}, {"label": "y", "value": 8}]},
        {"type": "chart", "kind": "pie", "data": [{"label": "x", "value": 5}, {"label": "y", "value": 5}]},
        {"type": "buttons", "items": [{"label": "go", "prompt": "давай"}]},
        {"type": "form", "fields": [{"name": "q", "kind": "text"}], "submit_prompt": "ищи"},
        {"type": "card", "title": "T", "children": [{"type": "text", "text": "внутри"}]},
        {"type": "columns", "children": [[{"type": "text", "text": "1"}], [{"type": "text", "text": "2"}]]},
        {"type": "tabs", "items": [{"label": "t", "children": [{"type": "text", "text": "in"}]}]},
        {"type": "accordion", "items": [{"title": "a", "children": [{"type": "text", "text": "in"}]}]},
    ]
    html = render_ui(spec, title="Всё сразу")
    assert html.startswith("<!doctype html>")
    assert "window.sendPrompt" in html  # мост интерактива на месте
    assert "data-ui-prompt=\"давай\"" in html
    assert "Всё сразу" in html


# Минимальный валидный пример каждого типа — гарантирует, что любой компонент
# из BLOCK_TYPES рендерится без ошибок.
_MINIMAL = {
    "heading": {"text": "h"}, "text": {"text": "t"}, "divider": {}, "badge": {"text": "b"},
    "callout": {"text": "c"}, "stat": {"label": "l", "value": 1}, "stats": {"items": [{"label": "l", "value": 1}]},
    "keyvalue": {"items": [{"key": "k", "value": "v"}]}, "list": {"items": ["a"]},
    "table": {"rows": [["r"]]}, "progress": {"value": 50}, "image": {"src": "/files/a.png"},
    "video": {"src": "/files/a.mp4"}, "audio": {"src": "/files/a.mp3"},
    "button": {"label": "b"}, "buttons": {"items": [{"label": "b"}]},
    "field": {"name": "f"}, "form": {"fields": [{"name": "f"}]},
    "chart": {"kind": "bar", "data": [{"label": "x", "value": 1}]},
    "card": {"children": [{"type": "text", "text": "x"}]},
    "columns": {"children": [[{"type": "text", "text": "1"}]]},
    "tabs": {"items": [{"label": "t", "children": [{"type": "text", "text": "x"}]}]},
    "accordion": {"items": [{"title": "a", "children": [{"type": "text", "text": "x"}]}]},
    "hero": {"title": "H"}, "steps": {"items": [{"title": "s", "status": "done"}]},
    "code": {"code": "x=1", "language": "py"}, "quote": {"text": "q", "author": "a"},
    "gauge": {"value": 70, "label": "g"}, "avatar": {"name": "Ivan Petrov"},
    "gallery": {"images": [{"src": "/files/a.png"}]}, "rating": {"value": 4},
    "chips": {"items": ["a", {"text": "b", "variant": "info"}]}, "spacer": {"size": "lg"},
    "icon": {"name": "check"},
}


def test_every_block_type_has_a_minimal_render():
    missing = set(BLOCK_TYPES) - set(_MINIMAL)
    assert not missing, f"нет минимального примера для: {missing}"
    for kind, extra in _MINIMAL.items():
        html = render_ui([{"type": kind, **extra}])
        assert html.startswith("<!doctype html>"), kind
        assert "<body>" in html, kind


def test_new_component_options_render():
    html = render_ui([
        {"type": "chart", "kind": "donut", "data": [{"label": "a", "value": 3}], "title": "d"},
        {"type": "chart", "kind": "hbar", "data": [{"label": "a", "value": 3}]},
        {"type": "button", "label": "x", "size": "sm", "outline": True, "icon": "star"},
        {"type": "badge", "text": "s", "solid": True, "variant": "success"},
        {"type": "card", "variant": "tinted", "accent": "info", "children": [{"type": "text", "text": "y"}]},
    ])
    assert "ui-chart-donut-c" in html and "ui-hbar" in html
    assert "ui-outline" in html and "ui-sm" in html
    assert "ui-solid" in html and "ui-card-tinted" in html


def test_multi_series_bar_and_line_render_with_legend():
    html = render_ui([
        {"type": "chart", "kind": "bar", "labels": ["Q1", "Q2"], "series": [
            {"name": "2025", "values": [1, 2]},
            {"name": "2026", "values": [3, 4], "color": "#22c55e"}]},
        {"type": "chart", "kind": "line", "area": True, "labels": ["A", "B", "C"], "series": [
            {"name": "x", "values": [1, 2, 3]},
            {"name": "y", "values": [3, 2, 1]}]},
    ])
    # по одной строке-легенде на график и обе серии названы
    assert html.count('class="ui-legend ui-legend-row"') == 2
    assert "2025" in html and "2026" in html and 'fill="#22c55e"' in html
    assert '<polyline' in html  # линии нарисованы


def test_series_with_non_numeric_value_is_rejected():
    with pytest.raises(UISpecError):
        render_ui([{"type": "chart", "kind": "bar", "series": [{"name": "a", "values": ["oops"]}]}])


def test_stat_sparkline_renders_and_defaults_colour_by_trend():
    html = render_ui([{"type": "stats", "items": [
        {"label": "Up", "value": 1, "trend": "up", "sparkline": [1, 2, 3, 2, 4]},
        {"label": "Down", "value": 2, "trend": "down", "sparkline": [4, 3, 3, 2, 1]},
        {"label": "Flat", "value": 3, "sparkline": [2, 2, 3, 2]},
    ]}])
    assert html.count('<svg class="ui-spark"') == 3
    assert "var(--success)" in html and "var(--danger)" in html and "var(--accent)" in html


def test_sparkline_needs_two_points():
    # один пункт — спарклайн просто опускается, без ошибки
    html = render_ui([{"type": "stat", "label": "x", "value": 1, "sparkline": [5]}])
    assert '<svg class="ui-spark"' not in html


def test_unknown_icon_name_is_omitted_in_field_but_errors_as_block():
    # icon-поле с неизвестным именем просто опускается (не ломает верстку)
    html = render_ui([{"type": "badge", "text": "b", "icon": "no-such-icon"}])
    assert "ui-badge" in html
    # отдельный блок icon с неизвестным именем — явная ошибка
    with pytest.raises(UISpecError):
        render_ui([{"type": "icon", "name": "no-such-icon"}])


def test_inline_formatting_is_escaped_and_converted():
    html = render_ui([{"type": "text", "text": "<script>alert(1)</script> **b** [x](https://y)"}])
    assert "<script>alert(1)" not in html  # пользовательский тег не исполнится
    assert "&lt;script&gt;alert(1)" in html
    assert "<strong>b</strong>" in html
    assert '<a href="https://y"' in html


def test_unknown_type_is_rejected():
    with pytest.raises(UISpecError) as e:
        render_ui([{"type": "frobnicate"}])
    assert "frobnicate" in str(e.value)


def test_missing_required_field_is_rejected():
    with pytest.raises(UISpecError):
        render_ui([{"type": "table"}])  # нет rows
    with pytest.raises(UISpecError):
        render_ui([{"type": "chart", "kind": "bar", "data": []}])  # пустые данные


def test_button_without_prompt_falls_back_to_label():
    html = render_ui([{"type": "button", "label": "Жми"}])
    assert 'data-ui-prompt="Жми"' in html


def test_link_button_uses_anchor_not_prompt():
    html = render_ui([{"type": "button", "label": "Сайт", "url": "https://z"}])
    assert '<a class="ui-btn' in html and 'href="https://z"' in html


def test_block_limit_is_enforced():
    with pytest.raises(UISpecError):
        render_ui([{"type": "divider"} for _ in range(MAX_BLOCKS + 1)])
