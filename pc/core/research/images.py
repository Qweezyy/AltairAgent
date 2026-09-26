"""Поиск релевантной картинки по текстовому запросу (для инлайна в ответах).

Идея как в ResearchAI: найти НАСТОЯЩЕЕ изображение сущности (персонаж, место,
устройство), а не логотип/иконку. Источники без ключей: Wikipedia (главное
изображение статьи через MediaWiki `pageimages`) и Wikimedia Commons (поиск по
файлам). Если задан SEARXNG_URL — добавляем image-поиск SearXNG.

Наружу отдаём список кандидатов {url, title, page}, отранжированных и очищенных
от мусора. Результаты кэшируются в памяти (запросы часто повторяются).
"""

from __future__ import annotations

import re
import time

import httpx

from core.logging_setup import get_logger
from core.settings import Settings, get_settings

logger = get_logger("research.images")

_UA = "LocalAIAgent/1.0 (inline research images; contact: local)"
_CACHE_TTL = 24 * 60 * 60  # сутки
_cache: dict[str, tuple[float, list[dict]]] = {}

#: Мусор в URL картинки — логотипы, иконки, заглушки: их не показываем.
_JUNK = re.compile(
    r"logo|icon|favicon|placeholder|sprite|avatar|banner|thumb/rating|"
    r"transparent|blank|spinner|loading|1x1|pixel|\.svg(\?|$)",
    re.I,
)
#: Языки Википедии, которые пробуем по очереди (запрос может быть на любом).
_WIKI_LANGS = ("ru", "en")


#: Темы, к которым картинка уместна (сущность можно «увидеть»).
_VISUAL_TOPIC = re.compile(
    r"персонаж|герой|игра|фильм|сериал|аниме|актёр|актер|актрис|место|город|стран|"
    r"карт[аы]|фото|картин|изображ|арт|скриншот|как выглядит|дизайн|бро[нн]|оруж|локац|"
    r"босс|npc|нпс|видеокарт|процессор|ноутбук|телефон|камер|автомоб|машин|достопримеч|"
    r"character|game|movie|series|anime|actor|place|city|country|photo|picture|screenshot|"
    r"artwork|hardware|gpu|cpu|laptop|phone|camera|car|landmark|looks like",
    re.I,
)
#: Явно НЕвизуальные (код/абстракции) — там картинка не нужна.
_NONVISUAL_TOPIC = re.compile(
    r"\b(code|api|sql|regex|docker|kubernetes|typescript|python|javascript|react|node|"
    r"nginx|linux|git|cli|terminal|algorithm)\b|код|ошибк|настро|установ|скрипт|команд|"
    r"конфиг|алгоритм|формул|уравнени",
    re.I,
)


def looks_visual_topic(text: str) -> bool:
    """Стоит ли к этой теме подобрать картинку (эвристика, как в ResearchAI)."""
    t = (text or "").strip()
    if not t:
        return False
    if _NONVISUAL_TOPIC.search(t) and not _VISUAL_TOPIC.search(t):
        return False
    return bool(_VISUAL_TOPIC.search(t))


def _clean_query(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip())[:200]


def _ok_image(url: str) -> bool:
    return bool(url) and url.startswith("http") and not _JUNK.search(url)


async def _wiki_main_image(client: httpx.AsyncClient, query: str, lang: str) -> list[dict]:
    """Главное изображение наиболее подходящей статьи Википедии."""
    api = f"https://{lang}.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrlimit": "3",
        "prop": "pageimages|info",
        "piprop": "original|thumbnail",
        "pithumbsize": "900",
        "inprop": "url",
        "format": "json",
        "origin": "*",
    }
    try:
        resp = await client.get(api, params=params, timeout=12.0)
        resp.raise_for_status()
        pages = (resp.json().get("query") or {}).get("pages") or {}
    except (httpx.HTTPError, ValueError):
        return []
    out: list[dict] = []
    # Сортируем по индексу поиска: чем меньше, тем релевантнее.
    for page in sorted(pages.values(), key=lambda p: p.get("index", 99)):
        original = (page.get("original") or {}).get("source")
        thumb = (page.get("thumbnail") or {}).get("source")
        url = original or thumb
        if url and _ok_image(url):
            out.append({"url": url, "title": page.get("title", query), "page": page.get("fullurl", "")})
    return out


async def _commons_images(client: httpx.AsyncClient, query: str) -> list[dict]:
    """Файлы Wikimedia Commons по запросу (запасной источник)."""
    api = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",  # File:
        "gsrlimit": "6",
        "prop": "imageinfo",
        "iiprop": "url",
        "iiurlwidth": "900",
        "format": "json",
        "origin": "*",
    }
    try:
        resp = await client.get(api, params=params, timeout=12.0)
        resp.raise_for_status()
        pages = (resp.json().get("query") or {}).get("pages") or {}
    except (httpx.HTTPError, ValueError):
        return []
    out: list[dict] = []
    for page in sorted(pages.values(), key=lambda p: p.get("index", 99)):
        info = (page.get("imageinfo") or [{}])[0]
        url = info.get("thumburl") or info.get("url")
        if url and _ok_image(url) and re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", url, re.I):
            out.append({"url": url, "title": page.get("title", "").removeprefix("File:"), "page": info.get("descriptionurl", "")})
    return out


async def _brave_images(client: httpx.AsyncClient, query: str, key: str) -> list[dict]:
    """Image-поиск Brave (нужен ключ). Возвращает [] при лимите/ошибке — ротация выше."""
    resp = await client.get(
        "https://api.search.brave.com/res/v1/images/search",
        params={"q": query, "count": 15},
        headers={"Accept": "application/json", "X-Subscription-Token": key},
        timeout=12.0,
    )
    if resp.status_code != 200:
        return []
    out: list[dict] = []
    for item in (resp.json().get("results") or [])[:15]:
        url = (item.get("properties") or {}).get("url") or (item.get("thumbnail") or {}).get("src") or ""
        if _ok_image(url):
            out.append({"url": url, "title": item.get("title", query), "page": item.get("url", "")})
    return out


async def _tavily_images(client: httpx.AsyncClient, query: str, key: str) -> list[dict]:
    """Картинки из Tavily (include_images). [] при лимите/ошибке."""
    resp = await client.post(
        "https://api.tavily.com/search",
        json={"api_key": key, "query": query, "include_images": True, "max_results": 5, "search_depth": "basic"},
        timeout=15.0,
    )
    if resp.status_code != 200:
        return []
    out: list[dict] = []
    for img in (resp.json().get("images") or [])[:15]:
        url = img if isinstance(img, str) else (img.get("url", "") if isinstance(img, dict) else "")
        title = "" if isinstance(img, str) else (img.get("description", "") if isinstance(img, dict) else "")
        if _ok_image(url):
            out.append({"url": url, "title": title or query, "page": ""})
    return out


async def _searxng_images(client: httpx.AsyncClient, query: str, base_url: str) -> list[dict]:
    """Image-поиск через SearXNG, если он настроен (опционально)."""
    url = base_url.rstrip("/") + "/search"
    params = {"q": query, "categories": "images", "format": "json"}
    try:
        resp = await client.get(url, params=params, timeout=12.0)
        resp.raise_for_status()
        results = resp.json().get("results") or []
    except (httpx.HTTPError, ValueError):
        return []
    out: list[dict] = []
    for item in results[:15]:
        img = item.get("img_src") or item.get("thumbnail_src") or ""
        if img.startswith("//"):
            img = "https:" + img
        if _ok_image(img):
            out.append({"url": img, "title": item.get("title", query), "page": item.get("url", "")})
    return out


def _score(candidate: dict, terms: set[str]) -> float:
    url = candidate["url"].lower()
    score = 0.0
    for term in terms:
        if term in url or term in candidate.get("title", "").lower():
            score += 0.8
    if re.search(r"wikipedia|wikimedia|wikia|fandom|static", url):
        score += 1.5
    if re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", url):
        score += 0.4
    return score


async def find_images(query: str, *, settings: Settings | None = None, limit: int = 4) -> list[dict]:
    """Возвращает до `limit` кандидатов-картинок {url, title, page}."""
    settings = settings or get_settings()
    q = _clean_query(query)
    if not q:
        return []

    cached = _cache.get(q)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1][:limit]

    from core.research.search import split_keys

    candidates: list[dict] = []

    async def _try(coro) -> None:
        """Добавляет кандидатов, молча пропуская сбойный/лимитный источник (ротация)."""
        try:
            candidates.extend(await coro)
        except (httpx.HTTPError, ValueError):
            pass

    async with httpx.AsyncClient(headers={"User-Agent": _UA}, follow_redirects=True) as client:
        # Ключевые сервисы: каждый ключ — отдельная попытка; сбой одного не мешает.
        for key in split_keys(settings.tavily_api_key):
            await _try(_tavily_images(client, q, key))
        for key in split_keys(settings.brave_api_key):
            await _try(_brave_images(client, q, key))
        searxng = (settings.searxng_url or "").strip()
        if searxng:
            await _try(_searxng_images(client, q, searxng))
        # Бесплатные и надёжные источники — всегда (хороши для реальных сущностей).
        for lang in _WIKI_LANGS:
            got = await _wiki_main_image(client, q, lang)
            if got:
                candidates += got
                break  # хватит первого языка, где есть картинка
        if len(candidates) < limit:
            await _try(_commons_images(client, q))

    # Дедуп и отсев мусора С СОХРАНЕНИЕМ ПОРЯДКА источников: если заданы ключи
    # Tavily/Brave, их результаты идут первыми и получают приоритет (как и для
    # текстового поиска). Внутри одного источника порядок — как вернул сервис.
    seen: set[str] = set()
    unique: list[dict] = []
    for cand in candidates:
        if cand["url"] in seen or not _ok_image(cand["url"]):
            continue
        seen.add(cand["url"])
        unique.append(cand)

    _cache[q] = (time.time(), unique)
    return unique[:limit]


async def find_image(query: str, *, settings: Settings | None = None) -> dict | None:
    """Лучшая одна картинка по запросу (или None)."""
    found = await find_images(query, settings=settings, limit=1)
    return found[0] if found else None
