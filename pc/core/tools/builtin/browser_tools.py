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
import time
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from core.browser_downloads import user_downloads_dir
from core.browser_session import PageView, get_agent_browser
from core.errors import ToolError
from core.events import ArtifactCreated, BrowserHandoff
from core.i18n import tr
from core.security.paths import resolve_path, safe_relpath
from core.security.permissions import decide
from core.tools.base import Tool, ToolContext, ToolResult
from core.tools.external_content import guard_external


async def _guarded(ctx: ToolContext, view: PageView) -> str:
    """Page text is untrusted — it goes through the prompt-injection guard."""
    return await guard_external(ctx, view.render(), source=view.url)


class _ReadOnly(Tool):
    """Looks at the page that is already open: nothing is sent anywhere, so no question."""

    category = "read"

    def auto_verdict(self, args, ctx) -> str:  # type: ignore[override]
        return "allow"


# ------------------------------------------------------------ navigate / read / find


class NavigateArgs(BaseModel):
    url: str = Field(description="Address (https:// is added if missing) or a search query")


class BrowserNavigateTool(_ReadOnly):
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


class BrowserReadTool(_ReadOnly):
    name = "browser_read"
    description = "Reads the active tab again: the fresh accessibility tree with [ref=…] handles."
    timeout = 45.0

    async def run(self, args, ctx: ToolContext) -> str:
        return await _guarded(ctx, await get_agent_browser().snapshot())


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


# ------------------------------------------------------------ interactions


class ClickArgs(BaseModel):
    ref: str = Field(description="The element's ref from the latest snapshot, e.g. 'e12'")
    double: bool = Field(default=False, description="Double-click")
    button: Literal["left", "right", "middle"] = Field(default="left")
    dialog: Literal["", "accept", "dismiss"] = Field(
        default="", description="How to answer a confirm/prompt dialog the click opens (default: dismiss)"
    )


class BrowserClickTool(Tool):
    name = "browser_click"
    description = "Clicks an element by its [ref=…] from the latest snapshot."
    Args = ClickArgs
    category = "network"
    dangerous = True
    timeout = 60.0

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.br_click", ref=args.ref)

    async def run(self, args: ClickArgs, ctx: ToolContext) -> str:
        async def action(page, target) -> None:
            if args.double:
                await target.dblclick(button=args.button, timeout=15000)
            else:
                await target.click(button=args.button, timeout=15000)

        return await _guarded(ctx, await get_agent_browser().act(action, args.ref, dialog=args.dialog, sandbox=ctx.settings))


class TypeArgs(BaseModel):
    ref: str = Field(description="Ref of the text field")
    text: str = Field(description="Text to enter")
    submit: bool = Field(default=False, description="Press Enter afterwards (send the form/search)")
    replace: bool = Field(default=True, description="Replace the field's current text (false = append)")


class BrowserTypeTool(Tool):
    name = "browser_type"
    description = "Types into a text field by ref; submit=true presses Enter after it."
    Args = TypeArgs
    category = "network"
    dangerous = True
    timeout = 60.0

    def approval_reason(self, args) -> str:  # type: ignore[override]
        text = args.text if len(args.text) <= 120 else args.text[:117] + "…"
        return tr("appr.br_type_submit" if args.submit else "appr.br_type", text=text, ref=args.ref)

    async def run(self, args: TypeArgs, ctx: ToolContext) -> str:
        async def action(page, target) -> None:
            if args.replace:
                try:
                    await target.fill(args.text, timeout=10000)
                except Exception:  # noqa: BLE001 — rich editors refuse fill(): type like a person
                    await target.click(timeout=10000)
                    await page.keyboard.press("Control+A")
                    await page.keyboard.type(args.text, delay=15)
            else:
                await target.click(timeout=10000)
                await page.keyboard.press("End")
                await page.keyboard.type(args.text, delay=15)
            if args.submit:
                await target.press("Enter")

        return await _guarded(ctx, await get_agent_browser().act(action, args.ref, sandbox=ctx.settings))


class PressArgs(BaseModel):
    key: str = Field(description="Key or combo: Enter, Escape, Tab, ArrowDown, PageDown, Control+A, …")
    ref: str = Field(default="", description="Focus this element first (optional)")


class BrowserPressTool(Tool):
    name = "browser_press"
    description = "Presses a key or a key combination in the page (optionally on an element)."
    Args = PressArgs
    category = "network"
    dangerous = True
    timeout = 30.0

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.br_press", combo=args.key)

    async def run(self, args: PressArgs, ctx: ToolContext) -> str:
        async def action(page, target) -> None:
            if target is not None:
                await target.press(args.key, timeout=10000)
            else:
                await page.keyboard.press(args.key)

        return await _guarded(ctx, await get_agent_browser().act(action, args.ref or None, sandbox=ctx.settings))


class SelectArgs(BaseModel):
    ref: str = Field(description="Ref of the <select> / combobox")
    values: list[str] = Field(description="Option labels or values to select")


class BrowserSelectTool(Tool):
    name = "browser_select"
    description = "Chooses option(s) in a drop-down list by ref."
    Args = SelectArgs
    category = "network"
    dangerous = True
    timeout = 30.0

    def approval_reason(self, args) -> str:  # type: ignore[override]
        return tr("appr.br_select", values=", ".join(args.values), ref=args.ref)

    async def run(self, args: SelectArgs, ctx: ToolContext) -> str:
        async def action(page, target) -> None:
            try:
                await target.select_option(label=args.values, timeout=10000)
            except Exception:  # noqa: BLE001 — the values may be option values, not labels
                await target.select_option(args.values, timeout=10000)

        return await _guarded(ctx, await get_agent_browser().act(action, args.ref, sandbox=ctx.settings))


class HoverArgs(BaseModel):
    ref: str = Field(description="Ref of the element to hover (opens hover menus/tooltips)")


class BrowserHoverTool(_ReadOnly):
    name = "browser_hover"
    description = "Moves the mouse over an element — for menus and tooltips that appear on hover."
    Args = HoverArgs
    timeout = 30.0

    async def run(self, args: HoverArgs, ctx: ToolContext) -> str:
        async def action(page, target) -> None:
            await target.hover(timeout=10000)

        return await _guarded(ctx, await get_agent_browser().act(action, args.ref, sandbox=ctx.settings))


class UploadArgs(BaseModel):
    ref: str = Field(description="Ref of the file input (or the button that opens the file chooser)")
    paths: list[str] = Field(description="Workspace files to upload")


class BrowserUploadTool(Tool):
    name = "browser_upload"
    description = "Attaches workspace files to a file-upload field of the page."
    Args = UploadArgs
    category = "network"
    dangerous = True
    timeout = 60.0

    def approval_reason(self, args: UploadArgs) -> str:
        return tr("appr.upload", paths=", ".join(args.paths))

    async def run(self, args: UploadArgs, ctx: ToolContext) -> str:
        files = [str(resolve_path(p, settings=ctx.settings, must_exist=True, must_be_file=True)) for p in args.paths]

        async def action(page, target) -> None:
            try:
                await target.set_input_files(files, timeout=5000)
            except Exception:  # noqa: BLE001 — a styled button: catch the chooser it opens
                async with page.expect_file_chooser(timeout=10000) as chooser:
                    await target.click()
                await (await chooser.value).set_files(files)

        return await _guarded(ctx, await get_agent_browser().act(action, args.ref, sandbox=ctx.settings))


class ScrollArgs(BaseModel):
    direction: Literal["down", "up"] = Field(default="down")
    amount: int = Field(default=3, ge=1, le=20, description="Wheel steps (~1/3 screen each)")
    ref: str = Field(default="", description="Scroll this element into view instead")


class BrowserScrollTool(_ReadOnly):
    name = "browser_scroll"
    description = "Scrolls the page (or brings an element into view) and returns the updated snapshot."
    Args = ScrollArgs
    timeout = 30.0

    async def run(self, args: ScrollArgs, ctx: ToolContext) -> str:
        async def action(page, target) -> None:
            if target is not None:
                await target.scroll_into_view_if_needed(timeout=10000)
            else:
                await page.mouse.wheel(0, 300 * args.amount * (1 if args.direction == "down" else -1))

        return await _guarded(ctx, await get_agent_browser().act(action, args.ref or None, sandbox=ctx.settings))


class WaitArgs(BaseModel):
    text: str = Field(default="", description="Wait until this text is on the page")
    gone: str = Field(default="", description="Or wait until this text disappears (e.g. 'Loading')")
    seconds: float = Field(default=0, ge=0, le=60, description="Or just wait this long")


class BrowserWaitTool(_ReadOnly):
    name = "browser_wait"
    description = "Waits for text to appear or disappear (or a fixed time), then returns the page."
    Args = WaitArgs
    timeout = 90.0

    async def run(self, args: WaitArgs, ctx: ToolContext) -> str:
        async def action(page, target) -> None:
            if args.text:
                await page.get_by_text(args.text).first.wait_for(state="visible", timeout=45000)
            elif args.gone:
                await page.get_by_text(args.gone).first.wait_for(state="hidden", timeout=45000)
            else:
                await asyncio.sleep(args.seconds or 1)

        return await _guarded(ctx, await get_agent_browser().act(action, sandbox=ctx.settings))


# ------------------------------------------------------------ screenshot


class ScreenshotArgs(BaseModel):
    question: str = Field(default="", description="What to look at (e.g. 'where is the pay button'); empty = describe")
    full_page: bool = Field(default=False, description="The whole page, not just the visible part")


class BrowserScreenshotTool(_ReadOnly):
    name = "browser_screenshot"
    description = (
        "Takes a screenshot of the active tab and attaches it to this conversation so you can see "
        "it yourself. Use when the snapshot is not enough (layout, images, charts, captcha)."
    )
    Args = ScreenshotArgs
    timeout = 60.0

    async def run(self, args: ScreenshotArgs, ctx: ToolContext) -> ToolResult:
        from core.tools.builtin.vision_tools import _queue_vision

        png = await get_agent_browser().screenshot_png(full_page=args.full_page)
        name = f".screenshots/browser-{int(time.time())}.png"
        destination = resolve_path(name, settings=ctx.settings)
        destination.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(destination.write_bytes, png)
        relative = safe_relpath(destination, ctx.settings).replace("\\", "/")
        await ctx.emitter(ArtifactCreated(path=relative, name=relative.split("/")[-1], kind="image", size_bytes=len(png)))
        prompt = args.question.strip() or f"What is on this page screenshot ({relative})? Describe what matters."
        _queue_vision(ctx, png, "image/png", prompt)
        return ToolResult(content=f"Screenshot {relative} is attached to the conversation as the next message.")


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
