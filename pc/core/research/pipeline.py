"""Многошаговое исследование: план -> поиск -> чтение -> конспекты -> отчёт.

Почему не «один поиск и хватит»: одна выдача отвечает на один запрос. На
настоящий вопрос («что сейчас с X и какие есть варианты») ответа в одной
выдаче нет — нужно разложить его на подзапросы, собрать разные источники,
прочитать их и свести противоречия в один текст со ссылками.

Каждый шаг сообщает о себе событием ResearchProgress: исследование идёт
минутами, и молчание в интерфейсе неотличимо от зависания.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from core.events import Emitter, ResearchProgress, noop_emitter
from core.llm.base import LLMClient
from core.logging_setup import get_logger
from core.research.documents import DocumentError, extract_document, is_document
from core.research.search import search_web
from core.research.sources import Source, SourceRegistry
from core.settings import Settings

logger = get_logger("research")

#: Насколько глубоко копаем. Числа подобраны так, чтобы «быстро» укладывалось
#: примерно в минуту, а «глубоко» — в несколько.
DEPTH_PRESETS = {
    # rounds — сколько раундов «поиск → чтение → анализ пробелов»: 1 = один проход,
    # >1 = рекурсивное дозакрытие пробелов (Query Fan-Out).
    # verify — сверять ли утверждения отчёта с процитированными источниками.
    "quick": {"queries": 3, "sources": 5, "chars": 6000, "rounds": 1, "verify": False},
    "standard": {"queries": 4, "sources": 9, "chars": 9000, "rounds": 2, "verify": True},
    "deep": {"queries": 6, "sources": 14, "chars": 12000, "rounds": 3, "verify": True},
}

#: Ниже этого объёма считаем, что страница не отдала текст без JavaScript.
JS_TEXT_THRESHOLD = 600


@dataclass(slots=True)
class ResearchReport:
    """Результат исследования."""

    question: str
    report: str
    registry: SourceRegistry
    queries: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    #: Результат проверки цитат: пусто, если проверка не проводилась.
    verification: str = ""
    #: Запрос для hero-картинки в начале отчёта (пусто — тема невизуальная).
    hero_image_query: str = ""

    def to_text(self) -> str:
        """Отчёт со списком литературы — то, что увидит модель и пользователь."""
        parts = []
        if self.hero_image_query:
            # Инлайн-маркер: фронтенд подставит реальную картинку (или уберёт).
            parts.append(f"![{self.question}](<img:{self.hero_image_query}>)")
        parts.append(self.report.strip())

        if self.verification.strip():
            parts.append(f"## Проверка источников\n{self.verification.strip()}")

        bibliography = self.registry.bibliography()
        if bibliography:
            parts.append(f"## Источники\n{bibliography}")

        if self.failed:
            broken = "\n".join(f"- {url}: {reason}" for url, reason in self.failed[:8])
            parts.append(f"## Не удалось прочитать\n{broken}")

        return "\n\n".join(parts)


class DeepResearcher:
    """Проводит исследование по вопросу и собирает отчёт со ссылками."""

    def __init__(
        self,
        llm: LLMClient,
        settings: Settings,
        *,
        emitter: Emitter = noop_emitter,
    ) -> None:
        self.llm = llm
        self.settings = settings
        self.emitter = emitter

    async def run(
        self,
        question: str,
        *,
        depth: str = "standard",
        timelimit: str | None = None,
        focus: str = "",
    ) -> ResearchReport:
        preset = DEPTH_PRESETS.get(depth, DEPTH_PRESETS["standard"])
        max_sources = min(preset["sources"], self.settings.research_max_sources)
        max_rounds = int(preset.get("rounds", 1))

        registry = SourceRegistry()
        failed: list[tuple[str, str]] = []
        all_digests: list[tuple[Source, str]] = []
        chosen_urls: set[str] = set()
        all_queries: list[str] = []

        queries = await self._plan(question, preset["queries"], focus)

        for round_no in range(1, max_rounds + 1):
            if not queries:
                break
            all_queries.extend(queries)
            label = "План поиска" if round_no == 1 else f"Раунд {round_no}: доискиваю пробелы"
            await self._progress("plan", f"{label}: " + "; ".join(queries), 0, len(queries))

            await self._collect(queries, registry, timelimit)
            # Первый раунд берёт основной бюджет источников, дозакрытие — поменьше.
            budget = max_sources if round_no == 1 else max(3, max_sources // 2)
            chosen = self._select(registry, budget, exclude=chosen_urls)
            if not chosen:
                break
            chosen_urls.update(s.url for s in chosen)
            await self._progress("read", f"Отобрано источников: {len(chosen)}", 0, len(chosen))

            await self._read_all(chosen, preset["chars"], failed)
            read = [s for s in chosen if s.text and not s.error]
            digests = await self._digest_all(question, read)
            all_digests.extend(digests)

            # Анализ пробелов: если чего-то не хватает и раунды ещё есть —
            # формируем точечные запросы на следующий круг.
            if round_no < max_rounds and all_digests:
                queries = await self._find_gaps(question, all_digests, focus)
            else:
                queries = []

        if not len(registry):
            raise RuntimeError(
                f"По запросу «{question}» ничего не нашлось. "
                "Переформулируйте вопрос или снимите ограничение по свежести."
            )
        if not all_digests:
            raise RuntimeError(
                "Ни один источник не удалось прочитать или в них не было ничего по теме. "
                "Попробуйте другой запрос."
            )

        await self._progress("report", "Свожу источники в отчёт", len(all_digests), len(all_digests))
        report = await self._synthesize(question, all_digests, focus)

        verification = ""
        if preset.get("verify"):
            verification = await self._verify_citations(question, report, all_digests)

        from core.research.images import looks_visual_topic

        return ResearchReport(
            question=question,
            report=report,
            registry=registry,
            queries=all_queries,
            failed=failed,
            verification=verification,
            hero_image_query=question if looks_visual_topic(question) else "",
        )

    # ------------------------------------------------------------- шаги

    async def _plan(self, question: str, count: int, focus: str) -> list[str]:
        """Разбивает вопрос на поисковые запросы."""
        await self._progress("plan", "Разбиваю вопрос на поисковые запросы")

        focus_note = f"\nОсобое внимание: {focus}" if focus else ""
        raw = await self._ask(
            "Ты планируешь поиск информации в интернете.",
            f"Вопрос исследования: {question}{focus_note}\n\n"
            f"Составь {count} разных поисковых запросов, которые вместе покроют тему: "
            "разные формулировки, разные стороны вопроса, при необходимости — "
            "запросы на английском. Запросы должны быть короткими, как в поисковой строке.\n"
            'Ответь ТОЛЬКО массивом JSON, например: ["запрос один", "запрос два"]',
        )

        queries = _parse_string_list(raw)
        if not queries:
            # План не обязателен для работы: сам вопрос — тоже нормальный запрос.
            logger.warning("Модель не вернула план поиска, использую исходный вопрос")
            return [question]
        return queries[:count]

    async def _collect(
        self, queries: list[str], registry: SourceRegistry, timelimit: str | None
    ) -> None:
        """Ищет по всем запросам разом и складывает результаты в реестр."""
        await self._progress("search", f"Ищу по {len(queries)} запросам", 0, len(queries))

        tasks = [
            search_web(query, settings=self.settings, max_results=8, timelimit=timelimit)  # type: ignore[arg-type]
            for query in queries
        ]
        batches = await asyncio.gather(*tasks, return_exceptions=True)

        for index, batch in enumerate(batches, 1):
            if isinstance(batch, BaseException):
                logger.debug("Поиск «%s» не удался", queries[index - 1], exc_info=batch)
                continue
            for item in batch:
                registry.add(item["url"], item.get("title", ""), item.get("snippet", ""))
            await self._progress(
                "search", f"Найдено ссылок: {len(registry)}", index, len(queries)
            )

    def _select(
        self, registry: SourceRegistry, limit: int, *, exclude: set[str] | None = None
    ) -> list[Source]:
        """Отбирает источники, разбавляя выдачу по доменам.

        Десять ссылок с одного сайта — это один источник, а не десять: сначала
        берём по одной с каждого домена, и лишь потом добираем остальные.
        `exclude` — URL уже отобранных в прошлых раундах, чтобы не читать повторно.
        """
        exclude = exclude or set()
        by_domain: dict[str, list[Source]] = {}
        for source in registry.sources:
            if source.url in exclude:
                continue
            by_domain.setdefault(source.domain, []).append(source)

        chosen: list[Source] = []
        round_index = 0
        while len(chosen) < limit:
            added = False
            for group in by_domain.values():
                if round_index < len(group):
                    chosen.append(group[round_index])
                    added = True
                    if len(chosen) >= limit:
                        break
            if not added:
                break
            round_index += 1
        return chosen

    async def _read_all(self, sources: list[Source], max_chars: int, failed: list) -> None:
        """Открывает источники параллельно, но не все сразу."""
        semaphore = asyncio.Semaphore(max(1, self.settings.research_concurrency))
        done = 0

        async def read_one(source: Source) -> None:
            nonlocal done
            async with semaphore:
                try:
                    source.text = await fetch_source_text(source.url, max_chars=max_chars)
                except Exception as exc:  # noqa: BLE001 - недоступный сайт это норма
                    source.error = str(exc)[:200]
                    failed.append((source.url, source.error))
                done += 1
                await self._progress(
                    "read",
                    f"Читаю: {source.domain}",
                    done,
                    len(sources),
                )

        await asyncio.gather(*(read_one(source) for source in sources))

    async def _digest_all(self, question: str, sources: list[Source]) -> list[tuple[Source, str]]:
        """Сжимает каждый источник до фактов, относящихся к вопросу."""
        await self._progress("digest", f"Выписываю главное из {len(sources)} источников", 0, len(sources))

        semaphore = asyncio.Semaphore(max(1, self.settings.research_concurrency))
        done = 0

        async def digest_one(source: Source) -> tuple[Source, str]:
            nonlocal done
            async with semaphore:
                try:
                    text = await self._ask(
                        "Ты выписываешь из текста только то, что относится к вопросу.",
                        f"Вопрос: {question}\n\n"
                        f"Источник [{source.index}] {source.title} ({source.url}):\n"
                        f"---\n{source.text}\n---\n\n"
                        "Выпиши факты, цифры и утверждения, относящиеся к вопросу, "
                        "списком коротких пунктов. Числа и даты сохраняй точно. "
                        "Если источник ничего по теме не содержит, ответь одним словом: НЕТ.",
                    )
                except Exception as exc:  # noqa: BLE001 - сбой одного конспекта не рушит отчёт
                    logger.debug("Конспект источника %s не удался", source.url, exc_info=exc)
                    text = ""
                done += 1
                await self._progress("digest", f"Конспект: {source.domain}", done, len(sources))
                return source, text.strip()

        results = await asyncio.gather(*(digest_one(source) for source in sources))
        return [(source, text) for source, text in results if text and text.upper() != "НЕТ"]

    async def _find_gaps(
        self, question: str, digests: list[tuple[Source, str]], focus: str
    ) -> list[str]:
        """Смотрит на собранное и решает, чего не хватает. Возвращает запросы-добивки.

        Пустой список = тема покрыта, дозакрывать нечего.
        """
        await self._progress("gaps", "Проверяю, всё ли собрано для ответа")
        # Конспекты уже короткие; на всякий случай ограничиваем общий объём.
        covered = "\n".join(f"- {text}" for _s, text in digests)[:6000]
        focus_note = f"\nОсобое внимание: {focus}" if focus else ""

        raw = await self._ask(
            "Ты проверяешь полноту собранного материала перед написанием ответа.",
            f"Вопрос исследования: {question}{focus_note}\n\n"
            f"Уже собранные факты из источников:\n{covered}\n\n"
            "Каких ВАЖНЫХ аспектов не хватает, чтобы полно и уверенно ответить на вопрос? "
            "Учитывай пробелы, противоречия, непокрытые стороны темы. "
            "Если материала уже достаточно — верни пустой массив [].\n"
            "Иначе верни 1–3 коротких поисковых запроса (как в поисковой строке), "
            "которые закроют именно эти пробелы.\n"
            'Ответь ТОЛЬКО массивом JSON: ["запрос"] или [].',
            max_tokens=400,
        )
        gaps = _parse_string_list(raw)[:3]
        if gaps:
            await self._progress("gaps", "Нужно доискать: " + "; ".join(gaps))
        return gaps

    async def _verify_citations(
        self, question: str, report: str, digests: list[tuple[Source, str]]
    ) -> str:
        """Скептически сверяет утверждения отчёта с процитированными источниками.

        Возвращает заметку о проверке: перечень неподтверждённых/натянутых
        утверждений или подтверждение, что всё опирается на источники. Отдельным
        придирчивым проходом — чтобы поймать галлюцинации, которые автор отчёта не
        заметил за собой.
        """
        await self._progress("verify", "Сверяю утверждения с источниками")
        material = "\n\n".join(
            f"[{source.index}] {source.title}\n{text}" for source, text in digests
        )
        note = await self._ask(
            "Ты придирчивый фактчекер. Твоя задача — найти в тексте утверждения, "
            "которые НЕ подтверждаются процитированным источником (выдуманы, преувеличены "
            "или приписаны не тому источнику). Не выискивай мелочи стиля — только факты.",
            f"Вопрос исследования: {question}\n\n"
            f"ОТЧЁТ (утверждения помечены ссылками [номер]):\n{report}\n\n"
            f"КОНСПЕКТЫ ИСТОЧНИКОВ:\n{material}\n\n"
            "Проверь: каждое утверждение со ссылкой [N] должно подтверждаться конспектом "
            "источника [N]. Верни КРАТКО:\n"
            "- если есть неподтверждённые/натянутые утверждения — перечисли их списком, "
            "у каждого укажи ссылку [N] и в чём расхождение с источником;\n"
            "- если все утверждения опираются на источники — напиши ровно: "
            "«Все утверждения подтверждаются процитированными источниками.»",
            max_tokens=1200,
        )
        return note.strip()

    async def _synthesize(self, question: str, digests: list[tuple[Source, str]], focus: str) -> str:
        """Собирает финальный отчёт со ссылками на источники."""
        if not digests:
            return (
                f"По вопросу «{question}» источники нашлись, но ничего относящегося к теме "
                "в них не оказалось. Стоит переформулировать вопрос."
            )

        material = "\n\n".join(
            f"[{source.index}] {source.title} ({source.url})\n{text}" for source, text in digests
        )
        focus_note = f"\nОсобое внимание: {focus}" if focus else ""

        return await self._ask(
            f"Ты пишешь аналитический обзор на языке: {self.settings.agent_language}.",
            f"Вопрос: {question}{focus_note}\n\n"
            f"Конспекты источников:\n{material}\n\n"
            "Напиши связный обзор в markdown:\n"
            "- каждое утверждение снабжай ссылкой на источник в виде [номер];\n"
            "- если источники противоречат друг другу, покажи оба и укажи расхождение;\n"
            "- не добавляй ничего, чего нет в конспектах;\n"
            "- в конце добавь раздел «Что осталось неясным», если данных не хватило.\n"
            "Список литературы не пиши — он добавится автоматически.",
            # Итоговый обзор длиннее подзадач — даём ему больше места.
            max_tokens=6000,
        )

    # ------------------------------------------------------- вспомогательное

    async def _ask(self, system: str, prompt: str, *, max_tokens: int = 2000) -> str:
        # Ограничиваем вывод: подзадачи исследования короткие (запросы, конспект,
        # обзор), а без потолка reasoning-модель может зациклиться и жечь токены
        # десятками тысяч на каждый источник.
        turn = await self.llm.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        return turn.content or ""

    async def _progress(self, phase: str, text: str, done: int = 0, total: int = 0) -> None:
        try:
            await self.emitter(
                ResearchProgress(phase=phase, text=text, done=done, total=total)  # type: ignore[arg-type]
            )
        except Exception:  # noqa: BLE001 - интерфейс не должен ломать исследование
            logger.debug("Не удалось отправить прогресс исследования", exc_info=True)


# ------------------------------------------------------------------ чтение


async def fetch_source_text(url: str, *, max_chars: int = 9000) -> str:
    """Достаёт текст источника: HTML, документ или страница на JavaScript.

    Порядок попыток выбран по цене: обычный запрос дешевле, чем запуск
    браузера, поэтому Chromium поднимается только когда без него текста нет.
    """
    from core.tools.builtin.web import _html_to_text, _require_http_url, get_http_client

    url = _require_http_url(url)
    client = get_http_client()

    response = await client.get(url)
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}")

    content_type = response.headers.get("content-type", "").lower()
    name = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]

    if "pdf" in content_type or response.content[:5] == b"%PDF-":
        # Ссылка на PDF часто выглядит как обычная (arxiv.org/pdf/1706.03762),
        # поэтому формат определяем по ответу, а не по имени.
        name = "документ.pdf"
    elif "spreadsheet" in content_type:
        name = "таблица.xlsx"

    if is_document(name):
        try:
            document = extract_document(response.content, name, max_chars=max_chars)
            return document.text
        except DocumentError as exc:
            raise RuntimeError(str(exc)) from exc

    text = _html_to_text(response.text) if "html" in content_type or "xml" in content_type else response.text

    if len(text.strip()) < JS_TEXT_THRESHOLD:
        try:
            from core.research.browser import render_page

            page = await render_page(url, timeout=25.0)
            if len(page.text) > len(text):
                text = page.text
        except Exception as exc:  # noqa: BLE001 - без браузера просто отдаём, что есть
            logger.debug("Рендеринг %s не удался: %s", url, exc)

    text = text.strip()
    if not text:
        raise RuntimeError("страница не отдала текст")
    return text[:max_chars]


def _parse_string_list(raw: str) -> list[str]:
    """Достаёт массив строк из ответа модели, даже если вокруг него есть текст."""
    candidates: list[Any] = []
    try:
        candidates.append(json.loads(raw))
    except (ValueError, TypeError):
        match = re.search(r"\[.*\]", raw or "", re.DOTALL)
        if match:
            try:
                candidates.append(json.loads(match.group(0)))
            except ValueError:
                pass

    for candidate in candidates:
        if isinstance(candidate, list):
            items = [str(item).strip() for item in candidate if str(item).strip()]
            if items:
                return items

    # Последняя попытка: модель могла ответить просто списком строк.
    lines = [
        re.sub(r'^[\s\-\*\d\.\)"]+|"$', "", line).strip()
        for line in (raw or "").splitlines()
    ]
    return [line for line in lines if len(line) > 3][:8]
