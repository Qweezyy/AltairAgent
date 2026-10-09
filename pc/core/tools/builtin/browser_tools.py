"""Tools for the built-in browser (core/browser_session.py).

The page is described as Playwright's AI snapshot — an accessibility tree where every
element the agent can act on carries a [ref=eN] handle (the format Playwright MCP
uses). Actions take that ref, so the agent hits exactly the element it saw instead of
guessing selectors or pixels. Every action returns the updated page plus what changed
(navigation, a new tab, a dialog, a download).

Safety split: reading, finding, scrolling, hovering, waiting and switching tabs only
look (auto-allowed); clicking, typing, keys, selecting and uploading can submit forms,
so they go through the normal approval of network actions. Downloads always land in
quarantine; moving one out is a separate, approved step.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from abc import abstractmethod
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from core.browser_downloads import user_downloads_dir
from core.browser_session import (
    LOG_KEEP,
    SNAPSHOT_LIMIT,
    PageView,
    Step,
    explain_failure,
    get_agent_browser,
    png_size,
)
from core.errors import ToolError
from core.events import ArtifactCreated, BrowserHandoff
from core.i18n import tr
from core.logging_setup import get_logger
from core.security.paths import resolve_path, safe_relpath
from core.security.permissions import decide
from core.tools.base import Tool, ToolContext, ToolResult
from core.tools.external_content import guard_external

logger = get_logger("browser_tools")


async def _guarded(ctx: ToolContext, view: PageView) -> str:
    """Page text is untrusted — it goes through the prompt-injection guard."""
    return await guard_external(ctx, view.render(), source=view.url)


class _ReadOnly(Tool):
    """Looks at the page that is already open: nothing is sent anywhere, so no question."""

    category = "read"

    def auto_verdict(self, args, ctx) -> str:  # type: ignore[override]
        return "allow"


# ------------------------------------------------------------ steps


class _StepTool(Tool):
    """An interaction with the page. It runs on its own (and returns the page after it) or as
    one step of browser_batch, where the page is read only once, at the end."""

    category = "network"
    dangerous = True
    timeout = 60.0

    @abstractmethod
    def step(self, args: Any, ctx: ToolContext) -> Step:
        """The action this tool performs, for running it alone or in a batch."""

    async def run(self, args: Any, ctx: ToolContext) -> str:
        s = self.step(args, ctx)
        view = await get_agent_browser().act(s.action, s.ref, dialog=s.dialog, sandbox=ctx.settings,
                                            label=s.label)
        return await _guarded(ctx, view)


class _LookingStep(_StepTool):
    """A step that only looks or moves the pointer: nothing is sent, so nothing is asked."""

    category = "read"
    dangerous = False

    def auto_verdict(self, args, ctx) -> str:  # type: ignore[override]
        return "allow"


def _label(name: str, args: BaseModel) -> str:
    """How a batch step is named in its log: the tool and the arguments that are not defaults."""
    shown = []
    for key, value in args.model_dump(exclude_defaults=True).items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        shown.append(f"{key}={text if len(text) <= 60 else text[:57] + '…'}")
    return f"{name.removeprefix('browser_')} {' '.join(shown)}".strip()


def _point(x: float | None, y: float | None) -> tuple[float, float] | None:
    return (x, y) if x is not None and y is not None else None


# ------------------------------------------------------------ navigate / read / find / text


class NavigateArgs(BaseModel):
    url: str = Field(description="Address (https:// is added if missing) or a search query")


class BrowserNavigateTool(_LookingStep):
    category = "network"  # it goes out to the internet: normal network approval
    name = "browser_navigate"
    description = (
        "Opens a URL (or searches the words) in the active tab of the built-in browser — a real "
        "browser the user sees in the app, with a persistent profile and logins. Returns the page "
        "as an accessibility tree with [ref=…] handles for the other browser tools. Local pages "
        "inside the workspace open too (file:// URL or a path like dist/index.html) — for testing "
        "the sites and apps you build; for apps with a dev server use http://localhost:…"
    )
    Args = NavigateArgs
    timeout = 90.0

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.br_open", url=args.url)

    def step(self, args: NavigateArgs, ctx: ToolContext) -> Step:
        b = get_agent_browser()

        async def action(page, target) -> None:
            await b.open_in(page, args.url, ctx.settings)

        return Step(_label(self.name, args), action)

    async def run(self, args: NavigateArgs, ctx: ToolContext) -> str:
        try:
            view = await get_agent_browser().navigate(args.url, ctx.settings)
        except ToolError as exc:
            hint = _network_hint(args.url)
            raise ToolError(f"{exc}{hint}") from exc
        note = _network_note(view.url)
        if note:
            view.notes.append(note)
        return await _guarded(ctx, view)


def _host_of(url: str) -> str:
    from urllib.parse import urlsplit

    return (urlsplit(url if "://" in url else f"https://{url}").hostname or "").lower()


def _network_note(url: str) -> str:
    """Which network the page came through — so the agent can tell a geo-block from a bug."""
    from core.browser_net import get_proxy

    net = get_proxy()
    host = _host_of(url)
    if net is None or not host or not net.topology.vpn:
        return ""
    last = net.recent.get(net.rules.normalize(host))
    if not last:
        return ""
    route = "directly, bypassing the VPN" if last["route"] == "direct" else "through the VPN"
    return f"network: {host} was loaded {route} ({last['why']}); change it with browser_network"


def _network_hint(url: str) -> str:
    from core.browser_net import get_proxy

    net = get_proxy()
    if net is None or not net.topology.vpn:
        return ""
    return (
        " The user has a VPN on: if the site blocks VPN users or is blocked without a VPN, "
        "switch its route with browser_network (mode direct or vpn) and open it again."
    )


class ReadArgs(BaseModel):
    ref: str = Field(default="", description="Read only this element's subtree (a form, a dialog, a list)")
    interactive: bool = Field(
        default=False, description="Only elements you can act on (and headings): a long page in a fraction of the size")
    depth: int | None = Field(default=None, ge=1, le=50, description="Only this many levels of the tree")
    max_chars: int = Field(default=SNAPSHOT_LIMIT, ge=1000, le=60_000, description="Cut the result at this size")


class BrowserReadTool(_ReadOnly):
    name = "browser_read"
    description = (
        "Reads the active tab again: the fresh accessibility tree with [ref=…] handles. Narrow it "
        "on big pages: ref= reads one part (a form, a dialog, a results list), interactive=true "
        "lists only what can be clicked or filled. For the page's text use browser_text."
    )
    Args = ReadArgs
    timeout = 45.0
    max_output_chars = 62_000

    async def run(self, args: ReadArgs, ctx: ToolContext) -> str:
        view = await get_agent_browser().read(ref=args.ref, interactive=args.interactive, depth=args.depth,
                                              limit=args.max_chars)
        return await _guarded(ctx, view)


class FindArgs(BaseModel):
    query: str = Field(description="Words to look for in element names and page text, e.g. 'checkout button'")


class BrowserFindTool(_ReadOnly):
    name = "browser_find"
    description = (
        "Finds elements on a long page: returns the snapshot lines (with refs) that contain all the "
        "words. Cheaper than reading the whole page again."
    )
    Args = FindArgs
    timeout = 30.0

    async def run(self, args: FindArgs, ctx: ToolContext) -> str:
        found = await get_agent_browser().find(args.query)
        page = await get_agent_browser().page()
        return await guard_external(ctx, found, source=page.url)


class TextArgs(BaseModel):
    ref: str = Field(default="", description="Only this element's text (an article, a table, a comment)")
    start: int = Field(default=0, ge=0, description="Character to start from (to read a long page in parts)")
    max_chars: int = Field(default=20_000, ge=500, le=100_000)


class BrowserTextTool(_ReadOnly):
    name = "browser_text"
    description = (
        "The readable text of the active tab — its main content when the page marks one (article, "
        "main), else the whole page; or of one element by ref. Several times cheaper than the "
        "accessibility tree when you need to read, not act: articles, results, documentation, "
        "messages. A long text comes in parts (start=…)."
    )
    Args = TextArgs
    timeout = 45.0
    max_output_chars = 102_000

    async def run(self, args: TextArgs, ctx: ToolContext) -> str:
        b = get_agent_browser()
        text, total = await b.page_text(ref=args.ref, start=args.start, limit=args.max_chars)
        page = await b.page()
        head = f"URL: {page.url}\nTitle: {await page.title()}\n\n"
        end = args.start + len(text)
        if not total:
            body = "(the page has no text yet — it may still be loading, or it is drawn on a canvas)"
        elif args.start >= total:
            body = f"(the text is {total} characters long; start={args.start} is past its end)"
        else:
            body = text + (f"\n\n[characters {args.start}–{end} of {total}; next part: start={end}]"
                           if end < total else "")
        return await guard_external(ctx, head + body, source=page.url)


# ------------------------------------------------------------ interactions


class ClickArgs(BaseModel):
    ref: str = Field(default="", description="The element's ref from the latest snapshot, e.g. 'e12'")
    x: float | None = Field(default=None, description="Or click at this point (CSS pixels of a browser_screenshot)")
    y: float | None = Field(default=None)
    double: bool = Field(default=False, description="Double-click")
    button: Literal["left", "right", "middle"] = Field(default="left")
    modifiers: list[Literal["Alt", "Control", "Meta", "Shift"]] = Field(
        default_factory=list, description="Keys held during the click (Control = open a link in a new tab)")
    dialog: Literal["", "accept", "dismiss"] = Field(
        default="", description="How to answer a confirm/prompt dialog the click opens (default: dismiss)"
    )

    @model_validator(mode="after")
    def _where(self) -> ClickArgs:
        if not self.ref and _point(self.x, self.y) is None:
            raise ValueError("give ref, or both x and y")
        return self


class BrowserClickTool(_StepTool):
    name = "browser_click"
    description = (
        "Clicks an element by its [ref=…] from the latest snapshot — or at x/y of a screenshot, for "
        "what the tree does not show (a canvas, a map, a custom widget)."
    )
    Args = ClickArgs

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.br_click", ref=args.ref or f"x={args.x:g}, y={args.y:g}")

    def step(self, args: ClickArgs, ctx: ToolContext) -> Step:
        count = 2 if args.double else 1
        mods = list(args.modifiers)

        async def action(page, target) -> None:
            if target is None:
                for key in mods:
                    await page.keyboard.down(key)
                try:
                    await page.mouse.click(args.x, args.y, button=args.button, click_count=count)
                finally:
                    for key in reversed(mods):
                        await page.keyboard.up(key)
            else:
                await target.click(button=args.button, click_count=count, modifiers=mods or None, timeout=8000)

        return Step(_label(self.name, args), action, args.ref or None, args.dialog)


class TypeArgs(BaseModel):
    ref: str = Field(description="Ref of the text field")
    text: str = Field(description="Text to enter")
    submit: bool = Field(default=False, description="Press Enter afterwards (send the form/search)")
    replace: bool = Field(default=True, description="Replace the field's current text (false = append)")
    slowly: bool = Field(
        default=False, description="Type key by key (for fields that react to each key: suggestions, masks)")


class BrowserTypeTool(_StepTool):
    name = "browser_type"
    description = (
        "Types into a text field or a rich-text editor by ref and checks the field shows the text "
        "(a warning says when it does not); submit=true presses Enter after it."
    )
    Args = TypeArgs

    def approval_reason(self, args) -> str:  # type: ignore[override]
        text = args.text if len(args.text) <= 120 else args.text[:117] + "…"
        return tr("appr.br_type_submit" if args.submit else "appr.br_type", text=text, ref=args.ref)

    def step(self, args: TypeArgs, ctx: ToolContext) -> Step:
        async def action(page, target) -> str:
            note = await _enter_text(page, target, args.text, replace=args.replace, slowly=args.slowly)
            if args.submit:
                await page.keyboard.press("Enter")
            return note

        return Step(_label(self.name, args), action, args.ref)


# The element that really takes the text for `el`. Rich-text editors (Trumbowyg, Summernote, …)
# keep the labelled <textarea> as a hidden copy (1x1 px or invisible, its value is the editor's
# HTML) next to the visible contenteditable box the site reads: text set on the copy shows up in
# the page tree yet the editor and its form never see it. Live: the agent spent ~100 steps on a
# Kwork form this way.
_EDITABLE_FOR = """(el) => {
  const visible = (n) => {
    const r = n.getBoundingClientRect(), s = getComputedStyle(n);
    return r.width > 4 && r.height > 4 && s.visibility !== "hidden" && s.display !== "none" && +s.opacity > 0.05;
  };
  if (el.isContentEditable) {
    let host = el;  // the editing host, not a paragraph inside it
    while (host.parentElement && host.parentElement.isContentEditable) host = host.parentElement;
    return host;
  }
  if (!["TEXTAREA", "INPUT"].includes(el.tagName)) {
    const inner = el.querySelector('textarea, input:not([type=hidden]), [contenteditable=""], [contenteditable=true]');
    return inner || el;
  }
  const copy = !visible(el) || /^\\s*<(div|p|br|span)\\b/i.test(el.value || "");
  if (!copy) return el;
  for (let n = el.parentElement, depth = 0; n && depth < 4; n = n.parentElement, depth++) {
    const boxes = [...n.querySelectorAll('[contenteditable=""], [contenteditable=true]')].filter(visible);
    if (boxes.length === 1) return boxes[0];
    if (boxes.length > 1) break;
  }
  return el;
}"""

# What the field shows now: the value of an input, the text of an editor.
_SHOWN_TEXT = """(el) => el.isContentEditable ? el.innerText : (el.value ?? el.textContent ?? "")"""

# A paste the way a browser delivers one: editors that rebuild their content on paste take it.
_PASTE = """(el, text) => {
  const data = new DataTransfer();
  data.setData("text/plain", text);
  el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
}"""


def _same_text(shown: str, wanted: str) -> bool:
    """The field holds the text: whitespace aside, and formatting aside for masked fields
    ("9 000" for 9000, "+7 (999) 123-45-67" for a phone)."""
    if "".join(shown.split()) == "".join(wanted.split()):
        return True
    letters = lambda s: re.sub(r"\W", "", s)  # noqa: E731
    return bool(letters(wanted)) and letters(shown) == letters(wanted)


async def _enter_text(page: Any, target: Any, text: str, *, replace: bool = True, slowly: bool = False) -> str:
    """Puts `text` into a field the way a person would, and checks the field took it.

    Plain inputs get fill() (fast; it fires the input events frameworks listen to). Editors get
    real keyboard input: focus, select all, insert the text as typed input, and a final key so
    sites that count or validate on key up see it. If the field then shows something else, a
    paste and then key-by-key typing are tried. Returns a note for the model ("" when it went
    as planned)."""
    handle = await target.element_handle(timeout=8000)
    field = (await handle.evaluate_handle(_EDITABLE_FOR)).as_element() or handle
    redirected = not await field.evaluate("(a, b) => a === b", handle)
    editor = await field.evaluate("(el) => el.isContentEditable")
    before = await field.evaluate(_SHOWN_TEXT) if not replace else ""
    wanted = text if replace else before + text
    notes = ["typed into the visible editor (the labelled field is its hidden copy)"] if redirected else []

    if not editor and replace and not slowly:
        try:
            await field.fill(text, timeout=8000)
            if _same_text(await field.evaluate(_SHOWN_TEXT), wanted):
                return "; ".join(notes)
        except Exception:  # noqa: BLE001 - masked or custom inputs refuse fill(): type like a person
            logger.debug("fill refused; typing instead", exc_info=True)

    async def focus(select_all: bool) -> None:
        await field.click(timeout=8000)
        if select_all:
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Delete")
        else:
            await page.keyboard.press("Control+End")

    await focus(replace)
    if slowly:
        await page.keyboard.type(text, delay=20)
    else:
        for number, line in enumerate(text.split("\n")):
            if number:
                # A new line, not "send": Enter submits chat boxes, Shift+Enter breaks the line.
                await page.keyboard.press("Shift+Enter" if editor else "Enter")
            if line:
                await page.keyboard.insert_text(line)
    await page.keyboard.press("End")  # a real key up for sites that count/validate on it
    shown = await field.evaluate(_SHOWN_TEXT)
    if _same_text(shown, wanted):
        return "; ".join(notes)

    for how in ("paste", "keys"):
        await focus(True)
        if how == "paste":
            await field.evaluate(_PASTE, wanted)
        else:
            await page.keyboard.type(wanted, delay=10)
        await page.keyboard.press("End")
        shown = await field.evaluate(_SHOWN_TEXT)
        if _same_text(shown, wanted):
            notes.append("the editor only took the text " + ("as a paste" if how == "paste" else "typed key by key"))
            return "; ".join(notes)
    preview = shown if len(shown) <= 200 else shown[:197] + "…"
    notes.append(f"WARNING: the field shows «{preview}», not the text you gave — check it before sending")
    return "; ".join(notes)


class PressArgs(BaseModel):
    key: str = Field(description="Key or combo: Enter, Escape, Tab, ArrowDown, PageDown, Control+A, …")
    ref: str = Field(default="", description="Focus this element first (optional)")


class BrowserPressTool(_StepTool):
    name = "browser_press"
    description = "Presses a key or a key combination in the page (optionally on an element)."
    Args = PressArgs
    timeout = 30.0

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.br_press", combo=args.key)

    def step(self, args: PressArgs, ctx: ToolContext) -> Step:
        async def action(page, target) -> None:
            if target is not None:
                await target.press(args.key, timeout=8000)
            else:
                await page.keyboard.press(args.key)

        return Step(_label(self.name, args), action, args.ref or None)


class SelectArgs(BaseModel):
    ref: str = Field(description="Ref of the <select> / combobox")
    values: list[str] = Field(description="Option labels or values to select")


class BrowserSelectTool(_StepTool):
    name = "browser_select"
    description = "Chooses option(s) in a drop-down list by ref."
    Args = SelectArgs
    timeout = 30.0

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.br_select", values=", ".join(args.values), ref=args.ref)

    def step(self, args: SelectArgs, ctx: ToolContext) -> Step:
        async def action(page, target) -> None:
            await _choose(target, args.values)

        return Step(_label(self.name, args), action, args.ref)


async def _choose(target: Any, values: list[str]) -> None:
    try:
        await target.select_option(label=values, timeout=8000)
    except Exception:  # noqa: BLE001 — the values may be option values, not labels
        await target.select_option(values, timeout=8000)


class FieldValue(BaseModel):
    ref: str = Field(description="Ref of a text field, checkbox, radio, switch or <select>")
    value: str | bool | float = Field(description="Text, a number, true/false for a checkbox, or an option's label")


class FillArgs(BaseModel):
    fields: list[FieldValue] = Field(min_length=1, max_length=60, description="The fields and their values")


# What kind of field an element is, to set it the right way.
_FIELD_KIND = """(el) => {
  const type = (el.getAttribute("type") || "").toLowerCase();
  const role = el.getAttribute("role") || "";
  if (el.tagName === "SELECT") return "select";
  if (["checkbox", "radio"].includes(type) || ["checkbox", "radio", "switch", "menuitemcheckbox"].includes(role))
    return "check";
  // A drop-down built from divs (vue-select, react-select…): opened by a click, picked from a list.
  if (["combobox", "listbox"].includes(role) && !["INPUT", "TEXTAREA"].includes(el.tagName)) return "combo";
  return "text";
}"""


async def _pick(page: Any, box: Any, value: str) -> None:
    """Chooses `value` in a drop-down made of divs: open it, click the option (typing to filter
    the list when the option is not shown)."""
    await box.click(timeout=8000)
    for attempt in range(2):
        options = page.get_by_role("option", name=value)
        try:
            await options.first.wait_for(state="visible", timeout=2500)
        except Exception:  # noqa: BLE001 - not listed yet: filter the list by typing
            if attempt == 0:
                await page.keyboard.insert_text(value)
                continue
            raise ToolError(f"No option «{value}» appeared in the list; read the list and pick by ref.") from None
        await options.first.click(timeout=8000)
        return


class BrowserFillTool(_StepTool):
    name = "browser_fill"
    description = (
        "Fills in a whole form in one call: text fields, checkboxes/radios/switches (true/false) and "
        "drop-downs (an option's label), each by ref. Sets the values directly, so it is fast and "
        "exact; it does not submit — click the button (or use browser_batch to do both)."
    )
    Args = FillArgs

    def approval_reason(self, args) -> str:  # type: ignore[override]
        shown = ", ".join(f"{f.ref}={str(f.value)[:40]}" for f in args.fields[:12])
        return tr("appr.br_fill", fields=shown + (" …" if len(args.fields) > 12 else ""))

    def step(self, args: FillArgs, ctx: ToolContext) -> Step:
        b = get_agent_browser()

        async def action(page, target) -> str:
            notes = []
            for item in args.fields:
                field = await b.locate(item.ref)
                kind = await field.evaluate(_FIELD_KIND)
                try:
                    if kind == "check":
                        on = item.value if isinstance(item.value, bool) else str(item.value).strip().lower() in (
                            "true", "1", "yes", "on", "checked")
                        await field.set_checked(on, timeout=8000)
                    elif kind == "select":
                        await _choose(field, [str(item.value)])
                    elif kind == "combo":
                        await _pick(page, field, str(item.value))
                    else:
                        value = item.value
                        if isinstance(value, float) and value.is_integer():
                            value = int(value)
                        note = await _enter_text(page, field, str(value).lower() if isinstance(value, bool) else str(value))
                        if note:
                            notes.append(f"[ref={item.ref}]: {note}")
                except ToolError as exc:
                    raise ToolError(f"Field [ref={item.ref}]: {exc}") from exc
                except Exception as exc:  # noqa: BLE001
                    raise ToolError(f"Field [ref={item.ref}]: {explain_failure(exc)}") from exc
            return "\n".join([f"filled {len(args.fields)} field(s)", *notes])

        return Step(_label(self.name, args), action)


class HoverArgs(BaseModel):
    ref: str = Field(default="", description="Ref of the element to hover (opens hover menus/tooltips)")
    x: float | None = Field(default=None, description="Or hover this point (CSS pixels of a screenshot)")
    y: float | None = Field(default=None)

    @model_validator(mode="after")
    def _where(self) -> HoverArgs:
        if not self.ref and _point(self.x, self.y) is None:
            raise ValueError("give ref, or both x and y")
        return self


class BrowserHoverTool(_LookingStep):
    name = "browser_hover"
    description = "Moves the mouse over an element (or a point) — for menus and tooltips that appear on hover."
    Args = HoverArgs
    timeout = 30.0

    def step(self, args: HoverArgs, ctx: ToolContext) -> Step:
        async def action(page, target) -> None:
            if target is None:
                await page.mouse.move(args.x, args.y)
            else:
                await target.hover(timeout=8000)

        return Step(_label(self.name, args), action, args.ref or None)


class UploadArgs(BaseModel):
    ref: str = Field(description="Ref of the file input, or of the button/area that opens the file chooser")
    paths: list[str] = Field(description="Workspace files to upload")


# The file input a ref stands for: the element itself, the input of its <label>, one inside it,
# or the only one near it or on the page. Sites hide the real input behind a styled button.
_FIND_FILE_INPUT = """(el) => {
  const isFile = (n) => n && n.tagName === "INPUT" && n.type === "file";
  if (isFile(el)) return el;
  const label = el.tagName === "LABEL" ? el : el.closest("label");
  if (label && isFile(label.control)) return label.control;
  const inner = el.querySelector && el.querySelector('input[type="file"]');
  if (inner) return inner;
  for (let n = el.parentElement, depth = 0; n && depth < 4; n = n.parentElement, depth++) {
    const near = n.querySelectorAll('input[type="file"]');
    if (near.length === 1) return near[0];
  }
  const all = document.querySelectorAll('input[type="file"]');
  return all.length === 1 ? all[0] : null;
}"""

# Many editors create the input only on click (input.click() / showPicker()) and never put it in
# the page. Catch it instead of letting the OS file dialog open, which the agent cannot operate.
_CATCH_FILE_INPUT = """() => {
  if (window.__altairUpload) return;
  const proto = HTMLInputElement.prototype, saved = { click: proto.click, showPicker: proto.showPicker };
  const box = window.__altairUpload = { input: null, saved };
  const grab = (orig) => function (...args) {
    if (this.type === "file") { box.input = this; return undefined; }
    return orig.apply(this, args);
  };
  proto.click = grab(saved.click);
  if (saved.showPicker) proto.showPicker = grab(saved.showPicker);
}"""

_RELEASE_FILE_INPUT = """() => {
  const box = window.__altairUpload; if (!box) return;
  HTMLInputElement.prototype.click = box.saved.click;
  if (box.saved.showPicker) HTMLInputElement.prototype.showPicker = box.saved.showPicker;
  delete window.__altairUpload;
}"""

# A caught input that is not in the page: attach it (hidden) so files can be set on it; its own
# change handler still fires, which is what the site listens to.
_CAUGHT_INPUT = """() => {
  const box = window.__altairUpload, input = box && box.input;
  if (input && !input.isConnected) { input.style.display = "none"; document.body.appendChild(input); }
  return input || null;
}"""


class BrowserUploadTool(_StepTool):
    name = "browser_upload"
    description = (
        "Attaches workspace files to a file-upload field of the page. Give the ref of the input or of "
        "the upload button itself - do not click the button first: that opens the system file dialog, "
        "which you cannot operate."
    )
    Args = UploadArgs

    def approval_reason(self, args: UploadArgs) -> str:
        return tr("appr.upload", paths=", ".join(args.paths))

    def step(self, args: UploadArgs, ctx: ToolContext) -> Step:
        files = [str(resolve_path(p, settings=ctx.settings, must_exist=True, must_be_file=True)) for p in args.paths]

        async def action(page, target) -> None:
            if target is None:
                raise ToolError("Give the ref of the file input or of the upload button.")
            await upload_files(page, target, files)

        return Step(_label(self.name, args), action, args.ref)


async def upload_files(page: Any, target: Any, files: list[str]) -> str:
    """Sets `files` on the upload field `target` stands for, without the OS file dialog.

    The embedded browser is driven over CDP, where the "file chooser opened" event does not
    reach us, so waiting for it (the usual way) times out. The input is filled directly
    instead. Returns how it was found (for tests and the log).
    """
    found = (await target.evaluate_handle(_FIND_FILE_INPUT)).as_element()
    if found is not None:
        await found.set_input_files(files, timeout=10000)
        return "input"
    await page.evaluate(_CATCH_FILE_INPUT)
    try:
        await target.click(timeout=10000)
        caught = None
        for _ in range(20):  # the site may open it a moment after the click
            caught = (await page.evaluate_handle(_CAUGHT_INPUT)).as_element()
            if caught is not None:
                break
            await asyncio.sleep(0.1)
        if caught is not None:
            await caught.set_input_files(files, timeout=10000)
            return "caught"
    finally:
        await page.evaluate(_RELEASE_FILE_INPUT)
    # Last resort, for browsers that do report the chooser (the Chrome fallback).
    try:
        async with page.expect_file_chooser(timeout=3000) as chooser:
            await target.click(timeout=5000)
        await (await chooser.value).set_files(files)
        return "chooser"
    except Exception as exc:  # noqa: BLE001 - reported to the agent with a way forward
        raise ToolError(
            "No file field found for this element and clicking it opened none. Look for another "
            "upload button or area (a snapshot may show an input of type file), or ask the user."
        ) from exc


class ScrollArgs(BaseModel):
    direction: Literal["down", "up", "left", "right"] = Field(default="down")
    amount: int = Field(default=3, ge=1, le=20, description="Wheel steps (~1/3 screen each)")
    ref: str = Field(default="", description="Scroll this element into view instead")
    x: float | None = Field(
        default=None, description="Scroll the area under this point (a list or panel with its own scrollbar)")
    y: float | None = Field(default=None)


class BrowserScrollTool(_LookingStep):
    name = "browser_scroll"
    description = (
        "Scrolls the page — or the panel under x/y (lists, chats, maps with their own scrollbar) — or "
        "brings an element into view by ref, and returns the updated page."
    )
    Args = ScrollArgs
    timeout = 30.0

    def step(self, args: ScrollArgs, ctx: ToolContext) -> Step:
        async def action(page, target) -> None:
            if target is not None:
                await target.scroll_into_view_if_needed(timeout=8000)
                return
            point = _point(args.x, args.y)
            if point is not None:
                await page.mouse.move(*point)
            else:
                size = page.viewport_size or {"width": 1280, "height": 800}
                await page.mouse.move(size["width"] / 2, size["height"] / 2)
            step = 300 * args.amount * (1 if args.direction in ("down", "right") else -1)
            await page.mouse.wheel(step if args.direction in ("left", "right") else 0,
                                   step if args.direction in ("down", "up") else 0)

        return Step(_label(self.name, args), action, args.ref or None)


class WaitArgs(BaseModel):
    text: str = Field(default="", description="Wait until this text is on the page")
    gone: str = Field(default="", description="Or wait until this text disappears (e.g. 'Loading')")
    url: str = Field(default="", description="Or wait until the address contains this")
    seconds: float = Field(default=0, ge=0, le=60, description="Or just wait this long")
    timeout: float = Field(default=15, ge=1, le=60, description="Give up after this many seconds")


class BrowserWaitTool(_LookingStep):
    name = "browser_wait"
    description = "Waits for text to appear or disappear, for the address to change (or a fixed time), then returns the page."
    Args = WaitArgs
    timeout = 90.0

    def step(self, args: WaitArgs, ctx: ToolContext) -> Step:
        limit = args.timeout * 1000

        async def action(page, target) -> None:
            try:
                if args.text:
                    await page.get_by_text(args.text).first.wait_for(state="visible", timeout=limit)
                elif args.gone:
                    await page.get_by_text(args.gone).first.wait_for(state="hidden", timeout=limit)
                elif args.url:
                    await page.wait_for_url(lambda u: args.url in u, timeout=limit, wait_until="commit")
                else:
                    await asyncio.sleep(args.seconds or 1)
            except Exception as exc:  # noqa: BLE001 - only a timeout gets the friendly wording
                if "Timeout" not in type(exc).__name__:
                    raise
                what = args.text or args.gone or args.url
                raise ToolError(f"Waited {args.timeout:g} s: '{what}' did not "
                                f"{'appear' if args.text else 'go away' if args.gone else 'show in the address'}.") from exc

        return Step(_label(self.name, args), action)


# ------------------------------------------------------------ JavaScript


class JsArgs(BaseModel):
    code: str = Field(description=(
        "JavaScript to run in the page. The value of the last expression is the result; "
        "top-level await works. Example: [...document.querySelectorAll('tr')].map(r => r.innerText)"))


class BrowserJsTool(_StepTool):
    name = "browser_js"
    description = (
        "Runs JavaScript in the active tab, like the DevTools console: top-level await works and the "
        "value of the last expression is returned (data as JSON, elements as their HTML). Use it to "
        "read exact data in one call (table rows, field values, prices, all links), to check state "
        "(what the app stored, which element has focus, computed styles) and for what the other tools "
        "cannot do. The script runs as the page, with the user's logins: never put secrets in it and "
        "never send page data anywhere."
    )
    Args = JsArgs
    max_output_chars = 30_000

    def approval_reason(self, args: JsArgs) -> str:  # type: ignore[override]
        code = args.code if len(args.code) <= 600 else args.code[:597] + "…"
        return tr("appr.br_js", url=get_agent_browser().active_url() or "?", code=code)

    def step(self, args: JsArgs, ctx: ToolContext) -> Step:
        b = get_agent_browser()

        async def action(page, target) -> str:
            return await b.run_js(page, args.code)

        return Step(_label(self.name, args), action)

    async def run(self, args: JsArgs, ctx: ToolContext) -> str:
        s = self.step(args, ctx)
        view = await get_agent_browser().act(s.action, sandbox=ctx.settings, look=False, label=s.label)
        notes = "".join(f"\nNote: {n}" for n in view.notes)
        return await guard_external(ctx, f"Result: {view.output}{notes}", source=view.url)


# ------------------------------------------------------------ batch


class BatchStep(BaseModel):
    tool: str = Field(description="navigate, click, type, press, select, fill, hover, scroll, wait, js or upload")
    args: dict[str, Any] = Field(default_factory=dict, description="That tool's arguments, as for a single call")


class BatchArgs(BaseModel):
    steps: list[BatchStep] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def _known(self) -> BatchArgs:
        for number, step in enumerate(self.steps, 1):
            tool = step_tool(step.tool)
            if tool is None:
                raise ValueError(f"step {number}: '{step.tool}' cannot be a step (use: {', '.join(_step_names())})")
            try:
                tool.Args.model_validate(step.args)
            except ValidationError as exc:
                problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'args'}: {e['msg']}" for e in exc.errors())
                raise ValueError(f"step {number} ({tool.name}): {problems}") from exc
        return self


class BrowserBatchTool(Tool):
    name = "browser_batch"
    description = (
        "Several browser actions in one call, the page read once at the end: fill a form and send "
        "it, open a page and click through a menu, type and wait for the result. Use it whenever you "
        "can tell two or more steps ahead — each step saves a full round trip. Steps run in order "
        "and stop at the first failure; the reply lists every step and shows the page."
    )
    Args = BatchArgs
    category = "network"
    dangerous = True
    timeout = 300.0
    max_output_chars = 62_000

    def _plan(self, args: BatchArgs) -> list[tuple[_StepTool, BaseModel]]:
        plan = []
        for step in args.steps:
            tool = step_tool(step.tool)
            assert tool is not None  # checked by BatchArgs
            plan.append((tool, tool.Args.model_validate(step.args)))
        return plan

    def auto_verdict(self, args: BatchArgs, ctx: ToolContext) -> str:  # type: ignore[override]
        verdicts = {tool.auto_verdict(parsed, ctx) for tool, parsed in self._plan(args)}
        return "allow" if verdicts == {"allow"} else "ask"

    def approval_reason(self, args: BatchArgs) -> str:  # type: ignore[override]
        steps = "\n".join(f"{n}. {tool.approval_reason(parsed)}" for n, (tool, parsed) in enumerate(self._plan(args), 1))
        return tr("appr.br_batch", steps=steps)

    async def run(self, args: BatchArgs, ctx: ToolContext) -> str:
        steps = [tool.step(parsed, ctx) for tool, parsed in self._plan(args)]
        view = await get_agent_browser().run_steps(steps, ctx.settings)
        return await _guarded(ctx, view)


_STEP_TOOLS: dict[str, _StepTool] = {}


def step_tool(name: str) -> _StepTool | None:
    """The tool a batch step names ("click" or "browser_click")."""
    if not _STEP_TOOLS:
        for tool in (BrowserNavigateTool(), BrowserClickTool(), BrowserTypeTool(), BrowserPressTool(),
                     BrowserSelectTool(), BrowserFillTool(), BrowserHoverTool(), BrowserScrollTool(),
                     BrowserWaitTool(), BrowserJsTool(), BrowserUploadTool()):
            _STEP_TOOLS[tool.name] = tool
    key = name.strip().lower()
    return _STEP_TOOLS.get(key if key.startswith("browser_") else f"browser_{key}")


def _step_names() -> list[str]:
    step_tool("click")
    return [n.removeprefix("browser_") for n in _STEP_TOOLS]


# ------------------------------------------------------------ console and requests


class ConsoleArgs(BaseModel):
    pattern: str = Field(default="", description="Only messages containing this text")
    only_errors: bool = Field(default=False, description="Only errors (and uncaught exceptions)")
    limit: int = Field(default=50, ge=1, le=300, description="The newest this many")
    clear: bool = Field(default=False, description="Empty the log after reading (to see only what comes next)")


class BrowserConsoleTool(_ReadOnly):
    name = "browser_console"
    description = (
        "What the active tab printed to its console: logs, warnings, errors and uncaught exceptions "
        "(collected since the tab was opened). The first place to look when a page you build or "
        "test misbehaves."
    )
    Args = ConsoleArgs
    timeout = 20.0

    async def run(self, args: ConsoleArgs, ctx: ToolContext) -> str:
        b = get_agent_browser()
        page = await b.page()
        entries = b.console_log(page)
        # Page loads and the agent's own actions stay in any filter: they tell which step a
        # message came from.
        marks = ("page", "action")
        if args.only_errors:
            entries = [e for e in entries if e["type"] in ("error", "assert", *marks)]
        if args.pattern:
            entries = [e for e in entries if e["type"] in marks or args.pattern.lower() in e["text"].lower()]
        if args.clear:
            b.clear_logs(page)
        messages = sum(1 for e in entries if e["type"] not in marks)
        if not messages:
            return "No console messages" + (" match." if args.pattern or args.only_errors else " yet.")
        shown = entries[-args.limit:]
        lines = [f"{_clock(e['at'])} [{e['type']}] {e['text'][:800]}" + (f"  ({e['where']})" if e["where"] else "")
                 for e in shown]
        head = f"Now {_clock(time.time())}. Oldest first; [page] = a page load, [action] = your action"
        head += f" ({len(shown)} of {len(entries)} lines):\n" if len(entries) > len(shown) else ":\n"
        return await guard_external(ctx, head + "\n".join(lines), source=page.url)


def _clock(at: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(at)) + f".{int(at % 1 * 10)}"


class RequestsArgs(BaseModel):
    url_pattern: str = Field(default="", description="Only requests whose address contains this")
    only_failed: bool = Field(default=False, description="Only failed requests and error statuses (4xx/5xx)")
    api_only: bool = Field(default=False, description="Only fetch/XHR calls (what an app asks its server)")
    limit: int = Field(default=50, ge=1, le=300)
    id: str = Field(default="", description="Show this request in full: its body and the response body")
    clear: bool = Field(default=False, description="Empty the log after reading")


class BrowserRequestsTool(_ReadOnly):
    name = "browser_requests"
    description = (
        "The network requests of the active tab: method, status, type, time and address; id=… shows "
        "one request with its body and the response body. For debugging an app (which API call "
        "failed and why) and for taking data straight from the JSON a site loads."
    )
    Args = RequestsArgs
    timeout = 30.0
    max_output_chars = 40_000

    async def run(self, args: RequestsArgs, ctx: ToolContext) -> str:
        b = get_agent_browser()
        page = await b.page()
        entries = b.request_log(page)
        if args.id:
            entry = next((e for e in entries if e["id"] == args.id), None)
            if entry is None:
                return f"No request '{args.id}' in the log (it keeps the newest {LOG_KEEP})."
            return await guard_external(ctx, await _request_details(entry), source=page.url)
        if args.api_only:
            entries = [e for e in entries if e["type"] in ("fetch", "xhr")]
        if args.only_failed:
            entries = [e for e in entries if e["failure"] or (e["status"] or 0) >= 400]
        if args.url_pattern:
            entries = [e for e in entries if args.url_pattern in e["url"]]
        if args.clear:
            b.clear_logs(page)
        if not entries:
            return "No requests match." if args.url_pattern or args.only_failed or args.api_only else "No requests yet."
        shown = entries[-args.limit:]
        lines = []
        for e in shown:
            status = str(e["status"]) if e["status"] is not None else (e["failure"] or "pending")
            if e["failure"] and e["status"] is not None:
                status += f" ({e['failure']})"  # a 404 whose body the page never read ends "aborted"
            took = f" {e['ms']}ms" if e["ms"] is not None else ""
            url = e["url"] if len(e["url"]) <= 300 else e["url"][:297] + "…"
            lines.append(f"[{e['id']}] {e['method']} {status} {e['type']}{took} {url}")
        head = f"{len(shown)} of {len(entries)} request(s), oldest first:\n" if len(entries) > len(shown) else ""
        return await guard_external(ctx, head + "\n".join(lines), source=page.url)


async def _request_details(entry: dict[str, Any]) -> str:
    lines = [f"{entry['method']} {entry['url']}", f"type: {entry['type']}",
             f"status: {entry['failure'] or entry['status'] or 'pending'}"]
    request = entry.get("request")
    body = None
    try:
        body = request.post_data if request is not None else None
    except Exception:  # noqa: BLE001 - binary bodies cannot be shown as text
        body = "(binary)"
    if body:
        lines.append(f"request body: {body[:4000]}")
    response = entry.get("response")
    if response is None:
        lines.append("no response (yet)")
        return "\n".join(lines)
    headers = await response.all_headers()
    lines.append(f"content-type: {headers.get('content-type', '?')}")
    try:
        text = await response.text()
    except Exception as exc:  # noqa: BLE001 - the browser no longer holds the body (or it is binary)
        lines.append(f"response body unavailable: {str(exc).splitlines()[0][:200]}")
        return "\n".join(lines)
    lines.append(f"response body ({len(text)} chars):\n{text[:30_000]}")
    return "\n".join(lines)


# ------------------------------------------------------------ screenshot


class ScreenshotArgs(BaseModel):
    question: str = Field(default="", description="What to look at (e.g. 'where is the pay button'); empty = describe")
    full_page: bool = Field(default=False, description="The whole page, not just the visible part")
    ref: str = Field(default="", description="Only this element")
    region: list[float] = Field(
        default_factory=list, description="Zoom into [x0, y0, x1, y1] of the visible page (CSS pixels): small text, icons")

    @model_validator(mode="after")
    def _region(self) -> ScreenshotArgs:
        if self.region and len(self.region) != 4:
            raise ValueError("region is [x0, y0, x1, y1]")
        return self


class BrowserScreenshotTool(_ReadOnly):
    name = "browser_screenshot"
    description = (
        "Takes a screenshot of the active tab and attaches it to this conversation so you can see "
        "it yourself. Use when the snapshot is not enough (layout, images, charts, captcha). The "
        "picture is in CSS pixels: a point on it is the x/y for browser_click. region= zooms in."
    )
    Args = ScreenshotArgs
    timeout = 60.0

    async def run(self, args: ScreenshotArgs, ctx: ToolContext) -> ToolResult:
        from core.tools.builtin.vision_tools import _queue_vision

        region = tuple(args.region) if args.region else None
        png = await get_agent_browser().screenshot_png(full_page=args.full_page, ref=args.ref, region=region)
        name = f".screenshots/browser-{int(time.time() * 1000)}.png"
        destination = resolve_path(name, settings=ctx.settings)
        destination.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(destination.write_bytes, png)
        relative = safe_relpath(destination, ctx.settings).replace("\\", "/")
        await ctx.emitter(ArtifactCreated(path=relative, name=relative.split("/")[-1], kind="image", size_bytes=len(png)))
        prompt = args.question.strip() or f"What is on this page screenshot ({relative})? Describe what matters."
        _queue_vision(ctx, png, "image/png", prompt)
        width, height = png_size(png)
        if region is not None:
            scale = width / max(region[2] - region[0], 1)
            where = (f"a zoom (x{scale:.1f}) of the region {region[0]:g},{region[1]:g}–{region[2]:g},{region[3]:g}: "
                     f"page x = {region[0]:g} + picture x / {scale:.1f} (same for y)")
        elif args.ref:
            where = f"element [ref={args.ref}] only"
        elif args.full_page:
            where = "the whole page; browser_click x/y are of the visible part, so scroll there first"
        else:
            where = "page coordinates in CSS pixels — use them as x/y for browser_click"
        return ToolResult(content=f"Screenshot {relative} ({width}x{height}, {where}) is attached to the "
                                  "conversation as the next message.")


# ------------------------------------------------------------ tabs


class TabsArgs(BaseModel):
    action: Literal["list", "new", "select", "close", "back", "forward", "reload"] = Field(default="list")
    tab: str = Field(default="", description="Tab id for select/close (from action=list)")
    url: str = Field(default="", description="Address for action=new")


class BrowserTabsTool(Tool):
    name = "browser_tabs"
    description = (
        "Browser tabs and history: list, new (optional url), select / close (by tab id), back, "
        "forward, reload. The user sees the same tabs in the browser panel; list marks which "
        "tabs you opened. Work in your own tabs, keep several open to compare pages, and close "
        "yours when done — closing the user's tabs asks them first."
    )
    Args = TabsArgs
    category = "network"
    timeout = 60.0

    def auto_verdict(self, args, ctx) -> str:  # type: ignore[override]
        # Closing a tab the user opened may lose their work; the agent's own tabs are its to tidy.
        if args.action == "close" and not get_agent_browser().opened_by_agent(args.tab):
            return "ask"
        return "allow"

    def approval_reason(self, args) -> str:  # type: ignore[override]
        if args.action == "close":
            return tr("appr.br_tab_close", tab=args.tab)
        if args.action == "new":
            return tr("appr.br_tab_new", url=args.url or "about:blank")
        return tr("appr.br_tabs", action=args.action)

    async def run(self, args: TabsArgs, ctx: ToolContext) -> str:
        b = get_agent_browser()
        if args.action == "list":
            tabs = await b.tabs()
            if not tabs:
                return "No tabs are open."
            return "\n".join(
                f"{'*' if t['active'] else ' '} [{t['id']}] {t['title']} — {t['url']}"
                + (" (sign-in popup)" if t.get("popup") else " (yours)" if t.get("by_agent") else " (user's)")
                for t in tabs
            ) + "\n(* = active. Close the tabs you no longer need; the user's tabs ask first.)"
        if args.action in ("back", "forward", "reload"):
            return await _guarded(ctx, await b.history(args.action, ctx.settings))
        if args.action == "new":
            await b.new_tab(args.url or None, sandbox=ctx.settings)
        elif args.action in ("select", "close"):
            if not args.tab:
                return f"action={args.action} needs a tab id (see action=list)."
            if args.action == "select":
                await b.select_tab(args.tab)
            else:
                await b.close_tab(args.tab)
                return "Tab closed.\n" + "\n".join(f"[{t['id']}] {t['title']}" for t in await b.tabs())
        return await _guarded(ctx, await b.snapshot())


# ------------------------------------------------------------ hand-off


class HandoffArgs(BaseModel):
    reason: str = Field(description="What exactly the user should do (solve a captcha, sign in, confirm 2FA)")
    hint: str = Field(default="", description="Optional details")


class BrowserHandoffTool(Tool):
    name = "browser_handoff"
    description = (
        "Last resort when you cannot pass a browser step yourself (captcha, 2FA, manual sign-in): "
        "pauses the run, asks the user in the browser panel and waits until they finish it in the "
        "same browser and press Done. Returns the fresh page."
    )
    Args = HandoffArgs
    category = "read"
    timeout = None

    async def run(self, args: HandoffArgs, ctx: ToolContext) -> str:
        b = get_agent_browser()
        try:
            await b.page()
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"The browser is not available for a hand-off: {exc}") from exc
        waiters = ctx.scratch.setdefault("_browser_handoff", {})
        request_id = uuid.uuid4().hex[:10]
        waiter: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        waiters[request_id] = waiter
        await ctx.emitter(BrowserHandoff(request_id=request_id, reason=args.reason, hint=args.hint))
        try:
            await waiter
        finally:
            waiters.pop(request_id, None)
        body = await _guarded(ctx, await b.snapshot())
        return "The user did it. The page now:\n\n" + body


# ------------------------------------------------------------ downloads


class DownloadsArgs(BaseModel):
    action: Literal["list", "move", "delete"] = Field(default="list")
    id: str = Field(default="", description="Download id (from action=list)")
    destination: Literal["workspace", "downloads"] = Field(
        default="workspace", description="Where to move: the workspace or the user's Downloads folder"
    )
    folder: str = Field(default="", description="Sub-folder of the workspace for destination=workspace")


class BrowserDownloadsTool(Tool):
    name = "browser_downloads"
    description = (
        "Files downloaded in the browser. They land in a quarantine folder and are checked "
        "(Microsoft Defender, disguised executables, risky types). list shows each file with its "
        "verdict; move puts a checked file into the workspace (to work with it) or the user's "
        "Downloads; delete removes it. Risky or unchecked files need the user's confirmation."
    )
    Args = DownloadsArgs
    # Listing and deleting only touch the quarantine, so the tool itself asks nothing;
    # a move out of quarantine asks in run() (see below).
    category = "read"
    timeout = 120.0

    def approval_reason(self, args: DownloadsArgs) -> str:
        try:
            item = get_agent_browser().downloads.get(args.id)
        except KeyError:
            return tr("appr.dl_move_unknown", id=args.id)
        where = tr("appr.where_downloads" if args.destination == "downloads" else "appr.where_workspace")
        warn = tr("appr.dl_warn", status=tr(f"dl.{item.status}"), detail=item.detail) if item.needs_confirmation else ""
        return tr("appr.dl_move", name=item.name, where=where) + warn

    async def run(self, args: DownloadsArgs, ctx: ToolContext) -> ToolResult:
        store = get_agent_browser().downloads
        if args.action == "list":
            items = [d for d in store.list() if d["status"] not in ("deleted",)]
            if not items:
                return ToolResult(content="No downloads.")
            lines = [
                f"[{d['id']}] {d['name']} ({d['size']} bytes) — {d['status']}: {d['detail']}"
                + (f" → {d['moved_to']}" if d.get("moved_to") else "")
                for d in items[:30]
            ]
            return ToolResult(content="\n".join(lines))
        if not args.id:
            return ToolResult.fail("This action needs the download id (see action=list).")
        try:
            if args.action == "delete":
                item = await store.delete(args.id)
                return ToolResult(content=f"Deleted {item.name}.")
            if args.destination == "downloads":
                target_dir = user_downloads_dir()
            else:
                target_dir = resolve_path(args.folder or ".", settings=ctx.settings)
            item = store.get(args.id)
            # A move writes into the user's folders: asked like any file edit (the mode
            # may allow it), and a risky or unchecked file never leaves quarantine
            # without the user's yes — in any mode, "no confirmations" included.
            must_ask = item.needs_confirmation or decide("edit", ctx.settings.approval_mode) != "allow"
            if must_ask and not await self._ask_approval(args, ctx):
                return ToolResult.fail("The user did not allow moving this file out of quarantine.")
            item = await store.move(args.id, Path(target_dir), confirmed=True)
        except KeyError:
            return ToolResult.fail(f"No download '{args.id}'.")
        except (ValueError, PermissionError) as exc:
            return ToolResult.fail(str(exc))
        shown = item.moved_to
        if args.destination == "workspace":
            shown = safe_relpath(Path(item.moved_to), ctx.settings)
        return ToolResult(content=f"Moved {item.name} to {shown} (marked as downloaded from the internet).")

# ------------------------------------------------------------ network (VPN or direct)


class NetworkArgs(BaseModel):
    action: Literal["status", "set"] = Field(default="status", description="status: what goes where; set: route a site")
    site: str = Field(default="", description="Site for action=set, e.g. 'example.com' (covers its subdomains)")
    mode: Literal["auto", "direct", "vpn"] = Field(
        default="auto",
        description="auto: direct first, the VPN if unreachable; direct: bypass the VPN; vpn: through the VPN",
    )


class BrowserNetworkTool(Tool):
    name = "browser_network"
    description = (
        "How the built-in browser reaches sites when the user's VPN is on. By default each site is "
        "tried directly (bypassing the VPN) and goes through the VPN only if it does not answer. "
        "Use set when a site says it blocks VPNs/foreign visitors (mode=direct) or is blocked in "
        "the user's country (mode=vpn), then reload the page. status shows the network and routes."
    )
    Args = NetworkArgs
    category = "edit"
    timeout = 15.0

    def auto_verdict(self, args, ctx) -> str:  # type: ignore[override]
        return "allow"  # only changes which network the app's own browser uses

    def approval_reason(self, args) -> str:  # type: ignore[override]
        if args.action == "set":
            return tr("appr.br_net", site=args.site, mode=tr(f"appr.br_net_{args.mode}"))
        return tr("appr.br_net_status")

    async def run(self, args: NetworkArgs, ctx: ToolContext) -> ToolResult:
        from core.browser_net import get_proxy

        net = get_proxy()
        if net is None:
            return ToolResult.fail("The browser network proxy is not running; the browser uses the system network.")
        if args.action == "set":
            if not args.site.strip():
                return ToolResult.fail("action=set needs a site, e.g. site='example.com'.")
            site = await asyncio.to_thread(net.rules.set, args.site, args.mode)
            net.forget(site)
            return ToolResult(content=f"{site}: {args.mode}. Reload the page (browser_tabs action=reload) to apply.")
        topo = net.topology
        if not topo.vpn:
            lines = ["No VPN detected: every site loads directly."]
        else:
            lines = [
                f"VPN on ({topo.vpn_kind}{', adapter ' + topo.vpn_adapter if topo.vpn_adapter else ''}); "
                f"direct traffic leaves via {topo.physical_adapter or 'the physical network'} ({topo.physical_ip or '?'})."
            ]
        lines.append(f"Default for sites without a rule: {net.default_mode}.")
        if net.rules.sites:
            lines.append("Rules: " + ", ".join(f"{k}={v}" for k, v in sorted(net.rules.sites.items())))
        if net.rules.learned:
            lines.append("Learned as blocked directly (go via VPN): " + ", ".join(sorted(net.rules.learned)[:20]))
        recent = list(net.recent.items())[-10:]
        if recent:
            lines.append("Recent: " + "; ".join(f"{h} → {r['route']}" for h, r in reversed(recent)))
        return ToolResult(content="\n".join(lines))
