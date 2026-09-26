"""Инлайн-канвас в ленте ответа: SVG-графика, интерактивный HTML-виджет, вложение.

Паритет с телефонным агентом (show_graphic / show_interactive / attach_file). SVG и
HTML+JS рендерятся в песочнице (iframe srcdoc без same-origin — как превью-панель),
поэтому скрипты виджета изолированы от данных приложения. attach_file прикрепляет
готовый файл из рабочей папки прямо в ответ.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from core.errors import PathNotAllowed
from core.events import ShowFile, ShowHtml
from core.gen_ui import UISpecError, render_ui
from core.security.paths import resolve_path, safe_relpath
from core.tools.base import Tool, ToolContext, ToolResult

#: Тип вложения по расширению — интерфейсу для выбора превью.
_IMAGE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
_VIDEO = {".mp4", ".webm", ".mov", ".mkv"}
_AUDIO = {".mp3", ".wav", ".ogg", ".m4a", ".flac"}
_DATA = {".csv", ".tsv", ".xlsx", ".json", ".parquet"}


def _attach_kind(suffix: str) -> str:
    s = suffix.lower()
    if s in _IMAGE:
        return "image"
    if s in _VIDEO:
        return "video"
    if s in _AUDIO:
        return "audio"
    if s in _DATA:
        return "data"
    return "file"


def _wrap_svg(svg: str) -> str:
    """Оборачивает SVG в самодостаточный HTML: прозрачный фон, масштаб по ширине."""
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<style>html,body{margin:0;padding:0;background:transparent}"
        "svg{max-width:100%;height:auto;display:block;margin:0 auto}</style>"
        f"</head><body>{svg}</body></html>"
    )


class ShowGraphicArgs(BaseModel):
    svg: str = Field(description="Полный валидный код <svg …>…</svg> (используй viewBox для масштаба)")
    caption: str = Field(default="", description="Короткая подпись под графикой")


class ShowGraphicTool(Tool):
    name = "show_graphic"
    description = (
        "Рисует векторную графику в ответе: диаграмму, схему, график, иллюстрацию. Передай "
        "ПОЛНЫЙ валидный <svg>…</svg> с viewBox. Для интерактива (кнопки, анимация, ввод) — "
        "show_interactive."
    )
    Args = ShowGraphicArgs
    category = "read"
    timeout = 15.0

    async def run(self, args: ShowGraphicArgs, ctx: ToolContext) -> str | ToolResult:
        svg = args.svg.strip()
        if "<svg" not in svg:
            return ToolResult.fail("нужен полный код <svg>…</svg>")
        await ctx.emitter(ShowHtml(html=_wrap_svg(svg), caption=args.caption.strip(), kind="graphic"))
        return "Показал графику пользователю в ленте ответа."


class ShowInteractiveArgs(BaseModel):
    html: str = Field(description="Самодостаточный HTML-документ (можно с <style>/<script>)")
    caption: str = Field(default="", description="Короткая подпись")


class ShowInteractiveTool(Tool):
    name = "show_interactive"
    description = (
        "Показывает ИНТЕРАКТИВНЫЙ виджет (генеративный UI) прямо в ленте ответа: HTML+CSS+JS — "
        "кнопки, ползунки, вкладки, формы, анимация, калькуляторы, интерактивные графики, дашборды, "
        "мини-игры. Передай самодостаточный HTML; весь CSS/JS встраивай инлайн, внешние сетевые "
        "ресурсы заблокированы песочницей.\n"
        "Возможности среды: (1) высота подстраивается автоматически — не задавай фиксированную. "
        "(2) Можно встраивать медиа из рабочей папки: <img src=\"/files/путь\">, "
        "<video src=\"/files/путь\" controls>, <audio src=\"/files/путь\" controls> (путь — как в "
        "рабочей папке). (3) Двусторонняя связь: вызови window.sendPrompt('текст') (например по "
        "клику), чтобы отправить сообщение агенту и продолжить диалог из виджета. "
        "Вызывай сколько угодно раз и в любом месте ответа, чередуя с текстом."
    )
    Args = ShowInteractiveArgs
    category = "read"
    timeout = 15.0

    async def run(self, args: ShowInteractiveArgs, ctx: ToolContext) -> str | ToolResult:
        html = args.html.strip()
        if not html:
            return ToolResult.fail("пустой html")
        await ctx.emitter(ShowHtml(html=html, caption=args.caption.strip(), kind="interactive"))
        return "Показал интерактивный виджет пользователю в ленте ответа."


class ShowUIArgs(BaseModel):
    blocks: list[dict[str, Any]] = Field(
        description=(
            "Список блоков UI в порядке показа. Каждый блок — объект {\"type\": <тип>, …поля}. "
            "Контейнеры (card, columns, tabs, accordion) вкладывают блоки через \"children\"."
        )
    )
    title: str = Field(default="", description="Необязательный заголовок над интерфейсом")
    caption: str = Field(default="", description="Короткая подпись под виджетом")


class ShowUITool(Tool):
    name = "show_ui"
    description = (
        "Собирает АККУРАТНЫЙ интерфейс в ответе из строгой библиотеки готовых компонентов "
        "(единый стиль, авто-тема свет/тьма, адаптивность) — предпочтительнее сырого HTML "
        "(show_interactive) для дашбордов, карточек, таблиц, метрик, форм и графиков.\n"
        "blocks — список {\"type\":…, …}. Компоненты и их поля:\n"
        "• heading{text,level:1-4,align?,icon?} • text{text,muted?,align?} • badge{text,variant,solid?,icon?} "
        "• chips{items:[str|{text,variant,icon}]} • callout{text,title?,variant,icon?} • divider{text?}\n"
        "• stat{label,value,delta?,trend:up|down|flat,icon?,variant?,sparkline?:[числа],spark_color?} • stats{items:[stat…]} "
        "• keyvalue{items:[{key,value}]} • list{items:[…],ordered?} • rating{value,max?,show_value?}\n"
        "• table{columns:[…],rows:[[…],…],dense?} • progress{value,max?,label?} • gauge{value,max?,label?,center?,variant?}\n"
        "• image{src,alt?,caption?} • gallery{images:[{src,caption?}]} • video{src,caption?} • audio{src}  (src: /files/<путь>, http(s) или data:)\n"
        "• chart{kind:bar|hbar|line|pie|donut, data:[{label,value}], title?, area?} — ОДНА серия; "
        "МНОГО серий: chart{kind:bar|line, labels:[…], series:[{name,values:[числа],color?}], title?, area?} "
        "(сгруппированные столбцы / несколько линий с легендой)\n"
        "• button{label,prompt?,url?,variant,size:sm|md|lg,outline?,icon?} • buttons{items:[button…]} "
        "• field{name,label?,kind:text|number|textarea|select,options?,placeholder?} "
        "• form{fields:[field…],submit_label?,submit_prompt?}\n"
        "• hero{title,subtitle?,icon?,variant?,actions:[button…]} • steps{items:[{title,text?,status:done|active|pending|failed}]}\n"
        "• code{code,language?} • quote{text,author?} • avatar{name,subtitle?,src?,initials?} • spacer{size:xs|sm|md|lg|xl} • icon{name,variant?,size?}\n"
        "• card{title?,icon?,variant:plain|outlined|elevated|tinted,accent?,footer?,children:[…]} "
        "• columns{children:[[…],[…]]} • tabs{items:[{label,children:[…]}]} • accordion{items:[{title,children:[…]}]}\n"
        "variant: default|info|success|warn|danger|accent. icon (имена): check,x,alert,info,star,bolt,clock,user,users,"
        "folder,file,chart,trend-up,trend-down,arrow-right,search,settings,heart,mail,calendar,code,play,download,globe,"
        "sparkles,shield,rocket. Инлайн в тексте: **жирный**, *курсив*, `код`, [ссылка](url).\n"
        "ИНТЕРАКТИВ: у button/form поле prompt/submit_prompt — при клике/отправке агенту "
        "уходит это сообщение (форма добавляет значения полей), и диалог продолжается. "
        "Вызывай сколько угодно раз и в любом месте ответа, чередуя с текстом."
    )
    Args = ShowUIArgs
    category = "read"
    timeout = 15.0

    async def run(self, args: ShowUIArgs, ctx: ToolContext) -> str | ToolResult:
        if not args.blocks:
            return ToolResult.fail("blocks пуст — передай хотя бы один блок.")
        try:
            html = render_ui(args.blocks, title=args.title.strip())
        except UISpecError as exc:
            return ToolResult.fail(f"Некорректный UI: {exc}")
        await ctx.emitter(ShowHtml(html=html, caption=args.caption.strip(), kind="interactive"))
        return f"Показал интерфейс из {len(args.blocks)} блок(ов) в ленте ответа."


class AttachFileArgs(BaseModel):
    path: str = Field(description="Путь к файлу в рабочей папке")
    caption: str = Field(default="", description="Короткая подпись")


class AttachFileTool(Tool):
    name = "attach_file"
    description = (
        "Прикрепляет ГОТОВЫЙ файл прямо в ответ — ЛЮБОЙ тип и в любом количестве, в любом месте "
        "ответа (чередуй с текстом). Фото, видео и аудио проигрываются прямо в чате (не просто "
        "ссылка): картинка показывается, видео/аудио — со встроенным плеером; документ, архив, "
        "таблица — чипом со скачиванием. Вызывай отдельно для каждого файла. path — путь в рабочей "
        "папке."
    )
    Args = AttachFileArgs
    category = "read"
    timeout = 15.0

    async def run(self, args: AttachFileArgs, ctx: ToolContext) -> str | ToolResult:
        try:
            path = resolve_path(args.path, settings=ctx.settings, must_exist=True, must_be_file=True)
        except (PathNotAllowed, OSError) as exc:
            return ToolResult.fail(str(exc))
        relative = safe_relpath(path, ctx.settings).replace("\\", "/")
        await ctx.emitter(
            ShowFile(
                path=relative,
                name=path.name,
                caption=args.caption.strip(),
                kind=_attach_kind(path.suffix),
                size_bytes=path.stat().st_size,
            )
        )
        return f"Прикрепил файл «{relative}» к ответу."
