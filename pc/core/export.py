"""Экспорт диалога в Markdown, HTML и PDF.

Источник — лента интерфейса (`session.timeline`), а не `messages`: лента хранит
ровно то, что видел пользователь (вопрос → шаги → ответ), тогда как messages —
служебный формат модели с tool_call_id и системными репликами. Экспортировать
надо «человеческую» версию диалога.

PDF делается через системный браузерный движок (Edge/Chrome в headless-режиме,
`--print-to-pdf`). Это сознательный выбор: на Windows Edge есть всегда, а тянуть
reportlab/weasyprint в 30-МБ сборку ради печати — несоразмерно.
"""

from __future__ import annotations

import html
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.logging_setup import get_logger

logger = get_logger("export")

#: Стандартные места установки Edge и Chrome на Windows. Проверяются по порядку;
#: первый существующий и используется как движок печати PDF.
_BROWSER_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


def _fmt_duration(ms: Any) -> str:
    try:
        seconds = float(ms) / 1000.0
    except (TypeError, ValueError):
        return ""
    if seconds < 60:
        return f"{seconds:.1f} с"
    return f"{int(seconds // 60)} мин {int(seconds % 60)} с"


def _answer_meta(entry: dict[str, Any]) -> str:
    """Строка-подпись под ответом: шаги, время, стоимость — как в интерфейсе."""
    bits: list[str] = []
    steps = entry.get("steps")
    if steps:
        bits.append(f"шагов: {steps}")
    dur = _fmt_duration(entry.get("duration_ms"))
    if dur:
        bits.append(dur)
    cost = entry.get("cost_usd")
    if isinstance(cost, (int, float)) and cost > 0:
        bits.append(f"${cost:.4f}")
    return " · ".join(bits)


def session_to_markdown(data: dict[str, Any], *, turn: int | None = None) -> str:
    """Собирает Markdown из данных сессии (`session.to_dict()`).

    `turn` — если задан, экспортируется только один ответ: N-й по счёту в ленте
    (0 — первый). Иначе весь диалог.
    """
    title = data.get("title") or "Диалог"
    timeline = data.get("timeline") or []

    lines: list[str] = [f"# {title}", ""]
    model = data.get("model")
    if model:
        lines.append(f"*Модель: `{model}`*")
    stamp = _fmt_ts(data.get("updated_at") or data.get("created_at"))
    if stamp:
        lines.append(f"*Экспортировано из Local AI Agent, {stamp}*")
    lines.append("")
    lines.append("---")
    lines.append("")

    answer_index = -1
    for entry in timeline:
        kind = entry.get("kind")
        if kind == "user":
            if turn is not None:
                # В режиме одного ответа вопрос печатаем только для нужного хода.
                continue
            lines += [f"## 🧑 {(entry.get('text') or '').strip()}", ""]
        elif kind == "answer":
            answer_index += 1
            if turn is not None and answer_index != turn:
                continue
            lines += ["### 🤖 Ответ", "", (entry.get("text") or "").strip(), ""]
            meta = _answer_meta(entry)
            if meta:
                lines += [f"<sub>{meta}</sub>", ""]
            if turn is not None:
                break
        elif kind == "error" and turn is None:
            lines += [f"> ⚠️ **Задача прервана:** {(entry.get('text') or '').strip()}", ""]

    text = "\n".join(lines).rstrip() + "\n"
    return text


# --------------------------------------------------------------------------- HTML

_HTML_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  font: 15px/1.6 -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
  color: #1a1a1a; background: #fff; max-width: 760px; margin: 0 auto;
  padding: 32px 28px;
}
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 26px 0 6px; color: #0f5132;
     border-left: 3px solid #198754; padding-left: 10px; }
h3 { font-size: 14px; text-transform: uppercase; letter-spacing: .04em;
     color: #6c757d; margin: 18px 0 6px; }
.meta { color: #6c757d; font-size: 13px; margin: 0 0 6px; }
hr { border: none; border-top: 1px solid #e5e5e5; margin: 18px 0; }
pre { background: #f6f8fa; padding: 12px 14px; border-radius: 8px;
      overflow-x: auto; font: 13px/1.5 "SF Mono", Consolas, monospace; }
code { background: #f0f1f2; padding: 1px 5px; border-radius: 4px;
       font: 13px "SF Mono", Consolas, monospace; }
pre code { background: none; padding: 0; }
blockquote { border-left: 3px solid #ffc107; margin: 10px 0;
             padding: 4px 14px; color: #664d03; background: #fff8e1; }
table { border-collapse: collapse; margin: 12px 0; }
th, td { border: 1px solid #d5d5d5; padding: 6px 10px; text-align: left; }
sub { color: #999; }
.answer { margin-bottom: 8px; }
@media print { body { padding: 0; } a { color: inherit; text-decoration: none; } }
"""


def _md_inline_to_html(text: str) -> str:
    """Минимальная разметка внутри строки: `code`, **bold**, *italic*.

    Полноценный markdown-парсер здесь не нужен — HTML/PDF идут в печать, а не в
    веб-приложение, где уже есть marked.js. Экранируем всё, потом возвращаем
    небольшой набор тегов.
    """
    import re

    out = html.escape(text)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", out)
    return out


def _md_block_to_html(md: str) -> str:
    """Грубый blocklevel-рендер: заголовки, кодоблоки, цитаты, списки, абзацы."""
    import re

    lines = md.split("\n")
    html_parts: list[str] = []
    i = 0
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            html_parts.append("</ul>")
            in_list = False

    while i < len(lines):
        line = lines[i]
        fence = re.match(r"^```(\w*)", line)
        if fence:
            close_list()
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(html.escape(lines[i]))
                i += 1
            i += 1
            html_parts.append("<pre><code>" + "\n".join(body) + "</code></pre>")
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            close_list()
            level = len(heading.group(1))
            html_parts.append(f"<h{level}>{_md_inline_to_html(heading.group(2))}</h{level}>")
            i += 1
            continue
        if line.startswith("> "):
            close_list()
            html_parts.append(f"<blockquote>{_md_inline_to_html(line[2:])}</blockquote>")
            i += 1
            continue
        bullet = re.match(r"^[-*]\s+(.*)$", line)
        if bullet:
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{_md_inline_to_html(bullet.group(1))}</li>")
            i += 1
            continue
        submeta = re.match(r"^<sub>(.*)</sub>$", line.strip())
        if submeta:
            close_list()
            html_parts.append(
                f'<p style="color:#999;font-size:12px;margin:2px 0 4px">'
                f"{_md_inline_to_html(submeta.group(1))}</p>"
            )
            i += 1
            continue
        if line.strip() == "---":
            close_list()
            html_parts.append("<hr>")
            i += 1
            continue
        if line.strip() == "":
            close_list()
            i += 1
            continue
        close_list()
        html_parts.append(f"<p>{_md_inline_to_html(line)}</p>")
        i += 1

    close_list()
    return "\n".join(html_parts)


def session_to_html(data: dict[str, Any], *, turn: int | None = None) -> str:
    """Самодостаточный HTML для печати. Строится поверх markdown-версии."""
    title = data.get("title") or "Диалог"
    md = session_to_markdown(data, turn=turn)
    # Первую строку-заголовок markdown убираем — он станет <h1> из шаблона,
    # чтобы не задваивался.
    body_md = md.split("\n", 1)[1] if md.startswith("# ") else md
    body_html = _md_block_to_html(body_md)
    return (
        "<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title><style>{_HTML_CSS}</style></head>"
        f"<body><h1>{html.escape(title)}</h1>{body_html}</body></html>"
    )


# ---------------------------------------------------------------------------- PDF


def find_print_browser() -> str | None:
    """Путь к Edge/Chrome для печати PDF, либо None если ни один не найден."""
    for candidate in _BROWSER_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    # Запасной путь: вдруг лежит в PATH под другим именем.
    for name in ("msedge", "chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    return None


class PdfEngineMissing(RuntimeError):
    """Нечем собрать PDF (нет ни Playwright-Chromium, ни Edge/Chrome)."""


def _pdf_via_playwright(html_text: str, out_path: Path) -> bool:
    """Печать через встроенный Chromium Playwright. Возвращает True при успехе.

    Это основной движок: Chromium Playwright изолирован и, в отличие от системного
    Edge, не конфликтует с уже открытым браузером пользователя (запущенный Edge
    заставляет `--print-to-pdf` молча завершаться, не создав файл).

    Возвращает False, если Playwright/Chromium не установлены, — тогда пробуем
    системный браузер.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(html_text, wait_until="networkidle")
                page.pdf(
                    path=str(out_path),
                    format="A4",
                    print_background=True,
                    margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"},
                )
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 - любой сбой = пробуем запасной движок
        logger.info("Playwright не смог напечатать PDF (%s), пробуем системный браузер", exc)
        return False
    return out_path.exists() and out_path.stat().st_size > 0


def _pdf_via_system_browser(html_text: str, out_path: Path) -> bool:
    """Запасной движок: системный Edge/Chrome через --print-to-pdf.

    Ненадёжен, если у пользователя уже открыт Edge (тогда headless-процесс
    присоединяется к существующему и файл не создаётся). Используется, только
    когда Playwright недоступен.
    """
    browser = find_print_browser()
    if not browser:
        return False

    tmp_dir = Path(tempfile.mkdtemp(prefix="aiagent-pdf-"))
    html_file = tmp_dir / "export.html"
    html_file.write_text(html_text, encoding="utf-8")
    # Именно старый --headless: режим «new» игнорирует --print-to-pdf.
    cmd = [
        browser,
        "--headless",
        "--disable-gpu",
        "--no-first-run",
        "--no-pdf-header-footer",
        f"--user-data-dir={tmp_dir / 'profile'}",
        f"--print-to-pdf={out_path}",
        html_file.as_uri(),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.info("Системный браузер не смог напечатать PDF: %s", exc)
        return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return out_path.exists() and out_path.stat().st_size > 0


def session_to_pdf(data: dict[str, Any], out_path: Path, *, turn: int | None = None) -> Path:
    """Рендерит диалог в PDF. Возвращает путь к файлу.

    Пробует движки по очереди: сначала изолированный Chromium Playwright, затем
    системный Edge/Chrome. Бросает PdfEngineMissing, если ни один не сработал —
    вызывающий код должен предложить экспорт в Markdown/HTML.
    """
    html_text = session_to_html(data, turn=turn)
    if _pdf_via_playwright(html_text, out_path):
        return out_path
    if _pdf_via_system_browser(html_text, out_path):
        return out_path
    raise PdfEngineMissing(
        "Не удалось собрать PDF: нет встроенного Chromium (playwright install "
        "chromium) и системного Edge/Chrome. Экспортируйте в Markdown или HTML."
    )


# ------------------------------------------------------------------------- имена


def safe_filename(title: str, suffix: str) -> str:
    """Имя файла из заголовка диалога: только безопасные символы, с расширением."""
    keep = []
    for ch in (title or "dialog").strip():
        if ch.isalnum() or ch in " -_()":
            keep.append(ch)
        elif ch.isspace():
            keep.append(" ")
    name = "".join(keep).strip() or "dialog"
    name = name[:60].rstrip()
    return f"{name}.{suffix}"


def timestamp_suffix() -> str:
    """Короткая метка времени для уникальности временных файлов."""
    return str(int(time.time()))
