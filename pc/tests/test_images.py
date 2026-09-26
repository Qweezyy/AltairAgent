"""Тесты поиска инлайн-картинок (core/research/images.py) — без сети."""

from __future__ import annotations

import core.research.images as img
from core.research.images import _ok_image, _score, find_images, looks_visual_topic
from core.research.pipeline import ResearchReport
from core.research.sources import SourceRegistry


def test_looks_visual_topic():
    assert looks_visual_topic("Как выглядит персонаж Elden Ring")
    assert looks_visual_topic("Nvidia RTX 4090 видеокарта")
    assert not looks_visual_topic("Как настроить Docker и написать python-скрипт")
    assert not looks_visual_topic("объясни алгоритм быстрой сортировки")


def test_report_hero_image_marker():
    rep = ResearchReport(
        question="Как выглядит Эйфелева башня",
        report="Текст отчёта.",
        registry=SourceRegistry(),
        hero_image_query="Эйфелева башня",
    )
    text = rep.to_text()
    assert text.startswith("![Как выглядит Эйфелева башня](<img:Эйфелева башня>)")

    plain = ResearchReport(question="код на python", report="x", registry=SourceRegistry())
    assert "<img:" not in plain.to_text()


def test_ok_image_filters_junk():
    assert _ok_image("https://upload.wikimedia.org/pic.jpg")
    assert not _ok_image("https://site.com/logo.png")
    assert not _ok_image("https://site.com/favicon.svg")
    assert not _ok_image("ftp://x/y.jpg")


def test_score_prefers_wiki_and_terms():
    terms = {"eiffel", "tower"}
    wiki = {"url": "https://upload.wikimedia.org/eiffel_tower.jpg", "title": "Eiffel"}
    other = {"url": "https://random.host/pic.jpg", "title": "pic"}
    assert _score(wiki, terms) > _score(other, terms)


async def test_find_images_dedup_rank_limit(settings, monkeypatch):
    async def fake_wiki(_client, _q, _lang):
        return [{"url": "https://upload.wikimedia.org/a.jpg", "title": "A", "page": ""}]

    async def fake_commons(_client, _q):
        return [
            {"url": "https://upload.wikimedia.org/a.jpg", "title": "dup", "page": ""},  # дубль
            {"url": "https://host/b_logo.png", "title": "junk", "page": ""},           # мусор
            {"url": "https://host/c.jpg", "title": "C", "page": ""},
        ]

    async def fake_searxng(_client, _q, _url):
        return []

    monkeypatch.setattr(img, "_wiki_main_image", fake_wiki)
    monkeypatch.setattr(img, "_commons_images", fake_commons)
    monkeypatch.setattr(img, "_searxng_images", fake_searxng)
    img._cache.clear()

    res = await find_images("eiffel tower", settings=settings, limit=4)
    urls = [r["url"] for r in res]
    # Дубль схлопнут, мусор отсеян, вики впереди.
    assert urls == ["https://upload.wikimedia.org/a.jpg", "https://host/c.jpg"]


def test_split_keys():
    from core.research.search import split_keys

    assert split_keys("a, b ,c") == ["a", "b", "c"]
    assert split_keys("k1;k2") == ["k1", "k2"]
    assert split_keys("") == []


async def test_find_images_rotates_over_keyed_providers(settings, monkeypatch):
    """Несколько ключей Tavily: сбойный пропускается, рабочий даёт картинку."""
    s = settings.model_copy(update={"tavily_api_key": "bad,good"})
    calls: list[str] = []

    async def fake_tavily(_client, _q, key):
        calls.append(key)
        if key == "bad":
            raise ValueError("limit reached")
        return [{"url": "https://host/from_tavily.jpg", "title": "T", "page": ""}]

    monkeypatch.setattr(img, "_tavily_images", fake_tavily)
    monkeypatch.setattr(img, "_brave_images", lambda *a: _empty())
    monkeypatch.setattr(img, "_wiki_main_image", lambda *a: _empty())
    monkeypatch.setattr(img, "_commons_images", lambda *a: _empty())
    monkeypatch.setattr(img, "_searxng_images", lambda *a: _empty())
    img._cache.clear()

    res = await find_images("some visual thing", settings=s, limit=4)
    assert calls == ["bad", "good"]  # оба ключа перепробованы по очереди
    assert any("from_tavily" in r["url"] for r in res)


async def test_find_images_uses_cache(settings, monkeypatch):
    calls = {"n": 0}

    async def fake_wiki(_client, _q, _lang):
        calls["n"] += 1
        return [{"url": "https://upload.wikimedia.org/x.jpg", "title": "X", "page": ""}]

    monkeypatch.setattr(img, "_wiki_main_image", fake_wiki)
    monkeypatch.setattr(img, "_commons_images", lambda *a: _empty())
    monkeypatch.setattr(img, "_searxng_images", lambda *a: _empty())
    img._cache.clear()

    await find_images("repeated query", settings=settings)
    await find_images("repeated query", settings=settings)
    assert calls["n"] == 1  # второй раз — из кэша


async def _empty():
    return []
