"""Поиск в интернете как структурированные данные.

Инструмент `web_search` показывает результаты человеку и модели текстом, а
исследованию нужны поля: заголовок, ссылка, описание. Поэтому вся работа с
поисковиками живёт здесь и возвращает список словарей, а форматирование —
задача вызывающего.

Порядок источников: Tavily -> Brave -> DuckDuckGo -> Qwant. Первые два нужны
только если пользователь ввёл ключ; без ключей поиск всё равно работает.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal
from urllib.parse import parse_qs, quote_plus, urlparse

import httpx
from bs4 import BeautifulSoup

from core.logging_setup import get_logger
from core.settings import Settings

logger = get_logger("research.search")

TimeLimit = Literal["d", "w", "m", "y"] | None

#: Фильтр свежести в терминах каждого поисковика.
_TAVILY_RANGE = {"d": "day", "w": "week", "m": "month", "y": "year"}


def split_keys(raw: str) -> list[str]:
    """Ключи через запятую → список. Для ротации нескольких ключей одного сервиса."""
    return [k.strip() for k in (raw or "").replace(";", ",").split(",") if k.strip()]


def _hit(title: str, url: str, snippet: str, engine: str) -> dict[str, str]:
    return {
        "title": (title or "").strip(),
        "url": (url or "").strip(),
        "snippet": " ".join((snippet or "").split())[:500],
        "engine": engine,
    }


async def search_web(
    query: str,
    *,
    settings: Settings,
    max_results: int = 6,
    timelimit: TimeLimit = None,
) -> list[dict[str, str]]:
    """Ищет в интернете и возвращает список результатов.

    Пустой список означает «ничего не нашлось или все поисковики недоступны»;
    решать, что с этим делать, — задача вызывающего.
    """
    from core.tools.builtin.web import get_http_client  # локальный импорт: общий HTTP-клиент

    client = get_http_client()
    # Именно функции, а не готовые корутины: создать корутину и не дождаться её —
    # значит получить предупреждение «coroutine was never awaited» на каждый
    # поисковик, до которого очередь не дошла.
    # Ротация ключей: каждый ключ каждого сервиса — отдельная попытка. Не сработал
    # (лимит/401/пусто) — пробуем следующий ключ, затем следующий сервис, затем
    # бесплатные DuckDuckGo/Qwant. Так «кончился лимит» не ломает поиск.
    attempts: list[tuple[str, Callable[[], Awaitable[list[dict[str, str]]]]]] = []
    for key in split_keys(settings.tavily_api_key):
        attempts.append(("tavily", lambda k=key: _tavily(client, query, k, max_results, timelimit)))
    for key in split_keys(settings.brave_api_key):
        attempts.append(("brave", lambda k=key: _brave(client, query, k, max_results, timelimit)))
    attempts.append(("duckduckgo", lambda: _duckduckgo(client, query, max_results, timelimit)))
    attempts.append(("qwant", lambda: _qwant(client, query, max_results)))

    for name, attempt in attempts:
        try:
            results = await attempt()
        except Exception:  # noqa: BLE001 - недоступный поисковик не повод падать
            logger.debug("Поисковик %s недоступен для «%s», пробую следующий", name, query, exc_info=True)
            continue
        if results:
            return results[:max_results]
        logger.debug("Поисковик %s не дал результатов (лимит?), пробую следующий", name)
    return []


async def _tavily(
    client: httpx.AsyncClient, query: str, api_key: str, limit: int, timelimit: TimeLimit
) -> list[dict[str, str]]:
    payload: dict[str, Any] = {
        "api_key": api_key,
        "query": query,
        "max_results": limit,
        "search_depth": "basic",
    }
    if timelimit in _TAVILY_RANGE:
        payload["time_range"] = _TAVILY_RANGE[timelimit]

    resp = await client.post("https://api.tavily.com/search", json=payload, timeout=20.0)
    if resp.status_code != 200:
        return []
    return [
        _hit(item.get("title", ""), item.get("url", ""), item.get("content", ""), "tavily")
        for item in resp.json().get("results", [])
    ]


async def _brave(
    client: httpx.AsyncClient, query: str, api_key: str, limit: int, timelimit: TimeLimit
) -> list[dict[str, str]]:
    params: dict[str, Any] = {"q": query, "count": limit}
    if timelimit:
        params["freshness"] = f"p{timelimit}"

    resp = await client.get(
        "https://api.search.brave.com/res/v1/web/search",
        params=params,
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        timeout=20.0,
    )
    if resp.status_code != 200:
        return []
    return [
        _hit(item.get("title", ""), item.get("url", ""), item.get("description", ""), "brave")
        for item in resp.json().get("web", {}).get("results", [])
    ]


async def _duckduckgo(
    client: httpx.AsyncClient, query: str, limit: int, timelimit: TimeLimit
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    post_data = {"q": query}
    if timelimit:
        post_data["df"] = timelimit

    for variant in ("html", "lite"):
        try:
            if variant == "html":
                resp = await client.post("https://html.duckduckgo.com/html/", data=post_data)
                selectors = (".result", ".result__a", ".result__snippet")
            else:
                query_str = f"q={quote_plus(query)}" + (f"&df={timelimit}" if timelimit else "")
                resp = await client.get(f"https://lite.duckduckgo.com/lite/?{query_str}")
                selectors = ("tr", ".result-link", ".result-snippet")
        except httpx.HTTPError:
            continue

        if resp.status_code != 200:
            continue

        block_sel, link_sel, snippet_sel = selectors
        soup = BeautifulSoup(resp.text, "html.parser")
        for block in soup.select(block_sel):
            link = block.select_one(link_sel)
            if not link:
                continue
            href = link.get("href", "")
            if "uddg=" in href:
                # DuckDuckGo прячет адрес за редиректом — достаём настоящий.
                href = parse_qs(urlparse(href).query).get("uddg", [href])[0]
            if not href.startswith("http") or "duckduckgo.com" in href:
                continue
            snippet = block.select_one(snippet_sel)
            results.append(
                _hit(
                    link.get_text(" ", strip=True),
                    href,
                    snippet.get_text(" ", strip=True) if snippet else "",
                    "duckduckgo",
                )
            )
            if len(results) >= limit:
                break
        if results:
            break
    return results


async def _qwant(client: httpx.AsyncClient, query: str, limit: int) -> list[dict[str, str]]:
    resp = await client.get(f"https://lite.qwant.com/?q={quote_plus(query)}&t=web", timeout=15.0)
    if resp.status_code != 200:
        return []

    results: list[dict[str, str]] = []
    soup = BeautifulSoup(resp.text, "html.parser")
    for article in soup.select(".result"):
        link = article.select_one("a.title")
        if not link:
            continue
        href = link.get("href", "")
        if not href.startswith("http"):
            continue
        desc = article.select_one(".desc")
        results.append(
            _hit(
                link.get_text(strip=True),
                href,
                desc.get_text(" ", strip=True) if desc else "",
                "qwant",
            )
        )
        if len(results) >= limit:
            break
    return results
