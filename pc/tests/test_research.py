"""Глубокое исследование: реестр источников, разбор документов, конвейер."""

from __future__ import annotations

import csv
import io

import pytest

from core.llm.base import AssistantTurn, LLMClient
from core.research.documents import DocumentError, extract_document, is_document
from core.research.pipeline import DeepResearcher, _parse_string_list
from core.research.sources import SourceRegistry, normalize_url

# ------------------------------------------------------------- источники


def test_tracking_params_do_not_create_duplicates():
    registry = SourceRegistry()
    first = registry.add("https://example.com/статья?utm_source=tg", "Статья")
    second = registry.add("http://www.example.com/статья/?utm_source=vk#top")

    assert first is second, "одна и та же страница не должна занять два номера"
    assert len(registry) == 1


def test_different_pages_get_different_numbers():
    registry = SourceRegistry()
    registry.add("https://example.com/a")
    registry.add("https://example.com/b")

    assert [s.index for s in registry.sources] == [1, 2]


def test_meaningful_query_params_are_kept():
    """?id=5 меняет страницу, в отличие от рекламной метки."""
    assert normalize_url("https://site.ru/p?id=5") != normalize_url("https://site.ru/p?id=6")


def test_title_is_filled_in_from_a_later_mention():
    registry = SourceRegistry()
    registry.add("https://example.com/x")
    source = registry.add("https://example.com/x", "Настоящий заголовок")

    assert source.title == "Настоящий заголовок"


def test_bibliography_lists_only_read_sources():
    registry = SourceRegistry()
    read = registry.add("https://example.com/a", "Прочитано")
    read.text = "текст"
    registry.add("https://example.com/b", "Не открывали")

    bibliography = registry.bibliography()
    assert "Прочитано" in bibliography
    assert "Не открывали" not in bibliography


# ------------------------------------------------------------- документы


def test_csv_is_read_with_windows_encoding():
    data = "имя;значение\nстрока;5\n".encode("cp1251")
    document = extract_document(data, "данные.csv")

    assert "имя;значение" in document.text


def test_large_table_is_cut_with_a_note():
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    for index in range(500):
        writer.writerow([index, f"строка {index}"])

    document = extract_document(buffer.getvalue().encode("utf-8"), "big.csv")
    assert "из 500" in document.text


def test_unknown_format_explains_what_is_supported():
    with pytest.raises(DocumentError) as exc:
        extract_document(b"data", "архив.rar")

    assert ".pdf" in str(exc.value)


def test_document_is_truncated_to_the_limit():
    data = ("строка данных\n" * 1000).encode("utf-8")
    document = extract_document(data, "long.txt", max_chars=100)

    assert len(document.text) == 100
    assert document.truncated


def test_is_document_recognizes_extensions():
    assert is_document("отчёт.PDF")
    assert is_document("таблица.xlsx")
    assert not is_document("script.py")


# --------------------------------------------------------------- конвейер


def test_query_plan_survives_a_chatty_model():
    """Модель часто добавляет текст вокруг JSON — план всё равно должен читаться."""
    raw = 'Вот запросы:\n["курс рубля 2026", "прогноз ЦБ"]\nУдачи!'
    assert _parse_string_list(raw) == ["курс рубля 2026", "прогноз ЦБ"]


def test_query_plan_falls_back_to_plain_lines():
    raw = "1. курс рубля\n2. прогноз ЦБ"
    assert _parse_string_list(raw) == ["курс рубля", "прогноз ЦБ"]


class FakeLLM(LLMClient):
    """Модель, отвечающая по сценарию: план -> конспекты -> отчёт."""

    model = "fake"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, messages, **kwargs):  # type: ignore[override]
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)

        if "массивом JSON" in prompt:
            return AssistantTurn(content='["запрос один", "запрос два"]')
        if "Выпиши факты" in prompt:
            return AssistantTurn(content="- факт из источника")
        return AssistantTurn(content="Обзор темы со ссылкой [1].")


@pytest.fixture
def researcher(settings, monkeypatch):
    """Исследователь с поддельными поиском и чтением — без выхода в сеть."""
    import core.research.pipeline as pipeline

    async def fake_search(query, *, settings, max_results=8, timelimit=None):
        return [
            {"title": f"Статья про {query}", "url": f"https://site-{query[-1]}.ru/a", "snippet": "", "engine": "test"},
            {"title": "Вторая", "url": "https://other.ru/b", "snippet": "", "engine": "test"},
        ]

    async def fake_fetch(url, *, max_chars=9000):
        if "other.ru" in url:
            raise RuntimeError("HTTP 403")
        return f"Содержимое страницы {url}"

    monkeypatch.setattr(pipeline, "search_web", fake_search)
    monkeypatch.setattr(pipeline, "fetch_source_text", fake_fetch)
    return DeepResearcher(FakeLLM(), settings)


async def test_research_produces_a_report_with_citations(researcher):
    report = await researcher.run("Что нового в Python?", depth="quick")
    text = report.to_text()

    assert "[1]" in text
    assert "## Источники" in text
    assert report.queries == ["запрос один", "запрос два"]


async def test_unreachable_sources_are_listed_not_hidden(researcher):
    """Молча потерянный источник — худший вид ошибки в исследовании."""
    report = await researcher.run("Что нового в Python?", depth="quick")

    assert any("other.ru" in url for url, _ in report.failed)
    assert "Не удалось прочитать" in report.to_text()


async def test_research_fails_loudly_when_nothing_can_be_read(settings, monkeypatch):
    import core.research.pipeline as pipeline

    async def fake_search(query, *, settings, max_results=8, timelimit=None):
        return [{"title": "x", "url": "https://closed.ru/a", "snippet": "", "engine": "test"}]

    async def always_fails(url, *, max_chars=9000):
        raise RuntimeError("HTTP 403")

    monkeypatch.setattr(pipeline, "search_web", fake_search)
    monkeypatch.setattr(pipeline, "fetch_source_text", always_fails)

    with pytest.raises(RuntimeError, match="Ни один источник"):
        await DeepResearcher(FakeLLM(), settings).run("вопрос", depth="quick")


async def test_progress_is_reported_for_every_phase(researcher):
    """Без прогресса минутное исследование выглядит зависанием."""
    seen: list[str] = []

    async def collect(event):
        seen.append(event.phase)

    researcher.emitter = collect
    await researcher.run("Что нового в Python?", depth="quick")

    assert {"plan", "search", "read", "digest", "report"} <= set(seen)


async def test_pdf_without_extension_in_url_is_recognized(settings, monkeypatch):
    """arxiv.org/pdf/1706.03762 — это PDF, хотя «расширение» в ссылке ложное."""
    import core.tools.builtin.web as web_module
    from core.tools.builtin.research import ReadDocumentTool

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/pdf"}
        content = b"%PDF-1.4 fake"

    class FakeClient:
        async def get(self, url, **kwargs):
            return FakeResponse()

    captured: dict[str, str] = {}

    def fake_extract(data, name, *, max_chars):
        captured["name"] = name
        raise DocumentError("проверка имени")

    monkeypatch.setattr(web_module, "get_http_client", lambda: FakeClient())
    monkeypatch.setattr("core.tools.builtin.research.extract_document", fake_extract)

    ctx = _context(settings)
    await ReadDocumentTool().invoke({"source": "https://arxiv.org/pdf/1706.03762"}, ctx)
    assert captured["name"].endswith(".pdf")


async def test_html_link_points_to_the_right_tool(settings, monkeypatch):
    """Страницу подсовывать в read_document бессмысленно — скажем, чем читать."""
    import core.tools.builtin.web as web_module
    from core.tools.builtin.research import ReadDocumentTool

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        content = b"<html></html>"

    class FakeClient:
        async def get(self, url, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(web_module, "get_http_client", lambda: FakeClient())

    result = await ReadDocumentTool().invoke({"source": "https://example.com/"}, _context(settings))
    assert not result.ok
    assert "fetch_url" in result.content


def _context(settings):
    from core.tools.base import ToolContext

    return ToolContext(settings=settings)


async def test_sources_are_spread_across_domains(researcher):
    """Пять ссылок с одного сайта — это один источник, а не пять."""
    registry = SourceRegistry()
    for index in range(5):
        registry.add(f"https://one.ru/{index}")
    registry.add("https://two.ru/x")
    registry.add("https://three.ru/x")

    chosen = researcher._select(registry, 3)
    assert {source.domain for source in chosen} == {"one.ru", "two.ru", "three.ru"}


class GapLLM(LLMClient):
    """Модель, у которой анализ пробелов сначала просит добить, потом говорит «всё»."""

    model = "fake"

    def __init__(self) -> None:
        self.gap_calls = 0
        self.searched_queries: list[str] = []

    async def complete(self, messages, **kwargs):  # type: ignore[override]
        prompt = messages[-1]["content"]
        if "не хватает" in prompt:  # это _find_gaps
            self.gap_calls += 1
            if self.gap_calls == 1:
                return AssistantTurn(content='["добивочный запрос"]')
            return AssistantTurn(content="[]")  # больше пробелов нет
        if "массивом JSON" in prompt:  # план поиска
            return AssistantTurn(content='["основной запрос"]')
        if "Выпиши факты" in prompt:
            return AssistantTurn(content="- собранный факт")
        return AssistantTurn(content="Обзор [1].")


async def test_gap_filling_runs_extra_round(settings, monkeypatch):
    """Стандартный режим доискивает пробелы вторым раундом."""
    import core.research.pipeline as pipeline

    seen: list[str] = []

    async def fake_search(query, *, settings, max_results=8, timelimit=None):
        seen.append(query)
        # каждый запрос даёт свой уникальный источник
        return [{"title": f"про {query}", "url": f"https://s-{len(seen)}.ru/a", "snippet": "", "engine": "t"}]

    async def fake_fetch(url, *, max_chars=9000):
        return f"текст {url}"

    monkeypatch.setattr(pipeline, "search_web", fake_search)
    monkeypatch.setattr(pipeline, "fetch_source_text", fake_fetch)

    llm = GapLLM()
    report = await DeepResearcher(llm, settings).run("вопрос", depth="standard")

    # Анализ пробелов вызвался, и добивочный запрос реально ушёл в поиск.
    assert llm.gap_calls >= 1
    assert "добивочный запрос" in seen
    assert "добивочный запрос" in report.queries


async def test_no_gaps_stops_after_first_round(settings, monkeypatch):
    """Если пробелов нет — второй раунд не запускается."""
    import core.research.pipeline as pipeline

    searches = 0

    async def fake_search(query, *, settings, max_results=8, timelimit=None):
        nonlocal searches
        searches += 1
        return [{"title": "x", "url": f"https://u-{searches}.ru/a", "snippet": "", "engine": "t"}]

    async def fake_fetch(url, *, max_chars=9000):
        return "текст"

    class NoGapLLM(GapLLM):
        async def complete(self, messages, **kwargs):  # type: ignore[override]
            if "не хватает" in messages[-1]["content"]:
                return AssistantTurn(content="[]")  # сразу нет пробелов
            return await super().complete(messages, **kwargs)

    monkeypatch.setattr(pipeline, "search_web", fake_search)
    monkeypatch.setattr(pipeline, "fetch_source_text", fake_fetch)

    await DeepResearcher(NoGapLLM(), settings).run("вопрос", depth="standard")
    # Один раунд поиска = один запрос (план дал один «основной запрос»).
    assert searches == 1


class VerifyLLM(LLMClient):
    """Модель, у которой шаг проверки цитат даёт узнаваемый вердикт."""

    model = "fake"

    async def complete(self, messages, **kwargs):  # type: ignore[override]
        prompt = messages[-1]["content"]
        system = messages[0]["content"] if messages else ""
        if "фактчекер" in system:  # это _verify_citations
            return AssistantTurn(content="ВЕРДИКТ ФАКТЧЕКЕРА: всё подтверждается.")
        if "не хватает" in prompt:
            return AssistantTurn(content="[]")
        if "массивом JSON" in prompt:
            return AssistantTurn(content='["запрос"]')
        if "Выпиши факты" in prompt:
            return AssistantTurn(content="- факт")
        return AssistantTurn(content="Обзор [1].")


async def test_standard_verifies_citations(settings, monkeypatch):
    import core.research.pipeline as pipeline

    async def fake_search(query, *, settings, max_results=8, timelimit=None):
        return [{"title": "t", "url": "https://a.ru/x", "snippet": "", "engine": "t"}]

    async def fake_fetch(url, *, max_chars=9000):
        return "текст"

    monkeypatch.setattr(pipeline, "search_web", fake_search)
    monkeypatch.setattr(pipeline, "fetch_source_text", fake_fetch)

    report = await DeepResearcher(VerifyLLM(), settings).run("вопрос", depth="standard")
    assert report.verification
    text = report.to_text()
    assert "## Проверка источников" in text
    assert "ВЕРДИКТ ФАКТЧЕКЕРА" in text


async def test_quick_skips_verification(settings, monkeypatch):
    import core.research.pipeline as pipeline

    async def fake_search(query, *, settings, max_results=8, timelimit=None):
        return [{"title": "t", "url": "https://a.ru/x", "snippet": "", "engine": "t"}]

    async def fake_fetch(url, *, max_chars=9000):
        return "текст"

    monkeypatch.setattr(pipeline, "search_web", fake_search)
    monkeypatch.setattr(pipeline, "fetch_source_text", fake_fetch)

    report = await DeepResearcher(VerifyLLM(), settings).run("вопрос", depth="quick")
    assert report.verification == ""
    assert "## Проверка источников" not in report.to_text()
