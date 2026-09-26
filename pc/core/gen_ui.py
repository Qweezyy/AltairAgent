"""Строгая библиотека компонентов для генеративного UI.

Модель не пишет произвольный HTML, а собирает интерфейс из фиксированного набора
типизированных блоков (карточки, метрики, таблицы, формы, графики, вкладки…). Здесь
они детерминированно и единообразно рендерятся в самодостаточный HTML-документ с
темой, адаптирующейся под светлый/тёмный режим. Интерактив (кнопки, формы, вкладки,
аккордеон) завязан на window.sendPrompt(...) — мост, который фронтенд внедряет в
песочницу-iframe. Так виджет может отвечать агенту.

Результат отдаётся через событие ShowHtml(kind="interactive"), поэтому наследует всё:
песочницу, авто-высоту, sendPrompt, встраивание /files/<путь> и сохранение в историю.
"""

from __future__ import annotations

import html
import math
import re
from typing import Any

#: Предел на размер спецификации — защита от «простыни» и зацикленной вложенности.
MAX_BLOCKS = 300
MAX_DEPTH = 6

#: Полный список типов — используется и в проверке, и в подсказке модели.
BLOCK_TYPES = (
    "heading", "text", "divider", "badge", "callout", "stat", "stats",
    "keyvalue", "list", "table", "progress", "image", "video", "audio",
    "button", "buttons", "field", "form", "chart", "card", "columns",
    "tabs", "accordion",
    # расширение — больше выбора для модели
    "hero", "steps", "code", "quote", "gauge", "avatar", "gallery",
    "rating", "chips", "spacer", "icon",
)

_VARIANTS = {"default", "info", "success", "warn", "danger", "accent"}
_CHART_COLORS = ["#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#a855f7", "#14b8a6", "#ec4899", "#64748b"]

#: Инлайн-иконки (24×24, stroke=currentColor) — доступны по имени в icon-поле
#: многих компонентов и в блоке icon. Неизвестное имя молча опускается.
_ICONS = {
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "x": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    "alert": '<path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "star": '<polygon points="12 2 15.1 8.6 22 9.3 17 14 18.2 21 12 17.5 5.8 21 7 14 2 9.3 8.9 8.6 12 2"/>',
    "bolt": '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    "user": '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    "users": '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "folder": '<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9L9.6 3.9A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2z"/>',
    "file": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>',
    "chart": '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
    "trend-up": '<polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/>',
    "trend-down": '<polyline points="23 18 13.5 8.5 8.5 13.5 1 6"/><polyline points="17 18 23 18 23 12"/>',
    "arrow-right": '<line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>',
    "search": '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    "heart": '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.6 1-1a5.5 5.5 0 0 0 0-7.8z"/>',
    "mail": '<rect x="2" y="4" width="20" height="16" rx="2"/><polyline points="22 6 12 13 2 6"/>',
    "calendar": '<rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
    "code": '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
    "play": '<polygon points="5 3 19 12 5 21 5 3"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    "globe": '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    "sparkles": '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    "rocket": '<path d="M4.5 16.5c-1.5 1.3-2 5-2 5s3.7-.5 5-2c.7-.8.7-2 0-2.8a2 2 0 0 0-3 0z"/><path d="M12 15l-3-3a22 22 0 0 1 10-10c1 4-1 8-4 11z"/><path d="M9 12H4s.5-2.8 2-4c1.7-1.3 5-1 5-1"/>',
}


class UISpecError(ValueError):
    """Некорректная спецификация UI — сообщение адресовано модели."""


def _e(v: Any) -> str:
    """Экранирование текста для HTML."""
    return html.escape("" if v is None else str(v), quote=True)


def _inline(text: Any) -> str:
    """Минимальная инлайн-разметка: **жирный**, *курсив*, `код`, [текст](url)."""
    s = _e(text)
    s = re.sub(r"\[([^\]]+)\]\((https?:[^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s.replace("\n", "<br>")


def _variant(v: Any) -> str:
    v = str(v or "default").strip().lower()
    return v if v in _VARIANTS else "default"


def _icon(name: Any, cls: str = "ui-ic") -> str:
    """Инлайн-SVG иконка по имени; неизвестное имя → пусто (не ошибка)."""
    inner = _ICONS.get(str(name or "").strip().lower())
    if not inner:
        return ""
    return (
        f'<svg class="{cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{inner}</svg>'
    )


def _align(v: Any) -> str:
    a = str(v or "").strip().lower()
    return f"text-align:{a}" if a in ("left", "center", "right") else ""


def _require(block: dict, field: str, kind: str) -> Any:
    if field not in block or block[field] in (None, ""):
        raise UISpecError(f"блок «{kind}»: обязательное поле «{field}» отсутствует")
    return block[field]


def _as_list(v: Any, kind: str, field: str) -> list:
    if not isinstance(v, list):
        raise UISpecError(f"блок «{kind}»: поле «{field}» должно быть списком")
    return v


# ---------------------------------------------------------------- рендер блоков


def _render_blocks(blocks: Any, depth: int, counter: list[int]) -> str:
    if blocks is None:
        return ""
    if isinstance(blocks, dict):
        blocks = [blocks]
    if not isinstance(blocks, list):
        raise UISpecError("blocks должен быть списком блоков {type: …}")
    if depth > MAX_DEPTH:
        raise UISpecError(f"слишком глубокая вложенность (> {MAX_DEPTH})")
    out = []
    for block in blocks:
        counter[0] += 1
        if counter[0] > MAX_BLOCKS:
            raise UISpecError(f"слишком много блоков (> {MAX_BLOCKS})")
        if not isinstance(block, dict):
            raise UISpecError("каждый блок — объект вида {type: …}")
        kind = str(block.get("type") or "").strip().lower()
        fn = _RENDERERS.get(kind)
        if fn is None:
            raise UISpecError(
                f"неизвестный тип блока «{kind or '—'}». Допустимо: {', '.join(BLOCK_TYPES)}"
            )
        out.append(fn(block, depth, counter))
    return "\n".join(out)


def _r_heading(b, depth, c):
    lvl = int(b.get("level") or 2)
    lvl = min(max(lvl, 1), 4)
    style = _align(b.get("align"))
    ic = _icon(b.get("icon"), "ui-ic ui-h-ic")
    cls = "ui-h ui-h-ico" if ic else "ui-h"
    attr = f' style="{style}"' if style else ""
    return f'<h{lvl} class="{cls}"{attr}>{ic}{_inline(_require(b, "text", "heading"))}</h{lvl}>'


def _r_text(b, depth, c):
    cls = "ui-text ui-muted" if b.get("muted") else "ui-text"
    style = _align(b.get("align"))
    attr = f' style="{style}"' if style else ""
    return f'<p class="{cls}"{attr}>{_inline(_require(b, "text", "text"))}</p>'


def _r_divider(b, depth, c):
    if b.get("text"):
        return f'<div class="ui-hr-text"><span>{_inline(b["text"])}</span></div>'
    return '<hr class="ui-hr">'


def _r_badge(b, depth, c):
    solid = " ui-solid" if b.get("solid") else ""
    ic = _icon(b.get("icon"), "ui-ic ui-badge-ic")
    return f'<span class="ui-badge ui-v-{_variant(b.get("variant"))}{solid}">{ic}{_inline(_require(b, "text", "badge"))}</span>'


def _r_callout(b, depth, c):
    v = _variant(b.get("variant"))
    ic = _icon(b.get("icon"), "ui-ic ui-callout-ic")
    title = f'<div class="ui-callout-title">{_inline(b["title"])}</div>' if b.get("title") else ""
    inner = (
        f'{title}<div class="ui-callout-body">{_inline(_require(b, "text", "callout"))}</div>'
    )
    body = f'<div class="ui-callout-main">{inner}</div>' if ic else inner
    return f'<div class="ui-callout ui-v-{v}{" ui-has-ic" if ic else ""}">{ic}{body}</div>'


def _stat_card(item: dict) -> str:
    v = _variant(item.get("variant"))
    label = _e(item.get("label", ""))
    value = _inline(item.get("value", ""))
    ic = _icon(item.get("icon"), "ui-ic")
    icon_html = f'<div class="ui-stat-ic ui-v-{v}">{ic}</div>' if ic else ""
    delta = item.get("delta")
    trend = str(item.get("trend") or "").lower()
    dv = ""
    if delta not in (None, ""):
        tcls = "up" if trend == "up" else "down" if trend == "down" else "flat"
        arrow = "▲" if tcls == "up" else "▼" if tcls == "down" else "→"
        dv = f'<div class="ui-stat-delta {tcls}">{arrow} {_e(delta)}</div>'
    spark = ""
    sp = item.get("sparkline")
    if isinstance(sp, list) and sp:
        col = item.get("spark_color")
        if not col:
            col = "var(--success)" if trend == "up" else "var(--danger)" if trend == "down" else "var(--accent)"
        spark = _sparkline(sp, str(col))
    body = f'<div class="ui-stat-label">{label}</div><div class="ui-stat-value">{value}</div>{dv}'
    main = f'<div class="ui-stat-row">{icon_html}<div>{body}</div></div>' if icon_html else body
    return f'<div class="ui-stat">{main}{spark}</div>'


def _r_stat(b, depth, c):
    return f'<div class="ui-stats">{_stat_card(b)}</div>'


def _r_stats(b, depth, c):
    items = _as_list(_require(b, "items", "stats"), "stats", "items")
    return f'<div class="ui-stats">{"".join(_stat_card(i if isinstance(i, dict) else {}) for i in items)}</div>'


def _r_keyvalue(b, depth, c):
    items = _as_list(_require(b, "items", "keyvalue"), "keyvalue", "items")
    rows = "".join(
        f'<div class="ui-kv-row"><div class="ui-kv-k">{_inline(i.get("key",""))}</div>'
        f'<div class="ui-kv-v">{_inline(i.get("value",""))}</div></div>'
        for i in items if isinstance(i, dict)
    )
    return f'<div class="ui-kv">{rows}</div>'


def _r_list(b, depth, c):
    items = _as_list(_require(b, "items", "list"), "list", "items")
    tag = "ol" if b.get("ordered") else "ul"
    lis = "".join(f"<li>{_inline(i)}</li>" for i in items)
    return f'<{tag} class="ui-list">{lis}</{tag}>'


def _r_table(b, depth, c):
    cols = _as_list(b.get("columns", []), "table", "columns")
    rows = _as_list(_require(b, "rows", "table"), "table", "rows")
    head = "".join(f"<th>{_inline(col)}</th>" for col in cols)
    body = ""
    for row in rows:
        cells = row if isinstance(row, list) else [row]
        body += "<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in cells) + "</tr>"
    head_html = f"<thead><tr>{head}</tr></thead>" if cols else ""
    dense = " ui-dense" if b.get("dense") else ""
    return f'<div class="ui-table-wrap"><table class="ui-table{dense}">{head_html}<tbody>{body}</tbody></table></div>'


def _r_progress(b, depth, c):
    value = float(_require(b, "value", "progress"))
    mx = float(b.get("max") or 100)
    pct = 0 if mx <= 0 else max(0.0, min(100.0, value / mx * 100))
    label = f'<div class="ui-progress-label">{_inline(b["label"])} <span>{pct:.0f}%</span></div>' if b.get("label") else ""
    return f'{label}<div class="ui-progress"><div class="ui-progress-bar" style="width:{pct:.1f}%"></div></div>'


def _media_src(src: str) -> str:
    # /files/<путь> фронтенд достроит до абсолютного; data: и http(s) — как есть.
    return _e(src)


def _r_image(b, depth, c):
    src = _media_src(_require(b, "src", "image"))
    cap = f'<figcaption>{_inline(b["caption"])}</figcaption>' if b.get("caption") else ""
    return f'<figure class="ui-fig"><img src="{src}" alt="{_e(b.get("alt",""))}" loading="lazy">{cap}</figure>'


def _r_video(b, depth, c):
    src = _media_src(_require(b, "src", "video"))
    cap = f'<figcaption>{_inline(b["caption"])}</figcaption>' if b.get("caption") else ""
    return f'<figure class="ui-fig"><video src="{src}" controls playsinline preload="metadata"></video>{cap}</figure>'


def _r_audio(b, depth, c):
    src = _media_src(_require(b, "src", "audio"))
    return f'<audio class="ui-audio" src="{src}" controls preload="metadata"></audio>'


def _button_html(item: dict) -> str:
    label = _e(_require(item, "label", "button"))
    v = _variant(item.get("variant"))
    size = str(item.get("size") or "").lower()
    scls = " ui-sm" if size in ("sm", "small") else " ui-lg" if size in ("lg", "large") else ""
    ocls = " ui-outline" if item.get("outline") or item.get("ghost") else ""
    ic = _icon(item.get("icon"), "ui-ic")
    cls = f"ui-btn ui-v-{v}{scls}{ocls}"
    url = item.get("url")
    if url:
        return f'<a class="{cls}" href="{_e(url)}" target="_blank" rel="noopener">{ic}{label}</a>'
    prompt = item.get("prompt") or item.get("label")
    return f'<button type="button" class="{cls}" data-ui-prompt="{_e(prompt)}">{ic}{label}</button>'


def _r_button(b, depth, c):
    return f'<div class="ui-btns">{_button_html(b)}</div>'


def _r_buttons(b, depth, c):
    items = _as_list(_require(b, "items", "buttons"), "buttons", "items")
    return f'<div class="ui-btns">{"".join(_button_html(i) for i in items if isinstance(i, dict))}</div>'


def _field_html(f: dict) -> str:
    name = _e(_require(f, "name", "field"))
    label = _e(f.get("label") or f.get("name"))
    kind = str(f.get("kind") or "text").lower()
    ph = _e(f.get("placeholder", ""))
    control: str
    if kind == "textarea":
        control = f'<textarea class="ui-input" name="{name}" placeholder="{ph}" rows="3"></textarea>'
    elif kind == "select":
        opts = "".join(f"<option>{_e(o)}</option>" for o in (f.get("options") or []))
        control = f'<select class="ui-input" name="{name}">{opts}</select>'
    elif kind == "number":
        control = f'<input class="ui-input" type="number" name="{name}" placeholder="{ph}">'
    else:
        control = f'<input class="ui-input" type="text" name="{name}" placeholder="{ph}">'
    return f'<label class="ui-field"><span class="ui-field-label">{label}</span>{control}</label>'


def _r_field(b, depth, c):
    return f'<div class="ui-form">{_field_html(b)}</div>'


def _r_form(b, depth, c):
    fields = _as_list(_require(b, "fields", "form"), "form", "fields")
    inner = "".join(_field_html(f) for f in fields if isinstance(f, dict))
    submit_label = _e(b.get("submit_label") or "Отправить")
    submit_prompt = _e(b.get("submit_prompt") or "")
    return (
        f'<form class="ui-form" data-ui-form data-ui-prompt="{submit_prompt}">{inner}'
        f'<div class="ui-btns"><button type="submit" class="ui-btn ui-v-accent">{submit_label}</button></div></form>'
    )


def _parse_series(raw) -> list[tuple[str, str, list[float]]]:
    """[{name, values:[…], color?}] → [(name, color, [floats])]."""
    out = []
    for i, s in enumerate(raw):
        if not isinstance(s, dict):
            continue
        vals = []
        for v in (s.get("values") or s.get("data") or []):
            if isinstance(v, dict):
                v = v.get("value")
            try:
                vals.append(float(v))
            except (TypeError, ValueError) as exc:
                raise UISpecError("блок «chart»: values серии должны быть числами") from exc
        col = str(s.get("color") or _CHART_COLORS[i % len(_CHART_COLORS)])
        out.append((str(s.get("name", "")), col, vals))
    if not out:
        raise UISpecError("блок «chart»: пустой series")
    return out


def _series_legend(norm) -> str:
    items = "".join(
        f'<div class="ui-legend-item"><span class="ui-legend-dot" style="background:{_e(col)}"></span>{_e(name)}</div>'
        for name, col, _ in norm if name
    )
    return f'<div class="ui-legend ui-legend-row">{items}</div>' if items else ""


def _r_chart(b, depth, c):
    kind = str(b.get("kind") or "bar").lower()
    title = f'<div class="ui-chart-title">{_inline(b["title"])}</div>' if b.get("title") else ""

    # Многосерийный режим: несколько наборов на одной оси (сгруппированные
    # столбцы / несколько линий) + подписи оси в labels.
    series = b.get("series")
    if isinstance(series, list) and series:
        norm = _parse_series(series)
        labels = [str(x) for x in (b.get("labels") or [])]
        if not labels:
            maxlen = max((len(vals) for _, _, vals in norm), default=0)
            labels = [str(i + 1) for i in range(maxlen)]
        if kind == "line":
            svg = _chart_line_multi(labels, norm, area=bool(b.get("area")))
        else:
            svg = _chart_bar_multi(labels, norm)
        return f'<div class="ui-chart">{title}{_series_legend(norm)}{svg}</div>'

    data = _as_list(_require(b, "data", "chart"), "chart", "data")
    points = []
    for d in data:
        if not isinstance(d, dict):
            continue
        try:
            points.append((str(d.get("label", "")), float(d.get("value", 0))))
        except (TypeError, ValueError) as exc:
            raise UISpecError("блок «chart»: value должен быть числом") from exc
    if not points:
        raise UISpecError("блок «chart»: пустые данные")
    if kind in ("pie", "donut"):
        svg = _chart_pie(points, donut=(kind == "donut" or bool(b.get("donut"))))
    elif kind == "line":
        svg = _chart_line(points, area=bool(b.get("area")))
    elif kind in ("hbar", "hbars") or (kind == "bar" and b.get("horizontal")):
        svg = _chart_hbar(points)
    else:
        svg = _chart_bar(points)
    return f'<div class="ui-chart">{title}{svg}</div>'


def _chart_bar_multi(labels, norm):
    """Сгруппированные столбцы: по столбцу на серию в каждой группе-подписи."""
    w, h, pad = 480, 216, 30
    ng = max(len(labels), 1)
    ns = max(len(norm), 1)
    mx = max((v for _, _, vals in norm for v in vals), default=0) or 1
    gap = (w - pad * 2) / ng
    inner = gap * 0.78
    bw = inner / ns
    bars, labs = [], []
    for gi in range(ng):
        gx = pad + gap * gi + (gap - inner) / 2
        for si, (name, col, vals) in enumerate(norm):
            v = vals[gi] if gi < len(vals) else 0
            bh = (h - pad * 2) * (max(v, 0) / mx)
            x = gx + bw * si
            y = h - pad - bh
            bars.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(bw-1.5,1):.1f}" height="{bh:.1f}" rx="2.5" fill="{_e(col)}">'
                f'<title>{_e(name)} · {_e(labels[gi] if gi < len(labels) else "")}: {_e(_num(v))}</title></rect>'
            )
        labs.append(f'<text x="{gx+inner/2:.1f}" y="{h-pad+14:.1f}" class="ui-chart-lab" text-anchor="middle">{_e(labels[gi] if gi < len(labels) else "")}</text>')
    axis = f'<line x1="{pad}" y1="{h-pad}" x2="{w-pad}" y2="{h-pad}" class="ui-chart-axis"/>'
    return f'<svg viewBox="0 0 {w} {h}" class="ui-chart-svg" preserveAspectRatio="xMidYMid meet">{axis}{"".join(bars)}{"".join(labs)}</svg>'


def _chart_line_multi(labels, norm, *, area=False):
    w, h, pad = 480, 210, 30
    nl = max(len(labels), 1)
    mx = max((v for _, _, vals in norm for v in vals), default=0) or 1
    step = (w - pad * 2) / max(nl - 1, 1)
    parts = []
    for _name, col, vals in norm:
        coords = []
        for i in range(min(nl, len(vals))):
            x = pad + step * i
            y = h - pad - (h - pad * 2) * (max(vals[i], 0) / mx)
            coords.append((x, y))
        if not coords:
            continue
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        if area:
            pts = f"{coords[0][0]:.1f},{h-pad} {poly} {coords[-1][0]:.1f},{h-pad}"
            parts.append(f'<polygon points="{pts}" fill="{_e(col)}" opacity="0.10"/>')
        parts.append(f'<polyline points="{poly}" fill="none" stroke="{_e(col)}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>')
        parts += [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.8" fill="{_e(col)}"/>' for x, y in coords]
    labs = "".join(
        f'<text x="{pad+step*i:.1f}" y="{h-pad+14:.1f}" class="ui-chart-lab" text-anchor="middle">{_e(lab)}</text>'
        for i, lab in enumerate(labels)
    )
    axis = f'<line x1="{pad}" y1="{h-pad}" x2="{w-pad}" y2="{h-pad}" class="ui-chart-axis"/>'
    return f'<svg viewBox="0 0 {w} {h}" class="ui-chart-svg" preserveAspectRatio="xMidYMid meet">{axis}{"".join(parts)}{labs}</svg>'


def _sparkline(values, color: str) -> str:
    """Мини-график-линия без осей — для комбо-метрики (stat.sparkline)."""
    vals = []
    for v in values:
        if isinstance(v, dict):
            v = v.get("value")
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            continue
    if len(vals) < 2:
        return ""
    w, h, pad = 108, 30, 3
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    n = len(vals)
    step = (w - pad * 2) / (n - 1)
    coords = [(pad + step * i, h - pad - (h - pad * 2) * ((v - lo) / rng)) for i, v in enumerate(vals)]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area_pts = f"{coords[0][0]:.1f},{h} {poly} {coords[-1][0]:.1f},{h}"
    lx, ly = coords[-1]
    return (
        f'<svg class="ui-spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">'
        f'<polygon points="{area_pts}" fill="{color}" opacity="0.14"/>'
        f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.4" fill="{color}"/></svg>'
    )


def _chart_bar(points):
    w, h, pad = 480, 200, 28
    mx = max(v for _, v in points) or 1
    n = len(points)
    bw = (w - pad * 2) / n * 0.62
    gap = (w - pad * 2) / n
    bars, labels = [], []
    for i, (lab, v) in enumerate(points):
        bh = (h - pad * 2) * (v / mx)
        x = pad + gap * i + (gap - bw) / 2
        y = h - pad - bh
        col = _CHART_COLORS[i % len(_CHART_COLORS)]
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="3" fill="{col}"><title>{_e(lab)}: {_e(v)}</title></rect>')
        bars.append(f'<text x="{x+bw/2:.1f}" y="{y-4:.1f}" class="ui-chart-val" text-anchor="middle">{_e(_num(v))}</text>')
        labels.append(f'<text x="{x+bw/2:.1f}" y="{h-pad+14:.1f}" class="ui-chart-lab" text-anchor="middle">{_e(lab)}</text>')
    axis = f'<line x1="{pad}" y1="{h-pad}" x2="{w-pad}" y2="{h-pad}" class="ui-chart-axis"/>'
    return f'<svg viewBox="0 0 {w} {h}" class="ui-chart-svg" preserveAspectRatio="xMidYMid meet">{axis}{"".join(bars)}{"".join(labels)}</svg>'


def _chart_hbar(points):
    """Горизонтальные полосы с подписью и значением в строке — удобно для рейтингов."""
    mx = max(v for _, v in points) or 1
    rows = []
    for i, (lab, v) in enumerate(points):
        pct = v / mx * 100
        col = _CHART_COLORS[i % len(_CHART_COLORS)]
        rows.append(
            f'<div class="ui-hbar-row"><div class="ui-hbar-lab">{_e(lab)}</div>'
            f'<div class="ui-hbar-track"><div class="ui-hbar-fill" style="width:{pct:.1f}%;background:{col}"></div></div>'
            f'<div class="ui-hbar-val">{_e(_num(v))}</div></div>'
        )
    return f'<div class="ui-hbar">{"".join(rows)}</div>'


def _chart_line(points, *, area=False):
    w, h, pad = 480, 200, 28
    mx = max(v for _, v in points) or 1
    n = len(points)
    step = (w - pad * 2) / max(n - 1, 1)
    coords = []
    for i, (_, v) in enumerate(points):
        x = pad + step * i
        y = h - pad - (h - pad * 2) * (v / mx)
        coords.append((x, y))
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    col = _CHART_COLORS[0]
    fill = ""
    if area and coords:
        pts = f"{coords[0][0]:.1f},{h-pad} " + poly + f" {coords[-1][0]:.1f},{h-pad}"
        fill = f'<polygon points="{pts}" fill="{col}" opacity="0.12"/>'
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="{col}"/>' for x, y in coords)
    labs = "".join(
        f'<text x="{pad+step*i:.1f}" y="{h-pad+14:.1f}" class="ui-chart-lab" text-anchor="middle">{_e(lab)}</text>'
        for i, (lab, _) in enumerate(points)
    )
    axis = f'<line x1="{pad}" y1="{h-pad}" x2="{w-pad}" y2="{h-pad}" class="ui-chart-axis"/>'
    return (
        f'<svg viewBox="0 0 {w} {h}" class="ui-chart-svg" preserveAspectRatio="xMidYMid meet">{axis}{fill}'
        f'<polyline points="{poly}" fill="none" stroke="{col}" stroke-width="2.5" stroke-linejoin="round"/>{dots}{labs}</svg>'
    )


def _chart_pie(points, *, donut=False):
    size, r, cx, cy = 200, 82, 100, 100
    total = sum(v for _, v in points) or 1
    ang = -math.pi / 2
    arcs, legend = [], []
    for i, (lab, v) in enumerate(points):
        frac = v / total
        a2 = ang + frac * 2 * math.pi
        x1, y1 = cx + r * math.cos(ang), cy + r * math.sin(ang)
        x2, y2 = cx + r * math.cos(a2), cy + r * math.sin(a2)
        large = 1 if frac > 0.5 else 0
        col = _CHART_COLORS[i % len(_CHART_COLORS)]
        arcs.append(f'<path d="M{cx},{cy} L{x1:.1f},{y1:.1f} A{r},{r} 0 {large} 1 {x2:.1f},{y2:.1f} Z" fill="{col}"><title>{_e(lab)}: {_e(_num(v))} ({frac*100:.0f}%)</title></path>')
        legend.append(f'<div class="ui-legend-item"><span class="ui-legend-dot" style="background:{col}"></span>{_e(lab)} <b>{_e(_num(v))}</b></div>')
        ang = a2
    hole = f'<circle cx="{cx}" cy="{cy}" r="{r*0.58:.0f}" fill="var(--surface)"/>' if donut else ""
    center = f'<text x="{cx}" y="{cy+5}" text-anchor="middle" class="ui-chart-donut-c">{_e(_num(total))}</text>' if donut else ""
    return (
        f'<div class="ui-pie-wrap"><svg viewBox="0 0 {size} {size}" class="ui-chart-svg ui-pie">{"".join(arcs)}{hole}{center}</svg>'
        f'<div class="ui-legend">{"".join(legend)}</div></div>'
    )


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


_CARD_VARIANTS = {"plain", "outlined", "elevated", "tinted"}


def _r_card(b, depth, c):
    var = str(b.get("variant") or "plain").lower()
    var = var if var in _CARD_VARIANTS else "plain"
    accent = _variant(b.get("accent"))
    cls = f"ui-card ui-card-{var}"
    if var == "tinted":
        cls += f" ui-v-{accent}"
    ic = _icon(b.get("icon"), "ui-ic ui-card-ic")
    title = ""
    if b.get("title"):
        title = f'<div class="ui-card-head">{ic}<div class="ui-card-title">{_inline(b["title"])}</div></div>'
    inner = _render_blocks(b.get("children"), depth + 1, c)
    footer = f'<div class="ui-card-foot">{_inline(b["footer"])}</div>' if b.get("footer") else ""
    return f'<div class="{cls}">{title}<div class="ui-card-body">{inner}</div>{footer}</div>'


# --------------------------------------------------------- расширенные компоненты


def _r_hero(b, depth, c):
    v = _variant(b.get("variant"))
    ic = _icon(b.get("icon"), "ui-ic ui-hero-ic")
    sub = f'<p class="ui-hero-sub">{_inline(b["subtitle"])}</p>' if b.get("subtitle") else ""
    actions = b.get("actions")
    act = ""
    if isinstance(actions, list) and actions:
        act = f'<div class="ui-btns ui-hero-actions">{"".join(_button_html(a) for a in actions if isinstance(a, dict))}</div>'
    icwrap = f'<div class="ui-hero-icwrap ui-v-{v}">{ic}</div>' if ic else ""
    return (
        f'<div class="ui-hero ui-v-{v}">{icwrap}<div class="ui-hero-main">'
        f'<div class="ui-hero-title">{_inline(_require(b, "title", "hero"))}</div>{sub}{act}</div></div>'
    )


_STEP_STATUS = {"done": "done", "active": "active", "current": "active", "pending": "pending", "todo": "pending", "failed": "failed", "error": "failed"}


def _r_steps(b, depth, c):
    items = _as_list(_require(b, "items", "steps"), "steps", "items")
    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        st = _STEP_STATUS.get(str(it.get("status") or "pending").lower(), "pending")
        mark = {"done": _icon("check", "ui-ic"), "failed": _icon("x", "ui-ic")}.get(st, "")
        text = f'<div class="ui-step-text">{_inline(it["text"])}</div>' if it.get("text") else ""
        rows.append(
            f'<div class="ui-step ui-step-{st}"><div class="ui-step-marker">{mark}</div>'
            f'<div class="ui-step-body"><div class="ui-step-title">{_inline(it.get("title",""))}</div>{text}</div></div>'
        )
    return f'<div class="ui-steps">{"".join(rows)}</div>'


def _r_code(b, depth, c):
    code = _require(b, "code", "code")
    lang = b.get("language") or b.get("lang") or ""
    head = f'<div class="ui-code-head">{_e(lang)}</div>' if lang else ""
    return f'<div class="ui-code-wrap">{head}<pre class="ui-code"><code>{_e(code)}</code></pre></div>'


def _r_quote(b, depth, c):
    author = f'<footer class="ui-quote-author">{_inline(b["author"])}</footer>' if b.get("author") else ""
    return f'<blockquote class="ui-quote">{_inline(_require(b, "text", "quote"))}{author}</blockquote>'


def _r_gauge(b, depth, c):
    value = float(_require(b, "value", "gauge"))
    mx = float(b.get("max") or 100)
    pct = 0.0 if mx <= 0 else max(0.0, min(1.0, value / mx))
    v = _variant(b.get("variant"))
    r, cx, cy = 52, 66, 66
    circ = 2 * math.pi * r
    dash = circ * pct
    label = f'<div class="ui-gauge-label">{_inline(b["label"])}</div>' if b.get("label") else ""
    center = _e(b.get("center") or f"{pct*100:.0f}%")
    return (
        f'<div class="ui-gauge"><svg viewBox="0 0 132 132" class="ui-gauge-svg ui-v-{v}">'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" class="ui-gauge-bg"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" class="ui-gauge-fg" '
        f'stroke-dasharray="{dash:.1f} {circ:.1f}" transform="rotate(-90 {cx} {cy})"/>'
        f'<text x="{cx}" y="{cy+6}" text-anchor="middle" class="ui-gauge-val">{center}</text></svg>{label}</div>'
    )


def _r_avatar(b, depth, c):
    name = _require(b, "name", "avatar")
    sub = f'<div class="ui-avatar-sub">{_inline(b["subtitle"])}</div>' if b.get("subtitle") else ""
    src = b.get("src")
    if src:
        media = f'<img class="ui-avatar-img" src="{_media_src(src)}" alt="{_e(name)}" loading="lazy">'
    else:
        initials = _e(b.get("initials") or "".join(w[0] for w in str(name).split()[:2]).upper() or "?")
        media = f'<div class="ui-avatar-ini">{initials}</div>'
    return (
        f'<div class="ui-avatar">{media}<div class="ui-avatar-meta">'
        f'<div class="ui-avatar-name">{_inline(name)}</div>{sub}</div></div>'
    )


def _r_gallery(b, depth, c):
    images = _as_list(_require(b, "images", "gallery"), "gallery", "images")
    cells = []
    for im in images:
        if isinstance(im, str):
            im = {"src": im}
        if not isinstance(im, dict) or not im.get("src"):
            continue
        cap = f'<figcaption>{_inline(im["caption"])}</figcaption>' if im.get("caption") else ""
        cells.append(f'<figure class="ui-gal-cell"><img src="{_media_src(im["src"])}" loading="lazy" alt="{_e(im.get("alt",""))}">{cap}</figure>')
    return f'<div class="ui-gallery">{"".join(cells)}</div>'


def _r_rating(b, depth, c):
    value = float(_require(b, "value", "rating"))
    mx = int(b.get("max") or 5)
    stars = []
    for i in range(1, mx + 1):
        fill = "full" if value >= i else "half" if value >= i - 0.5 else "empty"
        stars.append(f'<span class="ui-star {fill}">{_icon("star", "ui-ic")}</span>')
    txt = f'<span class="ui-rating-val">{_e(_num(value))}</span>' if b.get("show_value") else ""
    return f'<div class="ui-rating">{"".join(stars)}{txt}</div>'


def _r_chips(b, depth, c):
    items = _as_list(_require(b, "items", "chips"), "chips", "items")
    chips = []
    for it in items:
        if isinstance(it, str):
            it = {"text": it}
        if not isinstance(it, dict):
            continue
        v = _variant(it.get("variant"))
        ic = _icon(it.get("icon"), "ui-ic ui-badge-ic")
        chips.append(f'<span class="ui-chip ui-v-{v}">{ic}{_inline(it.get("text",""))}</span>')
    return f'<div class="ui-chips">{"".join(chips)}</div>'


def _r_spacer(b, depth, c):
    size = str(b.get("size") or "md").lower()
    px = {"xs": 4, "sm": 8, "md": 16, "lg": 28, "xl": 44}.get(size, 16)
    try:
        px = int(b["size"]) if str(b.get("size")).isdigit() else px
    except (KeyError, ValueError, TypeError):
        pass
    return f'<div class="ui-spacer" style="height:{px}px"></div>'


def _r_icon(b, depth, c):
    name = _require(b, "name", "icon")
    v = _variant(b.get("variant"))
    size = str(b.get("size") or "md").lower()
    scls = {"sm": "ui-icon-sm", "lg": "ui-icon-lg", "xl": "ui-icon-xl"}.get(size, "")
    ic = _icon(name, "ui-ic")
    if not ic:
        raise UISpecError(f"блок «icon»: неизвестная иконка «{name}»")
    return f'<span class="ui-icon ui-v-{v} {scls}">{ic}</span>'


def _r_columns(b, depth, c):
    children = _as_list(_require(b, "children", "columns"), "columns", "children")
    cols = "".join(f'<div class="ui-col">{_render_blocks(ch, depth + 1, c)}</div>' for ch in children)
    return f'<div class="ui-columns">{cols}</div>'


def _r_tabs(b, depth, c):
    items = _as_list(_require(b, "items", "tabs"), "tabs", "items")
    heads, panes = [], []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        act = " active" if i == 0 else ""
        heads.append(f'<button type="button" class="ui-tab{act}" data-ui-tab="{i}">{_inline(it.get("label", f"Вкладка {i+1}"))}</button>')
        panes.append(f'<div class="ui-tabpane{act}" data-ui-pane="{i}">{_render_blocks(it.get("children"), depth + 1, c)}</div>')
    return f'<div class="ui-tabs"><div class="ui-tabbar">{"".join(heads)}</div>{"".join(panes)}</div>'


def _r_accordion(b, depth, c):
    items = _as_list(_require(b, "items", "accordion"), "accordion", "items")
    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        rows.append(
            f'<div class="ui-acc-item"><button type="button" class="ui-acc-head" data-ui-acc>'
            f'<span>{_inline(it.get("title", ""))}</span><span class="ui-acc-chev">▾</span></button>'
            f'<div class="ui-acc-body">{_render_blocks(it.get("children"), depth + 1, c)}</div></div>'
        )
    return f'<div class="ui-accordion">{"".join(rows)}</div>'


_RENDERERS = {
    "heading": _r_heading, "text": _r_text, "divider": _r_divider, "badge": _r_badge,
    "callout": _r_callout, "stat": _r_stat, "stats": _r_stats, "keyvalue": _r_keyvalue,
    "list": _r_list, "table": _r_table, "progress": _r_progress, "image": _r_image,
    "video": _r_video, "audio": _r_audio, "button": _r_button, "buttons": _r_buttons,
    "field": _r_field, "form": _r_form, "chart": _r_chart, "card": _r_card,
    "columns": _r_columns, "tabs": _r_tabs, "accordion": _r_accordion,
    "hero": _r_hero, "steps": _r_steps, "code": _r_code, "quote": _r_quote,
    "gauge": _r_gauge, "avatar": _r_avatar, "gallery": _r_gallery, "rating": _r_rating,
    "chips": _r_chips, "spacer": _r_spacer, "icon": _r_icon,
}


# ---------------------------------------------------------------- документ


def render_ui(blocks: Any, *, title: str = "") -> str:
    """Собирает спецификацию блоков в самодостаточный HTML-документ.

    Бросает UISpecError с понятным сообщением, если спецификация некорректна.
    """
    body = _render_blocks(blocks, 0, [0])
    head = f'<div class="ui-title">{_inline(title)}</div>' if title else ""
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<style>{_CSS}</style></head><body><div class=\"ui-root\">{head}{body}</div>"
        f"<script>{_JS}</script></body></html>"
    )


_CSS = """
/* Токены наследуются из приложения (--app-*), c автономными запасными значениями,
   если виджет открыт вне приложения. */
:root{
 --bg:var(--app-bg,#faf9f7);--surface:var(--app-surface,#ffffff);--surface2:var(--app-surface2,#f0eee9);
 --elevated:var(--app-elevated,#ffffff);--border:var(--app-border,#e7e3db);--border-strong:var(--app-border-strong,#d3cdc2);
 --text:var(--app-text,#22201d);--dim:var(--app-dim,#6b665e);--faint:var(--app-faint,#928c82);
 --accent:var(--app-accent,#c8623f);--accent-contrast:var(--app-accent-contrast,#fff);
 --success:var(--app-success,#3fa06a);--danger:var(--app-danger,#d05a4e);--info:#4a8fd6;--warn:#cf9a3a;
 --radius:var(--app-radius,12px);--radius-sm:var(--app-radius-sm,8px);
 --shadow:var(--app-shadow,0 8px 24px -10px rgba(60,50,40,.18));--shadow-sm:var(--app-shadow-sm,0 1px 2px rgba(60,50,40,.1));
 --gap:12px;
}
@media(prefers-color-scheme:dark){:root{
 --bg:var(--app-bg,#1b1a18);--surface:var(--app-surface,#232220);--surface2:var(--app-surface2,#2c2a27);
 --elevated:var(--app-elevated,#2a2825);--border:var(--app-border,#38352f);--border-strong:var(--app-border-strong,#4a463f);
 --text:var(--app-text,#ece8e1);--dim:var(--app-dim,#a6a097);--faint:var(--app-faint,#79746b);
 --accent:var(--app-accent,#d1734f);--success:var(--app-success,#5cbd85);--danger:var(--app-danger,#e0736a);
 --info:#69a6e0;--warn:#e0b25a;
 --shadow:var(--app-shadow,0 10px 30px -12px rgba(0,0,0,.6));--shadow-sm:var(--app-shadow-sm,0 1px 2px rgba(0,0,0,.4));
}}
*{box-sizing:border-box}
::selection{background:color-mix(in srgb,var(--accent) 30%,transparent)}
body{margin:0;background:var(--bg);color:var(--text);-webkit-font-smoothing:antialiased;
 font:14px/1.55 ui-sans-serif,system-ui,-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;}
.ui-root{padding:16px;display:flex;flex-direction:column;gap:var(--gap)}
.ui-root>*{min-width:0}
.ui-title{font-size:17px;font-weight:700;letter-spacing:-.01em}
.ui-h{margin:0;font-weight:660;line-height:1.25;letter-spacing:-.01em}
h1.ui-h{font-size:22px}h2.ui-h{font-size:18px}h3.ui-h{font-size:15px}
h4.ui-h{font-size:11.5px;color:var(--faint);text-transform:uppercase;letter-spacing:.07em;font-weight:700}
.ui-text{margin:0;color:var(--text)}.ui-muted{color:var(--dim)}
.ui-hr{border:none;height:1px;background:linear-gradient(90deg,transparent,var(--border),transparent);margin:2px 0}
code{background:var(--surface2);padding:1.5px 6px;border-radius:6px;font-size:.86em;
 font-family:ui-monospace,'Cascadia Code','SF Mono',Consolas,monospace}
strong{font-weight:660}a{color:var(--accent);text-decoration:none;font-weight:550}a:hover{text-decoration:underline}
/* badge */
.ui-badge{display:inline-flex;align-items:center;gap:5px;padding:3px 11px;border-radius:999px;font-size:12px;
 font-weight:600;line-height:1.4;background:var(--surface2);color:var(--dim);border:1px solid var(--border)}
.ui-badge::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;opacity:.85}
.ui-v-info.ui-badge{background:color-mix(in srgb,var(--info) 14%,transparent);color:var(--info);border-color:color-mix(in srgb,var(--info) 30%,transparent)}
.ui-v-success.ui-badge{background:color-mix(in srgb,var(--success) 14%,transparent);color:var(--success);border-color:color-mix(in srgb,var(--success) 30%,transparent)}
.ui-v-warn.ui-badge{background:color-mix(in srgb,var(--warn) 16%,transparent);color:var(--warn);border-color:color-mix(in srgb,var(--warn) 32%,transparent)}
.ui-v-danger.ui-badge{background:color-mix(in srgb,var(--danger) 14%,transparent);color:var(--danger);border-color:color-mix(in srgb,var(--danger) 30%,transparent)}
.ui-v-accent.ui-badge{background:color-mix(in srgb,var(--accent) 14%,transparent);color:var(--accent);border-color:color-mix(in srgb,var(--accent) 30%,transparent)}
/* callout */
.ui-callout{position:relative;border:1px solid var(--border);border-radius:var(--radius);padding:12px 14px 12px 16px;
 background:var(--surface);box-shadow:var(--shadow-sm);overflow:hidden}
.ui-callout::before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--dim)}
.ui-callout-title{font-weight:660;margin-bottom:3px}
.ui-callout-body{color:var(--dim)}
.ui-callout.ui-v-info::before{background:var(--info)}.ui-callout.ui-v-success::before{background:var(--success)}
.ui-callout.ui-v-warn::before{background:var(--warn)}.ui-callout.ui-v-danger::before{background:var(--danger)}
.ui-callout.ui-v-accent::before{background:var(--accent)}
.ui-callout.ui-v-info{background:color-mix(in srgb,var(--info) 6%,var(--surface))}
.ui-callout.ui-v-success{background:color-mix(in srgb,var(--success) 6%,var(--surface))}
.ui-callout.ui-v-warn{background:color-mix(in srgb,var(--warn) 7%,var(--surface))}
.ui-callout.ui-v-danger{background:color-mix(in srgb,var(--danger) 6%,var(--surface))}
/* stats */
.ui-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.ui-stat{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 15px;
 box-shadow:var(--shadow-sm);transition:transform .15s var(--ease,ease),box-shadow .15s}
.ui-stat:hover{transform:translateY(-1px);box-shadow:var(--shadow)}
.ui-stat-label{color:var(--faint);font-size:11.5px;font-weight:600;text-transform:uppercase;letter-spacing:.05em}
.ui-stat-value{font-size:26px;font-weight:730;margin-top:5px;letter-spacing:-.02em;line-height:1.1}
.ui-stat-delta{display:inline-flex;align-items:center;gap:3px;font-size:12px;font-weight:650;margin-top:7px;
 padding:2px 8px;border-radius:999px}
.ui-stat-delta.up{color:var(--success);background:color-mix(in srgb,var(--success) 13%,transparent)}
.ui-stat-delta.down{color:var(--danger);background:color-mix(in srgb,var(--danger) 13%,transparent)}
.ui-stat-delta.flat{color:var(--dim);background:var(--surface2)}
/* keyvalue */
.ui-kv{display:grid;gap:0;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
 padding:4px 14px;box-shadow:var(--shadow-sm)}
.ui-kv-row{display:grid;grid-template-columns:minmax(90px,34%) 1fr;gap:12px;padding:9px 0;border-bottom:1px solid var(--border)}
.ui-kv-row:last-child{border-bottom:none}
.ui-kv-k{color:var(--faint);font-weight:600;font-size:13px}.ui-kv-v{font-weight:500}
/* list */
.ui-list{margin:0;padding-left:4px;display:grid;gap:6px;list-style:none}
.ui-list li{position:relative;padding-left:22px}
.ui-list li::before{content:"";position:absolute;left:4px;top:9px;width:6px;height:6px;border-radius:50%;
 background:var(--accent);opacity:.7}
ol.ui-list{counter-reset:li}
ol.ui-list li::before{counter-increment:li;content:counter(li);left:0;top:0;width:19px;height:19px;border-radius:50%;
 background:var(--surface2);color:var(--dim);font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center}
/* table */
.ui-table-wrap{overflow-x:auto;border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow-sm)}
.ui-table{border-collapse:collapse;width:100%;font-size:13.5px}
.ui-table th,.ui-table td{padding:10px 14px;text-align:left}
.ui-table th{background:var(--surface2);font-weight:660;color:var(--dim);font-size:11.5px;text-transform:uppercase;letter-spacing:.04em;
 position:sticky;top:0}
.ui-table tbody tr{border-top:1px solid var(--border)}
.ui-table tbody tr:nth-child(even){background:color-mix(in srgb,var(--surface2) 45%,transparent)}
.ui-table tbody tr:hover{background:color-mix(in srgb,var(--accent) 7%,transparent)}
/* progress */
.ui-progress{height:9px;background:var(--surface2);border-radius:999px;overflow:hidden;box-shadow:inset 0 1px 2px rgba(0,0,0,.08)}
.ui-progress-bar{height:100%;border-radius:999px;background:linear-gradient(90deg,color-mix(in srgb,var(--accent),#fff 22%),var(--accent));
 transition:width .5s var(--ease,ease)}
.ui-progress-label{display:flex;justify-content:space-between;font-size:12.5px;color:var(--dim);margin-bottom:6px;font-weight:550}
.ui-progress-label span{color:var(--text);font-weight:700}
/* media */
.ui-fig{margin:0}.ui-fig img,.ui-fig video{width:100%;border-radius:var(--radius);border:1px solid var(--border);display:block;box-shadow:var(--shadow-sm)}
.ui-fig figcaption{color:var(--faint);font-size:12px;margin-top:6px;text-align:center}
.ui-audio{width:100%}
/* buttons */
.ui-btns{display:flex;flex-wrap:wrap;gap:9px}
.ui-btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:9px 16px;border-radius:var(--radius-sm);
 border:1px solid var(--border);background:var(--surface);color:var(--text);font:inherit;font-weight:600;font-size:13.5px;
 cursor:pointer;text-decoration:none;box-shadow:var(--shadow-sm);
 transition:transform .06s var(--ease,ease),box-shadow .15s,background .15s,border-color .15s}
.ui-btn:hover{border-color:var(--border-strong);box-shadow:var(--shadow);transform:translateY(-1px)}
.ui-btn:active{transform:translateY(0)}
.ui-btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.ui-btn.ui-v-accent{background:var(--accent);border-color:transparent;color:var(--accent-contrast)}
.ui-btn.ui-v-accent:hover{background:color-mix(in srgb,var(--accent),#000 8%)}
.ui-btn.ui-v-success{background:var(--success);border-color:transparent;color:#fff}
.ui-btn.ui-v-danger{background:var(--danger);border-color:transparent;color:#fff}
/* forms */
.ui-form{display:grid;gap:12px}
.ui-field{display:grid;gap:5px}
.ui-field-label{font-size:12px;color:var(--dim);font-weight:650;letter-spacing:.01em}
.ui-input{width:100%;padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);
 background:var(--bg);color:var(--text);font:inherit;font-size:13.5px;transition:border-color .15s,box-shadow .15s}
.ui-input::placeholder{color:var(--faint)}
.ui-input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 22%,transparent)}
textarea.ui-input{resize:vertical;min-height:64px}
select.ui-input{appearance:none;background-image:linear-gradient(45deg,transparent 50%,var(--faint) 50%),linear-gradient(135deg,var(--faint) 50%,transparent 50%);
 background-position:calc(100% - 16px) 52%,calc(100% - 11px) 52%;background-size:5px 5px,5px 5px;background-repeat:no-repeat;padding-right:32px}
/* chart */
.ui-chart{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:16px;box-shadow:var(--shadow-sm)}
.ui-chart-title{font-weight:660;margin-bottom:10px;font-size:14px}
.ui-chart-svg{width:100%;height:auto;overflow:visible}
.ui-chart-axis{stroke:var(--border);stroke-width:1}
.ui-chart-val{fill:var(--faint);font-size:11px;font-weight:600}
.ui-chart-lab{fill:var(--dim);font-size:11px}
.ui-pie-wrap{display:flex;gap:22px;align-items:center;flex-wrap:wrap}.ui-pie{max-width:190px;filter:drop-shadow(0 4px 10px rgba(0,0,0,.12))}
.ui-legend{display:grid;gap:8px;font-size:13px}
.ui-legend-item{display:flex;align-items:center;gap:9px;color:var(--dim)}
.ui-legend-item b{color:var(--text);margin-left:auto;padding-left:14px}
.ui-legend-dot{width:12px;height:12px;border-radius:4px;display:inline-block;flex:none}
/* card */
.ui-card{background:var(--surface);border:1px solid var(--border);border-radius:calc(var(--radius) + 2px);padding:16px 18px;box-shadow:var(--shadow-sm)}
.ui-card-title{font-weight:680;font-size:15px;margin-bottom:12px;letter-spacing:-.01em}
.ui-card-body{display:flex;flex-direction:column;gap:var(--gap)}
/* columns */
.ui-columns{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px}
.ui-col{display:flex;flex-direction:column;gap:var(--gap);min-width:0}
/* tabs */
.ui-tabs{border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;background:var(--surface);box-shadow:var(--shadow-sm)}
.ui-tabbar{display:flex;gap:4px;padding:5px;background:var(--surface2);border-bottom:1px solid var(--border);overflow-x:auto;scrollbar-width:none}
.ui-tabbar::-webkit-scrollbar{display:none}
.ui-tab{padding:7px 14px;border:none;background:none;color:var(--dim);font:inherit;font-weight:600;font-size:13px;
 cursor:pointer;white-space:nowrap;border-radius:calc(var(--radius-sm) - 2px);transition:background .15s,color .15s}
.ui-tab:hover{color:var(--text)}
.ui-tab.active{color:var(--text);background:var(--surface);box-shadow:var(--shadow-sm)}
.ui-tabpane{display:none;padding:16px;flex-direction:column;gap:var(--gap)}.ui-tabpane.active{display:flex;animation:ui-fade .2s var(--ease,ease)}
@keyframes ui-fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
/* accordion */
.ui-accordion{display:grid;gap:9px}
.ui-acc-item{border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;background:var(--surface);box-shadow:var(--shadow-sm)}
.ui-acc-head{width:100%;display:flex;justify-content:space-between;align-items:center;gap:12px;padding:13px 15px;
 background:none;border:none;color:var(--text);font:inherit;font-weight:640;font-size:14px;cursor:pointer;text-align:left}
.ui-acc-head:hover{background:color-mix(in srgb,var(--accent) 5%,transparent)}
.ui-acc-chev{transition:transform .2s var(--ease,ease);color:var(--faint);flex:none}
.ui-acc-item.open .ui-acc-chev{transform:rotate(180deg)}
.ui-acc-body{display:none;padding:0 15px 14px;flex-direction:column;gap:var(--gap)}
.ui-acc-item.open .ui-acc-body{display:flex;animation:ui-fade .2s var(--ease,ease)}
/* icons */
.ui-ic{width:1em;height:1em;flex:none;vertical-align:-.13em}
.ui-h-ico{display:flex;align-items:center;gap:9px}.ui-h-ic{width:1.05em;height:1.05em;color:var(--accent)}
.ui-badge-ic{width:13px;height:13px}
.ui-badge.ui-solid{color:#fff;border-color:transparent}
.ui-v-info.ui-solid{background:var(--info)}.ui-v-success.ui-solid{background:var(--success)}
.ui-v-warn.ui-solid{background:var(--warn)}.ui-v-danger.ui-solid{background:var(--danger)}
.ui-v-accent.ui-solid{background:var(--accent)}.ui-v-default.ui-solid{background:var(--dim)}
.ui-badge.ui-solid::before{display:none}
/* divider with text */
.ui-hr-text{display:flex;align-items:center;gap:12px;color:var(--faint);font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.05em}
.ui-hr-text::before,.ui-hr-text::after{content:"";flex:1;height:1px;background:var(--border)}
/* callout with icon */
.ui-callout.ui-has-ic{display:flex;gap:12px;align-items:flex-start;padding-left:14px}
.ui-callout.ui-has-ic::before{display:none}
.ui-callout-ic{width:20px;height:20px;flex:none;margin-top:1px}
.ui-callout.ui-v-info .ui-callout-ic{color:var(--info)}.ui-callout.ui-v-success .ui-callout-ic{color:var(--success)}
.ui-callout.ui-v-warn .ui-callout-ic{color:var(--warn)}.ui-callout.ui-v-danger .ui-callout-ic{color:var(--danger)}
.ui-callout.ui-v-accent .ui-callout-ic{color:var(--accent)}
/* stat with icon */
.ui-stat-row{display:flex;align-items:center;gap:13px}
.ui-stat-ic{width:40px;height:40px;flex:none;border-radius:11px;display:flex;align-items:center;justify-content:center;
 background:var(--surface2);color:var(--dim)}
.ui-stat-ic .ui-ic{width:20px;height:20px}
.ui-stat-ic.ui-v-accent{background:color-mix(in srgb,var(--accent) 16%,transparent);color:var(--accent)}
.ui-stat-ic.ui-v-success{background:color-mix(in srgb,var(--success) 16%,transparent);color:var(--success)}
.ui-stat-ic.ui-v-info{background:color-mix(in srgb,var(--info) 16%,transparent);color:var(--info)}
.ui-stat-ic.ui-v-warn{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn)}
.ui-stat-ic.ui-v-danger{background:color-mix(in srgb,var(--danger) 16%,transparent);color:var(--danger)}
/* button options */
.ui-btn .ui-ic{width:15px;height:15px}
.ui-btn.ui-sm{padding:6px 11px;font-size:12.5px;border-radius:calc(var(--radius-sm) - 1px)}
.ui-btn.ui-lg{padding:12px 22px;font-size:15px}
.ui-btn.ui-outline{background:transparent;box-shadow:none}
.ui-btn.ui-outline.ui-v-accent{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 45%,transparent)}
.ui-btn.ui-outline.ui-v-accent:hover{background:color-mix(in srgb,var(--accent) 10%,transparent)}
.ui-btn.ui-outline.ui-v-danger{color:var(--danger);border-color:color-mix(in srgb,var(--danger) 45%,transparent);background:transparent}
.ui-btn.ui-outline.ui-v-success{color:var(--success);border-color:color-mix(in srgb,var(--success) 45%,transparent);background:transparent}
/* table dense */
.ui-table.ui-dense th,.ui-table.ui-dense td{padding:6px 11px;font-size:12.5px}
/* card variants */
.ui-card-head{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.ui-card-head .ui-card-title{margin:0}
.ui-card-ic{width:20px;height:20px;color:var(--accent);flex:none}
.ui-card-outlined{box-shadow:none}
.ui-card-elevated{box-shadow:var(--shadow);border-color:transparent}
.ui-card-plain{box-shadow:none;background:var(--surface2);border-color:transparent}
.ui-card-tinted{border-color:transparent}
.ui-card-tinted.ui-v-info{background:color-mix(in srgb,var(--info) 9%,var(--surface))}
.ui-card-tinted.ui-v-success{background:color-mix(in srgb,var(--success) 9%,var(--surface))}
.ui-card-tinted.ui-v-warn{background:color-mix(in srgb,var(--warn) 10%,var(--surface))}
.ui-card-tinted.ui-v-danger{background:color-mix(in srgb,var(--danger) 9%,var(--surface))}
.ui-card-tinted.ui-v-accent{background:color-mix(in srgb,var(--accent) 9%,var(--surface))}
.ui-card-foot{margin-top:12px;padding-top:11px;border-top:1px solid var(--border);color:var(--dim);font-size:12.5px}
/* hero */
.ui-hero{display:flex;gap:16px;align-items:center;padding:20px 22px;border-radius:calc(var(--radius) + 3px);
 background:linear-gradient(135deg,color-mix(in srgb,var(--accent) 12%,var(--surface)),var(--surface));
 border:1px solid var(--border);box-shadow:var(--shadow-sm)}
.ui-hero.ui-v-info{background:linear-gradient(135deg,color-mix(in srgb,var(--info) 12%,var(--surface)),var(--surface))}
.ui-hero.ui-v-success{background:linear-gradient(135deg,color-mix(in srgb,var(--success) 12%,var(--surface)),var(--surface))}
.ui-hero-icwrap{width:48px;height:48px;flex:none;border-radius:14px;display:flex;align-items:center;justify-content:center;
 background:var(--accent);color:var(--accent-contrast)}
.ui-hero-icwrap.ui-v-info{background:var(--info)}.ui-hero-icwrap.ui-v-success{background:var(--success)}
.ui-hero-ic{width:24px;height:24px}
.ui-hero-main{flex:1;min-width:0;display:flex;flex-direction:column;gap:4px}
.ui-hero-title{font-size:20px;font-weight:730;letter-spacing:-.02em;line-height:1.2}
.ui-hero-sub{margin:0;color:var(--dim);font-size:13.5px}
.ui-hero-actions{margin-top:8px}
/* steps */
.ui-steps{display:grid;gap:0;padding-left:2px}
.ui-step{display:flex;gap:13px;position:relative;padding-bottom:16px}
.ui-step:not(:last-child)::before{content:"";position:absolute;left:11px;top:24px;bottom:0;width:2px;background:var(--border)}
.ui-step-done:not(:last-child)::before{background:var(--success)}
.ui-step-marker{width:24px;height:24px;flex:none;border-radius:50%;border:2px solid var(--border);background:var(--surface);
 display:flex;align-items:center;justify-content:center;color:#fff;z-index:1}
.ui-step-marker .ui-ic{width:13px;height:13px}
.ui-step-done .ui-step-marker{background:var(--success);border-color:var(--success)}
.ui-step-active .ui-step-marker{border-color:var(--accent);box-shadow:0 0 0 4px color-mix(in srgb,var(--accent) 20%,transparent)}
.ui-step-active .ui-step-marker::after{content:"";width:8px;height:8px;border-radius:50%;background:var(--accent)}
.ui-step-failed .ui-step-marker{background:var(--danger);border-color:var(--danger)}
.ui-step-body{padding-top:1px}
.ui-step-title{font-weight:640;font-size:14px}
.ui-step-active .ui-step-title{color:var(--accent)}
.ui-step-pending .ui-step-title{color:var(--dim)}
.ui-step-text{color:var(--dim);font-size:13px;margin-top:2px}
/* code block */
.ui-code-wrap{border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;background:var(--surface2)}
.ui-code-head{padding:7px 14px;font-size:11.5px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;
 color:var(--faint);background:color-mix(in srgb,var(--border) 40%,transparent);border-bottom:1px solid var(--border)}
.ui-code{margin:0;padding:14px;overflow-x:auto;font-family:ui-monospace,'Cascadia Code','SF Mono',Consolas,monospace;
 font-size:12.5px;line-height:1.6;color:var(--text)}
.ui-code code{background:none;padding:0;font-size:inherit}
/* quote */
.ui-quote{margin:0;padding:12px 16px;border-left:3px solid var(--accent);background:var(--surface2);border-radius:0 var(--radius) var(--radius) 0;
 color:var(--text);font-style:italic;font-size:14.5px}
.ui-quote-author{margin-top:8px;color:var(--faint);font-size:12.5px;font-style:normal;font-weight:600}
.ui-quote-author::before{content:"— "}
/* gauge */
.ui-gauge{display:inline-flex;flex-direction:column;align-items:center;gap:8px}
.ui-gauge-svg{width:120px;height:120px}
.ui-gauge-bg{fill:none;stroke:var(--surface2);stroke-width:11}
.ui-gauge-fg{fill:none;stroke:var(--accent);stroke-width:11;stroke-linecap:round;transition:stroke-dasharray .6s var(--ease,ease)}
.ui-gauge-svg.ui-v-success .ui-gauge-fg{stroke:var(--success)}.ui-gauge-svg.ui-v-danger .ui-gauge-fg{stroke:var(--danger)}
.ui-gauge-svg.ui-v-warn .ui-gauge-fg{stroke:var(--warn)}.ui-gauge-svg.ui-v-info .ui-gauge-fg{stroke:var(--info)}
.ui-gauge-val{fill:var(--text);font-size:22px;font-weight:730}
.ui-gauge-label{color:var(--dim);font-size:13px;font-weight:550}
/* avatar */
.ui-avatar{display:flex;align-items:center;gap:11px}
.ui-avatar-img,.ui-avatar-ini{width:42px;height:42px;flex:none;border-radius:50%;object-fit:cover}
.ui-avatar-ini{background:linear-gradient(135deg,var(--accent),color-mix(in srgb,var(--accent),#000 25%));
 color:var(--accent-contrast);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:15px}
.ui-avatar-name{font-weight:640}.ui-avatar-sub{color:var(--dim);font-size:12.5px}
/* gallery */
.ui-gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:9px}
.ui-gal-cell{margin:0}
.ui-gal-cell img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:var(--radius-sm);border:1px solid var(--border);display:block}
.ui-gal-cell figcaption{color:var(--faint);font-size:11px;margin-top:3px;text-align:center}
/* rating */
.ui-rating{display:inline-flex;align-items:center;gap:2px}
.ui-star .ui-ic{width:18px;height:18px}
.ui-star.full{color:var(--warn)}.ui-star.full .ui-ic{fill:var(--warn)}
.ui-star.empty{color:var(--border-strong)}
.ui-star.half{color:var(--warn)}
.ui-rating-val{margin-left:7px;font-weight:650;color:var(--dim);font-size:13px}
/* chips */
.ui-chips{display:flex;flex-wrap:wrap;gap:7px}
.ui-chip{display:inline-flex;align-items:center;gap:5px;padding:4px 11px;border-radius:999px;font-size:12.5px;font-weight:550;
 background:var(--surface2);color:var(--dim);border:1px solid var(--border)}
.ui-chip.ui-v-accent{background:color-mix(in srgb,var(--accent) 13%,transparent);color:var(--accent);border-color:transparent}
.ui-chip.ui-v-success{background:color-mix(in srgb,var(--success) 13%,transparent);color:var(--success);border-color:transparent}
.ui-chip.ui-v-info{background:color-mix(in srgb,var(--info) 13%,transparent);color:var(--info);border-color:transparent}
.ui-chip.ui-v-warn{background:color-mix(in srgb,var(--warn) 15%,transparent);color:var(--warn);border-color:transparent}
.ui-chip.ui-v-danger{background:color-mix(in srgb,var(--danger) 13%,transparent);color:var(--danger);border-color:transparent}
/* standalone icon */
.ui-icon{display:inline-flex;color:var(--dim)}.ui-icon .ui-ic{width:20px;height:20px}
.ui-icon.ui-v-accent{color:var(--accent)}.ui-icon.ui-v-success{color:var(--success)}.ui-icon.ui-v-info{color:var(--info)}
.ui-icon.ui-v-warn{color:var(--warn)}.ui-icon.ui-v-danger{color:var(--danger)}
.ui-icon-sm .ui-ic{width:15px;height:15px}.ui-icon-lg .ui-ic{width:28px;height:28px}.ui-icon-xl .ui-ic{width:40px;height:40px}
/* horizontal bar chart */
.ui-hbar{display:grid;gap:9px}
.ui-hbar-row{display:grid;grid-template-columns:minmax(60px,26%) 1fr auto;align-items:center;gap:10px}
.ui-hbar-lab{font-size:13px;color:var(--dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ui-hbar-track{height:10px;background:var(--surface2);border-radius:999px;overflow:hidden}
.ui-hbar-fill{height:100%;border-radius:999px;transition:width .5s var(--ease,ease)}
.ui-hbar-val{font-size:12.5px;font-weight:700;color:var(--text);min-width:20px;text-align:right}
.ui-chart-donut-c{fill:var(--text);font-size:20px;font-weight:730}
/* многосерийная легенда (в ряд, над графиком) */
.ui-legend-row{grid-auto-flow:column;grid-template-columns:none;display:flex;flex-wrap:wrap;gap:14px;margin-bottom:10px}
.ui-legend-row .ui-legend-item{color:var(--dim);font-weight:550}
/* спарклайн в комбо-метрике */
.ui-spark{width:100%;max-width:150px;height:30px;margin-top:10px;display:block}
.ui-stat-row+.ui-spark,.ui-stat-row .ui-spark{margin-top:8px}
"""

_JS = """
(function(){
function send(t){if(window.sendPrompt)window.sendPrompt(t);}
document.addEventListener('click',function(ev){
 var t=ev.target.closest('[data-ui-prompt]');
 if(t&&!t.closest('[data-ui-form]')&&t.tagName!=='FORM'){var p=t.getAttribute('data-ui-prompt');if(p)send(p);return;}
 var tab=ev.target.closest('[data-ui-tab]');
 if(tab){var box=tab.closest('.ui-tabs');var i=tab.getAttribute('data-ui-tab');
  box.querySelectorAll('.ui-tab').forEach(function(x){x.classList.toggle('active',x===tab)});
  box.querySelectorAll('[data-ui-pane]').forEach(function(p){p.classList.toggle('active',p.getAttribute('data-ui-pane')===i)});return;}
 var acc=ev.target.closest('[data-ui-acc]');
 if(acc){acc.parentElement.classList.toggle('open');return;}
});
document.addEventListener('submit',function(ev){
 var f=ev.target.closest('[data-ui-form]');if(!f)return;ev.preventDefault();
 var base=f.getAttribute('data-ui-prompt')||'';var lines=[];
 f.querySelectorAll('input,textarea,select').forEach(function(el){
  if(!el.name)return;lines.push(el.name+': '+(el.value||''));});
 send((base?base+'\\n\\n':'')+lines.join('\\n'));
});
})();
"""
