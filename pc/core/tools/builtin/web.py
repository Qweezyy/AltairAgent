"""Веб-инструменты: поиск, чтение страниц, REST-запросы, скачивание."""

from __future__ import annotations

import asyncio
import json
from typing import Literal
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from core.errors import ToolError
from core.i18n import tr
from core.research.search import search_web
from core.security.paths import resolve_path, safe_relpath
from core.tools.base import Tool, ToolContext, ToolResult
from core.tools.external_content import guard_external

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """Переиспользуемый HTTP-клиент (keep-alive вместо нового соединения на вызов)."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            headers=HEADERS,
            follow_redirects=True,
            timeout=httpx.Timeout(20.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _require_http_url(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    if parsed.scheme not in ("http", "https"):
        raise ToolError(f"Поддерживаются только http/https URL, получено: '{url}'.")
    if not parsed.netloc:
        raise ToolError(f"Некорректный URL: '{url}'.")
    return parsed.geturl()


# -------------------------------------------------------------- search


class SearchArgs(BaseModel):
    query: str = Field(description="Search query")
    max_results: int = Field(default=6, ge=1, le=15, description="Maximum results")
    timelimit: Literal["d", "w", "m", "y"] | None = Field(
        default=None,
        description="Freshness filter: 'd' (24h), 'w' (week), 'm' (month), 'y' (year)",
    )


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Searches the web and returns titles, links and snippets. Open the most relevant pages "
        "with fetch_url for details. For a broad topic that needs many sources, deep_research "
        "(load it with tool_search) produces a sourced overview."
    )
    Args = SearchArgs
    category = "network"
    timeout = 45.0

    def auto_verdict(self, args, ctx) -> str:  # type: ignore[override]
        """Поиск только читает."""
        return "allow"

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.web_search", query=args.query)

    async def run(self, args: SearchArgs, ctx: ToolContext) -> str:
        results = await search_web(
            args.query,
            settings=ctx.settings,
            max_results=args.max_results,
            timelimit=args.timelimit,
        )
        if not results:
            time_note = f" (с фильтром timelimit='{args.timelimit}')" if args.timelimit else ""
            raise ToolError(
                f"Поиск по запросу «{args.query}»{time_note} не дал результатов. "
                "Попробуй переформулировать запрос или снять ограничение по времени."
            )

        engine = results[0].get("engine", "")
        filter_info = f" [период: {args.timelimit}]" if args.timelimit else ""
        body = "\n\n".join(
            f"{index}. {item['title']}\n   URL: {item['url']}\n   {item['snippet']}"
            for index, item in enumerate(results, 1)
        )
        # Заголовки и сниппеты — тоже чужой текст: инъекция бывает и в выдаче.
        guarded = await guard_external(ctx, body, source=f"поиск «{args.query}»")
        return f"Результаты поиска «{args.query}»{filter_info} ({engine}):\n\n{guarded}"



# --------------------------------------------------------------- fetch


class FetchUrlArgs(BaseModel):
    url: str = Field(description="Page URL")
    start_char: int = Field(default=0, ge=0, description="Offset for reading long pages in parts")
    max_chars: int = Field(default=8000, ge=500, le=40000)


class FetchUrlTool(Tool):
    name = "fetch_url"
    description = (
        "Fetches a web page and returns its readable text without scripts and ads. "
        "Read long pages in parts with start_char."
    )
    Args = FetchUrlArgs
    category = "network"
    timeout = 45.0

    def auto_verdict(self, args, ctx) -> str:  # type: ignore[override]
        """Чтение страницы безопасно."""
        return "allow"

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.open_url", url=args.url)

    async def run(self, args: FetchUrlArgs, ctx: ToolContext) -> str:
        url = _require_http_url(args.url)
        try:
            resp = await get_http_client().get(url)
        except httpx.HTTPError as exc:
            raise ToolError(f"Не удалось загрузить '{url}': {exc}") from exc

        if resp.status_code >= 400:
            raise ToolError(f"Страница '{url}' вернула HTTP {resp.status_code}.")

        content_type = resp.headers.get("content-type", "")
        if "html" not in content_type and "xml" not in content_type:
            text = resp.text
        else:
            text = _html_to_text(resp.text)

        total = len(text)
        if total == 0:
            raise ToolError(f"Страница '{url}' не содержит текста (возможно, рендерится через JS).")

        start = min(args.start_char, total)
        end = min(total, start + args.max_chars)
        body = text[start:end]
        footer = ""
        if end < total:
            footer = f"\n\n... [символы {start}-{end} из {total}. Продолжи: fetch_url(start_char={end})]"
        # Содержимое страницы — чужой текст. Оборачиваем как данные и проверяем
        # на промпт-инъекции, чтобы модель не приняла его за команды.
        guarded = await guard_external(ctx, f"{body}{footer}", source=url)
        return f"# {url}\n[символы {start}-{end} из {total}]\n\n{guarded}"


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg"]):
        tag.decompose()

    lines: list[str] = []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "blockquote", "td"]):
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if el.name.startswith("h"):
            lines.append(f"\n{'#' * int(el.name[1])} {text}")
        elif el.name == "li":
            lines.append(f"- {text}")
        elif el.name == "pre":
            lines.append(f"```\n{text}\n```")
        else:
            lines.append(text)

    if not lines:
        lines = [soup.get_text("\n", strip=True)]
    return "\n".join(lines)


# ----------------------------------------------------------- http_call


class HttpRequestArgs(BaseModel):
    url: str = Field(description="URL эндпоинта")
    method: str = Field(default="GET", description="GET, POST, PUT, PATCH или DELETE")
    headers: dict[str, str] = Field(default_factory=dict)
    json_body: dict | list | None = Field(default=None, description="Тело запроса в виде JSON")
    params: dict[str, str] = Field(default_factory=dict, description="Query-параметры")


class HttpRequestTool(Tool):
    name = "http_request"
    description = "Выполняет произвольный HTTP-запрос к API (GET/POST/PUT/PATCH/DELETE) и возвращает ответ."
    Args = HttpRequestArgs
    category = "network"
    dangerous = True
    timeout = 60.0

    def approval_reason(self, args: HttpRequestArgs) -> str:
        return tr("appr.http", method=args.method.upper(), url=args.url)

    async def run(self, args: HttpRequestArgs, ctx: ToolContext) -> ToolResult:
        url = _require_http_url(args.url)
        method = args.method.upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
            raise ToolError(f"Неподдерживаемый HTTP-метод: {method}")

        try:
            resp = await get_http_client().request(
                method,
                url,
                headers=args.headers or None,
                json=args.json_body,
                params=args.params or None,
            )
        except httpx.HTTPError as exc:
            raise ToolError(f"Запрос к '{url}' не удался: {exc}") from exc

        try:
            body = json.dumps(resp.json(), ensure_ascii=False, indent=2)
        except (ValueError, json.JSONDecodeError):
            body = resp.text

        return ToolResult(
            content=f"HTTP {resp.status_code} {resp.reason_phrase}\n\n{body}",
            ok=resp.status_code < 400,
        )


# ------------------------------------------------------------ download


class DownloadArgs(BaseModel):
    url: str = Field(description="Прямая ссылка на файл")
    destination: str = Field(description="Куда сохранить, например 'downloads/data.zip'")
    max_mb: float = Field(default=200.0, gt=0, le=2048, description="Лимит размера в мегабайтах")


class DownloadFileTool(Tool):
    name = "download_file"
    description = "Скачивает файл по прямой ссылке в рабочую директорию."
    Args = DownloadArgs
    category = "network"
    dangerous = True
    timeout = 600.0

    def approval_reason(self, args: DownloadArgs) -> str:
        return tr("appr.download", url=args.url, dest=args.destination)

    async def run(self, args: DownloadArgs, ctx: ToolContext) -> str:
        url = _require_http_url(args.url)
        dest = resolve_path(args.destination, settings=ctx.settings)
        dest.parent.mkdir(parents=True, exist_ok=True)
        limit_bytes = int(args.max_mb * 1024 * 1024)

        total = 0
        try:
            async with get_http_client().stream("GET", url, timeout=300.0) as resp:
                if resp.status_code >= 400:
                    raise ToolError(f"Сервер вернул HTTP {resp.status_code}.")
                fh = await asyncio.to_thread(open, dest, "wb")
                try:
                    async for chunk in resp.aiter_bytes(65536):
                        total += len(chunk)
                        if total > limit_bytes:
                            raise ToolError(
                                f"Файл больше лимита {args.max_mb} МБ — скачивание прервано."
                            )
                        await asyncio.to_thread(fh.write, chunk)
                finally:
                    await asyncio.to_thread(fh.close)
        except httpx.HTTPError as exc:
            dest.unlink(missing_ok=True)
            raise ToolError(f"Ошибка скачивания: {exc}") from exc

        return (
            f"Файл сохранён: {safe_relpath(dest, ctx.settings)} "
            f"({total / 1024 / 1024:.2f} МБ)."
        )
