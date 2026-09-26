"""Инструменты глубокого исследования: браузер, документы, отчёт со ссылками."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from core.errors import ToolError
from core.llm.openai_client import OpenAICompatClient
from core.research.browser import render_page
from core.research.documents import FORMATS, DocumentError, extract_document
from core.research.pipeline import DEPTH_PRESETS, DeepResearcher
from core.security.paths import resolve_path
from core.tools.base import Tool, ToolContext
from core.tools.external_content import guard_external

# ------------------------------------------------------------ browse_page


class BrowsePageArgs(BaseModel):
    url: str = Field(description="Адрес страницы")
    wait_for: str = Field(
        default="",
        description="CSS-селектор, появления которого нужно дождаться (например '.article-body')",
    )
    max_chars: int = Field(default=12000, ge=500, le=40000)


class BrowsePageTool(Tool):
    name = "browse_page"
    description = (
        "Открывает страницу в настоящем браузере и возвращает текст ПОСЛЕ выполнения скриптов. "
        "Нужен для сайтов, где fetch_url возвращает пусто или обрывки: одностраничные "
        "приложения, ленты с подгрузкой, интерактивные таблицы. "
        "Дороже fetch_url — сначала пробуй его."
    )
    Args = BrowsePageArgs
    category = "network"
    timeout = 120.0

    def auto_verdict(self, args, ctx) -> str:  # type: ignore[override]
        """Открыть страницу — то же чтение, что и fetch_url."""
        return "allow"

    async def run(self, args: BrowsePageArgs, ctx: ToolContext) -> str:
        page = await render_page(args.url, wait_for=args.wait_for or None, timeout=45.0)
        if not page.text:
            raise ToolError(
                f"Страница '{args.url}' открылась, но текста в ней нет. "
                "Возможно, контент за авторизацией или нужен другой селектор в wait_for."
            )

        body = page.text[: args.max_chars]
        tail = ""
        if len(page.text) > args.max_chars:
            tail = f"\n\n... [показано {args.max_chars} символов из {len(page.text)}]"

        links = "\n".join(f"- {text}: {href}" for text, href in page.links[:20])
        links_block = f"\n\n## Ссылки со страницы\n{links}" if links else ""
        guarded = await guard_external(ctx, f"{body}{tail}{links_block}", source=page.url)
        return f"# {page.title}\n{page.url}\n\n{guarded}"


# ---------------------------------------------------------- read_document


class ReadDocumentArgs(BaseModel):
    source: str = Field(description="Путь к файлу в рабочей папке или ссылка http(s)")
    max_chars: int = Field(default=20000, ge=500, le=60000)


class ReadDocumentTool(Tool):
    name = "read_document"
    description = (
        "Читает PDF, Excel (xlsx), Word (docx), CSV — из рабочей папки или по ссылке. "
        "Возвращает текст с разметкой страниц и листов. "
        "Для обычного кода и текстовых файлов используй read_file."
    )
    Args = ReadDocumentArgs
    category = "read"
    timeout = 120.0

    async def run(self, args: ReadDocumentArgs, ctx: ToolContext) -> str:
        source = args.source.strip()

        if source.startswith(("http://", "https://")):
            data, name = await self._download(source)
        else:
            path = resolve_path(source, settings=ctx.settings)
            if not path.is_file():
                raise ToolError(f"Файл не найден: {source}")
            data, name = path.read_bytes(), path.name

        try:
            document = extract_document(data, name, max_chars=args.max_chars)
        except DocumentError as exc:
            raise ToolError(str(exc)) from exc

        header = f"# {document.title}"
        if document.parts > 1:
            header += f"\n[частей: {document.parts}]"
        if document.truncated:
            header += f"\n[показаны первые {args.max_chars} символов]"
        # Документ — тоже чужой текст (особенно скачанный): PDF и DOCX —
        # классический канал промпт-инъекций.
        guarded = await guard_external(ctx, document.text, source=source)
        return f"{header}\n\n{guarded}"

    async def _download(self, url: str) -> tuple[bytes, str]:
        from core.tools.builtin.web import get_http_client

        try:
            response = await get_http_client().get(url, timeout=60.0)
        except httpx.HTTPError as exc:
            raise ToolError(f"Не удалось скачать '{url}': {exc}") from exc
        if response.status_code >= 400:
            raise ToolError(f"Сервер вернул HTTP {response.status_code} для '{url}'.")

        name = Path(url.split("?")[0]).name or "документ"
        # Расширение решает, как разбирать файл, а в ссылке его может не быть
        # (например, arxiv.org/pdf/1706.03762 — «.03762» это не формат).
        # Тогда спрашиваем сервер и, в крайнем случае, смотрим на сами байты.
        if Path(name).suffix.lower() not in FORMATS:
            content_type = response.headers.get("content-type", "").lower()
            suffix = next(
                (
                    ext
                    for ext, marker in ((".pdf", "pdf"), (".xlsx", "spreadsheet"), (".docx", "word"))
                    if marker in content_type
                ),
                "",
            )
            if not suffix and response.content[:5] == b"%PDF-":
                suffix = ".pdf"
            if not suffix:
                if "html" in content_type:
                    raise ToolError(
                        f"По ссылке '{url}' обычная веб-страница, а не документ. "
                        "Читай её через fetch_url (или browse_page, если она на JavaScript)."
                    )
                raise ToolError(
                    f"Не удалось определить формат файла по ссылке '{url}' "
                    f"(сервер сообщил тип '{content_type or 'не указан'}'). "
                    "Скачайте его через download_file и укажите путь с расширением."
                )
            name += suffix
        return response.content, name


# --------------------------------------------------------- deep_research


class DeepResearchArgs(BaseModel):
    question: str = Field(description="Вопрос исследования, развёрнутой формулировкой")
    depth: Literal["quick", "standard", "deep"] = Field(
        default="standard",
        description=(
            "quick — 3 запроса и 5 источников (быстрая справка); "
            "standard — 4 и 9 (обычный обзор); "
            "deep — 6 и 14 (подробный разбор, идёт несколько минут)"
        ),
    )
    timelimit: Literal["d", "w", "m", "y"] | None = Field(
        default=None, description="Брать только свежие материалы: d/w/m/y"
    )
    focus: str = Field(default="", description="На чём заострить внимание (необязательно)")


class DeepResearchTool(Tool):
    name = "deep_research"
    description = (
        "Проводит исследование темы: разбивает вопрос на подзапросы, ищет по нескольким "
        "поисковикам, открывает источники (включая PDF и страницы на JavaScript), "
        "и возвращает обзор со ссылками вида [1], [2] и списком литературы. "
        "Занимает минуты. Для одного факта или одной страницы используй web_search и fetch_url."
    )
    Args = DeepResearchArgs
    category = "network"
    #: Глубокое исследование читает десятки страниц — лимит времени свой.
    timeout = 900.0

    def auto_verdict(self, args, ctx) -> str:  # type: ignore[override]
        """Исследование только читает открытые источники."""
        return "allow"

    async def run(self, args: DeepResearchArgs, ctx: ToolContext) -> str:
        model = ctx.settings.research_model or ctx.settings.default_model
        llm = OpenAICompatClient(settings=ctx.settings, model=model)
        researcher = DeepResearcher(llm, ctx.settings, emitter=ctx.emitter)

        try:
            report = await researcher.run(
                args.question,
                depth=args.depth,
                timelimit=args.timelimit,
                focus=args.focus,
            )
        except RuntimeError as exc:
            raise ToolError(str(exc)) from exc
        finally:
            await llm.aclose()

        preset = DEPTH_PRESETS[args.depth]
        header = (
            f"Исследование «{args.question}»\n"
            f"[запросов: {len(report.queries)}, найдено ссылок: {len(report.registry)}, "
            f"прочитано: {len(report.registry.read_sources())} из {preset['sources']}]"
        )
        # Отчёт собран из чужих источников — проверяем и его: инъекция могла
        # просочиться из страницы в конспект, а из конспекта в обзор.
        guarded = await guard_external(ctx, report.to_text(), source="глубокое исследование")
        return f"{header}\n\n{guarded}"
