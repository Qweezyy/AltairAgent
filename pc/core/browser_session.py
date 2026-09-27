"""The built-in browser that the user and the agent share.

Two hosts, one interface:

* **embedded** (the Altair desktop app): tabs are real WebView2 (Edge/Chromium)
  webviews inside the app window, placed over the browser panel by the Tauri shell
  (desktop/src-tauri/src/browser.rs). Nothing is streamed: the page renders natively,
  so it is sharp and reacts to the user instantly — the way Claude Desktop's browser
  pane works. The UI owns the tab strip; we ask it to open/select/close tabs through a
  relay over the app's WebSocket.
* **chrome** (no desktop shell — e.g. the UI opened in a browser, CLI runs): a real
  Chrome (or Edge) started as an ordinary program with its own profile and a hidden
  window; the panel shows it as a live screencast.

Either way the browser is a normal, non-automated browser — no --enable-automation,
no Playwright launch, navigator.webdriver stays false — and the agent attaches to it
over the DevTools protocol, the way a debugger would. Pages are described to the model
with Playwright's AI snapshot (the accessibility tree with [ref=eN] handles, the format
Playwright MCP uses), and actions target those refs.
"""

from __future__ import annotations

import asyncio
import difflib
import os
import re
import shutil
import socket
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, unquote, urlparse
from urllib.request import url2pathname

from core.browser_downloads import DownloadStore
from core.errors import PathNotAllowed, ToolError
from core.logging_setup import get_logger
from core.security.paths import resolve_path
from core.settings import Settings, get_settings

logger = get_logger("browser_session")

#: Search engine for bare queries typed into the address bar.
_SEARCH_URL = "https://www.google.com/search?q={q}"

#: How much of the page snapshot goes to the model in one reply.
SNAPSHOT_LIMIT = 14_000
#: Marks the two shapes of a page the model gets (the history tells them apart by these).
FULL_PAGE_MARK = "Page (accessibility tree"
PAGE_CHANGES_MARK = "Page changes since your previous look"
#: A diff larger than this share of the page is not worth it: the whole page is sent.
DIFF_MAX_SHARE = 0.5
#: Link targets longer than this lose their query string (ad and tracking links run to 1000+
#: characters; the agent clicks by ref, the address only tells it where a link goes).
URL_KEEP = 150

# Roles whose clickability is implied, so "[cursor=pointer]" on them says nothing.
_CLICKABLE_ROLES = frozenset({"link", "button", "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "option",
                              "checkbox", "radio", "switch", "textbox", "combobox", "searchbox", "slider",
                              "treeitem", "spinbutton"})
_UNNAMED_WRAPPER = re.compile(r"\s*- generic \[ref=[^\]]+\]:")
_ROLE = re.compile(r"\s*- (\w+)")
_URL_LINE = re.compile(r"^(\s*- /url: )(\S+)(.*)$")


def _short_url(url: str) -> str:
    base, sep, _ = url.partition("?")
    base = base.split("#", 1)[0]
    if len(base) > 100:
        return base[:100] + "…"
    return base + ("?…" if sep else "…")


def compact_tree(tree: str) -> str:
    """The accessibility tree without what the model never uses.

    Unnamed ``generic`` wrappers (layout divs) go, their children stay; "[cursor=pointer]"
    goes where the role already says the element is clickable (a clickable generic keeps
    it); long tracking URLs keep their address and lose the query. Refs are untouched.
    """
    out: list[str] = []
    for line in tree.splitlines():
        if _UNNAMED_WRAPPER.fullmatch(line):
            continue
        if " [cursor=pointer]" in line:
            role = _ROLE.match(line)
            if role and role.group(1) in _CLICKABLE_ROLES:
                line = line.replace(" [cursor=pointer]", "")
        url = _URL_LINE.match(line)
        if url and len(url.group(2)) > URL_KEEP:
            line = url.group(1) + _short_url(url.group(2)) + url.group(3)
        out.append(line)
    return "\n".join(out)


def page_changes(old: str, new: str) -> str | None:
    """What changed between two snapshots of one page, as a compact diff.

    "" means nothing visible changed; None means the change is too big for a diff to help
    (the caller sends the whole page).
    """
    a, b = old.splitlines(), new.splitlines()
    lines: list[str] = []
    for line in difflib.unified_diff(a, b, lineterm="", n=1):
        if line.startswith(("---", "+++")):
            continue
        lines.append("…" if line.startswith("@@") else line)
    text = "\n".join(lines)
    if not text:
        return ""
    return None if len(text) > DIFF_MAX_SHARE * max(len(new), 1) else text


def looks_like_url(text: str) -> bool:
    """True when the address-bar text is a URL, not a search query.

    A real browser treats "example.com" and "localhost:3000" as addresses but
    "погода в москве" or "openai" as searches. Rule: an explicit scheme, or a
    single token whose host part is localhost, an IP, or has a dotted domain.
    """
    s = text.strip()
    if not s:
        return False
    if s.startswith(("http://", "https://", "file://", "about:")):
        return True
    if any(c.isspace() for c in s):
        return False
    host = s.split("/", 1)[0]
    if ":" in host:  # strip :port
        head, _, port = host.rpartition(":")
        if head and port.isdigit():
            host = head
    if host in ("localhost",):
        return True
    return "." in host and not host.startswith(".") and not host.endswith(".")


def normalize_target(text: str) -> str:
    """Turn address-bar text into a URL: real address → https://, else a search."""
    s = text.strip()
    if s.startswith(("http://", "https://", "file://", "about:")):
        return s
    if looks_like_url(s):
        return "https://" + s
    return _SEARCH_URL.format(q=quote(s))


_LOCAL_PAGE_SUFFIXES = (".html", ".htm", ".xhtml", ".svg", ".pdf")


def local_target(text: str, settings: Settings | None = None) -> str | None:
    """A file:// URL for a local page the agent may open, None when `text` is not local.

    The agent tests its own sites and apps straight from disk, so file:// is allowed —
    but only inside the workspace sandbox, like every other file tool; otherwise the
    browser would read any file on the disk. Accepts file:// URLs, absolute paths and
    workspace-relative paths to a page ("dist/index.html"). `settings` is the run's own
    sandbox: a chat without a chosen folder works in its private folder, not the global one.
    """
    s = text.strip().strip('"').strip("'")
    if not s:
        return None
    fragment = ""
    if s.lower().startswith("file:"):
        parsed = urlparse(s)
        raw = url2pathname(parsed.path) if os.name == "nt" else unquote(parsed.path)
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            raw = "//" + parsed.netloc + raw  # a network share: the sandbox check decides
        fragment = f"#{parsed.fragment}" if parsed.fragment else ""
        query = f"?{parsed.query}" if parsed.query else ""
    else:
        is_abs = (len(s) > 2 and s[1] == ":" and s[2] in "\\/") or s.startswith(("\\\\", "/"))
        if "://" in s or (":" in s and not is_abs):
            return None  # http://…, about:…, localhost:3000/…
        base, _, rest = s.partition("#")
        if not is_abs:
            # "dist/index.html" is local only when it exists; "example.com/page.html" is a site.
            if not base.lower().endswith(_LOCAL_PAGE_SUFFIXES):
                return None
            try:
                if not resolve_path(base, settings=settings).is_file():
                    return None
            except PathNotAllowed:
                return None
        raw, fragment, query = base, (f"#{rest}" if rest else ""), ""
    try:
        path = resolve_path(raw, settings=settings, must_exist=True)
    except PathNotAllowed as exc:
        raise ToolError(
            f"{exc} The browser opens local files only inside the workspace; "
            "serve other projects with a dev server (http://localhost:…)."
        ) from exc
    return path.as_uri() + query + fragment


def _local_url_allowed(url: str, settings: Settings | None = None) -> bool:
    """For pages the agent reached by clicking: file:// must stay inside the sandbox too."""
    if not url.lower().startswith("file:"):
        return True
    try:
        return local_target(url, settings) is not None
    except ToolError:
        return False


# --------------------------------------------------------------------- hosts

_EMBEDDED: dict[str, Any] = {"port": 0, "dir": None}


def configure_embedded(port: int, directory: Path) -> None:
    """Called at startup when the Tauri shell hosts the browser (main.py)."""
    _EMBEDDED["port"] = int(port)
    _EMBEDDED["dir"] = Path(directory)


def browser_dir() -> Path:
    configured = _EMBEDDED.get("dir")
    if configured:
        return Path(configured)
    return get_settings().data_dir / "browser"


class HostRelay(Protocol):
    """The UI side of the embedded browser (a WebSocket connection of the app window)."""

    async def request(self, op: str, **params: Any) -> dict[str, Any]: ...


def find_browser_executable() -> str | None:
    """A real browser to start as an ordinary program: Chrome first, then Edge."""
    candidates: list[str] = []
    if os.name == "nt":
        try:
            import winreg

            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for exe in ("chrome.exe", "msedge.exe"):
                    try:
                        key = winreg.OpenKey(root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}")
                        candidates.append(winreg.QueryValue(key, None))
                    except OSError:
                        continue
        except ImportError:
            pass
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
            if base:
                candidates += [
                    str(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"),
                    str(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
                ]
    else:
        candidates += [shutil.which(n) or "" for n in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge")]
        candidates.append("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    return next((c for c in candidates if c and Path(c).is_file()), None)


def _hide_offscreen_from_taskbar() -> int:
    """Windows: drop the taskbar button of OUR off-screen Chrome windows.

    WS_EX_TOOLWINDOW (and no WS_EX_APPWINDOW) hides a window from the taskbar and
    Alt-Tab while it keeps rendering. Only Chrome_WidgetWin_1 windows parked far
    off-screen (left < -20000) are touched — the user's own Chrome is not.
    """
    if os.name != "nt":
        return 0
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    gwl_exstyle, ws_ex_toolwindow, ws_ex_appwindow = -20, 0x00000080, 0x00040000
    get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    get_style.restype = ctypes.c_ssize_t
    get_style.argtypes = [wintypes.HWND, ctypes.c_int]
    set_style.restype = ctypes.c_ssize_t
    set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    changed = 0

    def visit(hwnd: Any, _lparam: Any) -> bool:
        nonlocal changed
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        rect = RECT()
        if cls.value != "Chrome_WidgetWin_1" or not user32.GetWindowRect(hwnd, ctypes.byref(rect)) or rect.left > -20000:
            return True
        ex = get_style(hwnd, gwl_exstyle)
        if not ex & ws_ex_toolwindow:
            user32.ShowWindow(hwnd, 0)
            set_style(hwnd, gwl_exstyle, (ex | ws_ex_toolwindow) & ~ws_ex_appwindow)
            user32.ShowWindow(hwnd, 4)
            changed += 1
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit), 0)
    return changed


def _read_devtools_port(port_file: Path) -> int:
    """Port Chrome wrote to DevToolsActivePort, or 0 while it is not there yet."""
    try:
        return int(port_file.read_text(encoding="utf-8").splitlines()[0])
    except (OSError, ValueError, IndexError):
        return 0


def _port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


# ---------------------------------------------------------------------- model


@dataclass(slots=True)
class Tab:
    id: str
    url: str = ""
    title: str = ""
    target_id: str = ""
    #: A sign-in popup window, not one of the panel's tabs.
    popup: bool = False
    #: Opened by the agent (directly or by its click): it may close it without asking.
    by_agent: bool = False


@dataclass(slots=True)
class PageView:
    """What the model gets back after reading or acting on a page."""

    url: str
    title: str
    tree: str
    truncated: bool = False
    notes: list[str] = field(default_factory=list)
    #: After an action on the same page: only what changed (the model has the rest). None =
    #: send the whole page; "" = nothing visible changed.
    changes: str | None = None

    def render(self) -> str:
        head = [f"URL: {self.url}", f"Title: {self.title}"]
        head += [f"Note: {n}" for n in self.notes]
        if self.changes is not None:
            if not self.changes:
                return "\n".join([*head, "", f"{PAGE_CHANGES_MARK}: nothing visible changed."])
            return "\n".join([
                *head, "",
                f"{PAGE_CHANGES_MARK} (lines with - were removed, + added; the rest of the page and its "
                "refs are as before; browser_read gives the whole page):",
                self.changes,
            ])
        body = self.tree or "(the page has no readable content yet — it may still be loading)"
        tail = (
            ["", f"[snapshot truncated at {SNAPSHOT_LIMIT} chars — use browser_find to locate "
             "elements further down, or browser_scroll]"]
            if self.truncated else []
        )
        return "\n".join([*head, "", f"{FULL_PAGE_MARK}; use [ref=…] values with the browser tools):",
                          body, *tail])


Listener = Callable[[dict[str, Any]], Awaitable[None]]


class AgentBrowser:
    """The one shared browser session of this process."""

    def __init__(self) -> None:
        self._pw: Any = None
        self._browser: Any = None
        self._ctx: Any = None
        self._chrome: subprocess.Popen[bytes] | None = None
        self._mode = ""
        self._host: HostRelay | None = None
        self._lock = asyncio.Lock()
        self._tabs: dict[str, Tab] = {}
        self._active = ""
        self._chrome_ids: dict[Any, str] = {}   # chrome mode: Page -> tab id
        self._target_ids: dict[Any, str] = {}   # Page -> DevTools target id (cache)
        self._target_waiters: dict[str, asyncio.Event] = {}
        self._listeners: list[Listener] = []
        self._agent_depth = 0
        self._action_lock = asyncio.Lock()
        self._action_owner: asyncio.Task[Any] | None = None
        self._dialog_notes: list[str] = []
        #: The page as the model last saw it: (tab, url, compacted tree) — the base of a diff.
        self._seen: tuple[str, str, str] | None = None
        self._pending_dialog_choice: str = ""
        self._downloads: DownloadStore | None = None
        self._seq = 0
        #: chrome mode: the panel's size in CSS px; the hidden window is fitted to it.
        self.viewport = (1280, 860)
        self._cast: Any = None
        self._cast_cb: Any = None

    # --- wiring ---------------------------------------------------------------

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def downloads(self) -> DownloadStore:
        if self._downloads is None:
            self._downloads = DownloadStore(browser_dir() / "quarantine")
        return self._downloads

    def embedded_available(self) -> bool:
        return bool(_EMBEDDED.get("port")) and self._host is not None

    def attach_host(self, host: HostRelay) -> None:
        """The app window connected: from now on tabs live inside it."""
        self._host = host

    def detach_host(self, host: HostRelay) -> None:
        if self._host is host:
            self._host = None

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    async def _notify(self, event: dict[str, Any]) -> None:
        for listener in list(self._listeners):
            try:
                await listener(event)
            except Exception:  # noqa: BLE001 — a dead socket must not break the browser
                logger.debug("browser listener failed", exc_info=True)

    def _new_id(self) -> str:
        self._seq += 1
        return f"t{int(time.time()) % 100000}{self._seq}"

    # --- lifecycle --------------------------------------------------------------

    async def ensure(self) -> Any:
        """Connects to the browser (starting it if needed) and returns the context."""
        async with self._lock:
            if self._browser is not None and self._browser.is_connected():
                return self._ctx
            await self._disconnect()
            if self.embedded_available():
                await self._connect_embedded()
            else:
                await self._launch_chrome()
            await self._wire_context()
            return self._ctx

    async def _playwright(self) -> Any:
        if self._pw is None:
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise ToolError("The browser needs Playwright: pip install playwright") from exc
            self._pw = await async_playwright().start()
        return self._pw

    async def _connect_embedded(self) -> None:
        port = int(_EMBEDDED["port"])
        if not self._tabs:
            # The browser engine starts with its first tab.
            await self._host_open(url="about:blank", activate=True, wait=False)
        for _ in range(100):
            if _port_open(port):
                break
            await asyncio.sleep(0.1)
        pw = await self._playwright()
        try:
            self._browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=15000)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Could not connect to the built-in browser: {exc}") from exc
        self._ctx = self._browser.contexts[0]
        self._mode = "embedded"
        # Playwright takes over downloads when it attaches, and WebView2's browser
        # process crashes on the first download it handles that way. Hand downloads back
        # to WebView2: the shell saves them into quarantine (browser.rs, DownloadStarting)
        # and reports them to us through the UI.
        session = await self._browser.new_browser_cdp_session()
        await session.send("Browser.setDownloadBehavior", {"behavior": "default"})
        logger.info("Connected to the built-in browser (port %s)", port)

    async def _launch_chrome(self) -> None:
        exe = await asyncio.to_thread(find_browser_executable)
        pw = await self._playwright()
        if exe is None:
            try:
                bundled = pw.chromium.executable_path
                exe = bundled if await asyncio.to_thread(os.path.isfile, bundled) else None
            except Exception:  # noqa: BLE001
                exe = None
        if not exe:
            raise ToolError("No Chrome or Edge found. Install Google Chrome to use the agent browser.")
        profile = browser_dir() / "chrome_profile"
        await asyncio.to_thread(profile.mkdir, parents=True, exist_ok=True)
        port_file = profile / "DevToolsActivePort"
        # Reuse a Chrome we started earlier (backend restart) if it is still alive.
        port = await asyncio.to_thread(_read_devtools_port, port_file)
        if port and not await asyncio.to_thread(_port_open, port):
            port = 0
        if not port:
            await asyncio.to_thread(port_file.unlink, missing_ok=True)
            args = [
                exe, f"--user-data-dir={profile}", "--remote-debugging-port=0",
                "--no-first-run", "--no-default-browser-check",
                # With a debugging port Chromium sets navigator.webdriver; keep it false.
                "--disable-blink-features=AutomationControlled",
                # A hidden window: parked off-screen, still rendering for the panel.
                "--window-position=-32000,-32000", "--window-size=1400,1000",
                "--disable-features=CalculateNativeWinOcclusion",
                "--disable-backgrounding-occluded-windows",
                "about:blank",
            ]
            from core.browser_net import get_proxy, pac_url

            net = get_proxy()
            if net is not None and net.port:
                args.insert(-1, f"--proxy-pac-url={pac_url(net.port)}")
            from core.utils.proc import no_window_kwargs

            self._chrome = await asyncio.to_thread(subprocess.Popen, args, **no_window_kwargs())
            for _ in range(150):
                port = await asyncio.to_thread(_read_devtools_port, port_file)
                if port:
                    break
                await asyncio.sleep(0.1)
            if not port:
                raise ToolError("The browser did not start (no DevTools port).")
        self._browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=15000)
        self._ctx = self._browser.contexts[0]
        self._mode = "chrome"
        await asyncio.to_thread(_hide_offscreen_from_taskbar)
        if not self._ctx.pages:
            await self._ctx.new_page()
        for page in self._ctx.pages:
            self._adopt_chrome_page(page)
        if not self._active and self._tabs:
            self._active = next(iter(self._tabs))
        logger.info("Agent browser: %s over DevTools (port %s)", Path(exe).name, port)

    async def _wire_context(self) -> None:
        ctx = self._ctx
        ctx.on("page", lambda page: asyncio.ensure_future(self._on_new_page(page)))
        for page in ctx.pages:
            self._wire_page(page)

    def _wire_page(self, page: Any) -> None:
        page.on("dialog", lambda dialog: asyncio.ensure_future(self._on_dialog(dialog)))
        if self._mode == "chrome":
            # Chrome mode: downloads come through Playwright, saved into quarantine.
            # (Embedded tabs report theirs through the shell instead — see above.)
            page.on("download", lambda download: asyncio.ensure_future(self._on_download(download)))

    async def _on_download(self, download: Any) -> None:
        target = self.downloads.new_path(download.suggested_filename or "download")
        await self.downloads.started(download.url, target, self._active)
        try:
            await download.save_as(str(target))
            ok = True
        except Exception:  # noqa: BLE001 — a cancelled or failed download
            logger.info("download failed: %s", download.url, exc_info=True)
            ok = False
        await self.downloads.finished(target, ok, download.url, self._active)

    async def _on_new_page(self, page: Any) -> None:
        self._wire_page(page)
        if self._mode == "chrome":
            tab = self._adopt_chrome_page(page)
            self._active = tab.id  # a link that opened a new tab: the user follows it
            await self._notify({"kind": "tabs"})
        else:
            # Embedded: panel tabs report their target id through the shell; anything
            # else is a sign-in popup window.
            await asyncio.sleep(1.5)
            tid = await self._target_id(page)
            if tid and not any(t.target_id == tid for t in self._tabs.values()):
                popup = Tab(id=f"popup-{self._new_id()}", url=page.url, popup=True, target_id=tid)
                self._tabs[popup.id] = popup

    def _adopt_chrome_page(self, page: Any) -> Tab:
        tab_id = self._chrome_ids.get(page)
        if tab_id is None:
            tab_id = self._new_id()
            self._chrome_ids[page] = tab_id
            self._tabs[tab_id] = Tab(id=tab_id, url=page.url)
            page.on("close", lambda _p=page: self._forget_chrome_page(_p))
        return self._tabs[tab_id]

    def _forget_chrome_page(self, page: Any) -> None:
        tab_id = self._chrome_ids.pop(page, None)
        if tab_id:
            self._tabs.pop(tab_id, None)
            if self._active == tab_id:
                self._active = next(iter(self._tabs), "")
            asyncio.ensure_future(self._notify({"kind": "tabs"}))

    async def _disconnect(self) -> None:
        await self.stop_screencast()
        if self._browser is not None:
            try:
                await self._browser.close()  # over CDP this only disconnects
            except Exception:  # noqa: BLE001
                logger.debug("browser disconnect failed", exc_info=True)
        self._browser = self._ctx = None
        self._target_ids.clear()
        self._chrome_ids.clear()
        if self._mode == "chrome":
            self._tabs.clear()
            self._active = ""

    async def _quit_chrome_gracefully(self, timeout: float = 5.0) -> None:
        """Let our own Chrome exit by itself: it writes its cookie store to disk only now and
        then (about every 30 s) and on a clean exit. Killing it lost the latest logins, and
        every cookie imported from Firefox, at each app restart."""
        if self._chrome is None or self._chrome.poll() is not None or self._browser is None:
            return
        try:
            cdp = await self._browser.new_browser_cdp_session()
            await cdp.send("Browser.close")
        except Exception:  # noqa: BLE001 - the browser may be gone already; the kill below remains
            logger.debug("graceful browser close failed", exc_info=True)
            return
        try:
            await asyncio.to_thread(self._chrome.wait, timeout)
        except subprocess.TimeoutExpired:
            logger.info("the browser did not exit in %.0f s; it will be stopped", timeout)

    async def close(self) -> None:
        async with self._lock:
            await self._quit_chrome_gracefully()
            await self._disconnect()
            if self._chrome is not None and self._chrome.poll() is None:
                self._chrome.terminate()
            self._chrome = None
            if self._pw is not None:
                try:
                    await self._pw.stop()
                except Exception:  # noqa: BLE001
                    logger.debug("playwright stop failed", exc_info=True)
                self._pw = None

    # --- embedded host plumbing --------------------------------------------------

    async def _host_open(self, *, url: str, activate: bool, wait: bool = True) -> Tab:
        if self._host is None:
            raise ToolError("The app window is not connected.")
        tab = Tab(id=self._new_id(), url=url)
        self._tabs[tab.id] = tab
        waiter = self._target_waiters.setdefault(tab.id, asyncio.Event())
        reply = await self._host.request("open", tab=tab.id, url=url, activate=activate)
        if not reply.get("ok", True):
            self._tabs.pop(tab.id, None)
            raise ToolError(f"Could not open a tab: {reply.get('error') or 'unknown error'}")
        if activate:
            self._active = tab.id
        if wait:
            try:
                await asyncio.wait_for(waiter.wait(), timeout=15)
            except TimeoutError as exc:
                raise ToolError("The new tab did not come up in time.") from exc
        return tab

    async def on_host_tabs(self, tabs: list[dict[str, Any]], active: str) -> None:
        """The panel's tab strip changed (the user opened, closed or switched tabs)."""
        known = {t["id"] for t in tabs if t.get("id")}
        for tab_id in [t for t, tab in self._tabs.items() if not tab.popup and t not in known]:
            self._tabs.pop(tab_id, None)
        for item in tabs:
            tab = self._tabs.setdefault(item["id"], Tab(id=item["id"]))
            tab.url = item.get("url") or tab.url
            tab.title = item.get("title") or tab.title
        if active in self._tabs:
            self._active = active

    async def on_host_event(self, event: dict[str, Any]) -> None:
        """Something happened in a panel tab (reported by the Tauri shell via the UI)."""
        tab_id = str(event.get("tab") or "")
        kind = event.get("kind")
        tab = self._tabs.setdefault(tab_id, Tab(id=tab_id)) if tab_id else None
        if kind == "target" and tab is not None:
            tab.target_id = str(event.get("target_id") or "")
            self._target_waiters.setdefault(tab_id, asyncio.Event()).set()
        elif kind == "title" and tab is not None:
            tab.title = str(event.get("title") or "")
        elif kind in ("loading", "loaded") and tab is not None:
            tab.url = str(event.get("url") or tab.url)
        elif kind == "download_started":
            await self.downloads.started(str(event.get("url") or ""), Path(str(event.get("path"))), tab_id)
        elif kind == "download_finished" and event.get("path"):
            await self.downloads.finished(Path(str(event["path"])), bool(event.get("ok")), str(event.get("url") or ""), tab_id)

    # --- pages ------------------------------------------------------------------

    async def _target_id(self, page: Any) -> str:
        cached = self._target_ids.get(page)
        if cached:
            return cached
        try:
            session = await self._ctx.new_cdp_session(page)
            try:
                info = await session.send("Target.getTargetInfo")
            finally:
                await session.detach()
            tid = str(info.get("targetInfo", {}).get("targetId", ""))
        except Exception:  # noqa: BLE001 — a closing page
            return ""
        if tid:
            self._target_ids[page] = tid
        return tid

    async def _page_of(self, tab: Tab) -> Any:
        if self._mode == "chrome":
            for page, tab_id in self._chrome_ids.items():
                if tab_id == tab.id and not page.is_closed():
                    return page
            return None
        if not tab.target_id:
            waiter = self._target_waiters.setdefault(tab.id, asyncio.Event())
            try:
                await asyncio.wait_for(waiter.wait(), timeout=8)
            except TimeoutError:
                return None
        for _ in range(30):
            for page in self._ctx.pages:
                if not page.is_closed() and await self._target_id(page) == tab.target_id:
                    return page
            await asyncio.sleep(0.1)
        return None

    async def page(self) -> Any:
        """The active tab's page, creating a first tab if there is none."""
        await self.ensure()
        tab = self._tabs.get(self._active)
        if tab is None:
            tab = next((t for t in self._tabs.values() if not t.popup), None)
            if tab is None:
                if self._mode == "embedded":
                    tab = await self._host_open(url="about:blank", activate=True)
                else:
                    self._adopt_chrome_page(await self._ctx.new_page())
                    tab = next(iter(self._tabs.values()))
                tab.by_agent = True
            self._active = tab.id
        page = await self._page_of(tab)
        if page is None:
            raise ToolError("The active tab is not available. Try browser_tabs action=list.")
        return page

    async def tabs(self) -> list[dict[str, Any]]:
        await self.ensure()
        result = []
        for tab in self._tabs.values():
            page = await self._page_of(tab) if self._mode == "chrome" else None
            title = tab.title
            url = tab.url
            if page is not None:
                url = page.url
                try:
                    title = await page.title()
                except Exception:  # noqa: BLE001
                    title = tab.title
            result.append({"id": tab.id, "url": url, "title": title or url, "active": tab.id == self._active,
                           "popup": tab.popup, "by_agent": tab.by_agent})
        return result

    async def new_tab(self, url: str | None = None, *, by_agent: bool = True,
                      sandbox: Settings | None = None) -> Any:
        await self.ensure()
        target = (local_target(url, sandbox) or normalize_target(url)) if url else "about:blank"
        if self._mode == "embedded":
            tab = await self._host_open(url=target, activate=True)
            page = await self._page_of(tab)
        else:
            page = await self._ctx.new_page()
            tab = self._adopt_chrome_page(page)
            self._active = tab.id
            if url:
                await self._goto(page, target)
        tab.by_agent = by_agent
        await self._notify({"kind": "tabs"})
        return page

    async def select_tab(self, tab_id: str) -> Any:
        await self.ensure()
        tab = self._tabs.get(tab_id)
        if tab is None:
            raise ToolError(f"No tab '{tab_id}'. Use browser_tabs action=list.")
        self._active = tab_id
        if self._mode == "embedded" and not tab.popup and self._host is not None:
            await self._host.request("select", tab=tab_id)
        page = await self._page_of(tab)
        if page is not None and self._mode == "chrome":
            await page.bring_to_front()
        await self._notify({"kind": "tabs"})
        return page

    def opened_by_agent(self, tab_id: str) -> bool:
        tab = self._tabs.get(tab_id)
        return bool(tab and tab.by_agent)

    async def close_tab(self, tab_id: str) -> None:
        await self.ensure()
        tab = self._tabs.get(tab_id)
        if tab is None:
            raise ToolError(f"No tab '{tab_id}'.")
        if self._mode == "embedded" and not tab.popup and self._host is not None:
            await self._host.request("close", tab=tab_id)
            self._tabs.pop(tab_id, None)
        else:
            page = await self._page_of(tab)
            if page is not None:
                await page.close()
            self._tabs.pop(tab_id, None)
        if self._active == tab_id:
            self._active = next((t.id for t in self._tabs.values() if not t.popup), "")
        await self._notify({"kind": "tabs"})

    # --- agent actions ------------------------------------------------------------

    async def _goto(self, page: Any, url: str) -> None:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:  # noqa: BLE001
            if "Download is starting" in str(exc):
                return  # the URL is a file: it goes to quarantine, reported via downloads
            raise ToolError(f"Could not open '{url}': {exc}") from exc
        await self._settle(page)

    async def _settle(self, page: Any, quiet_ms: int = 2500) -> None:
        """Waits briefly for the page to calm down; endless polling pages are normal."""
        try:
            await page.wait_for_load_state("networkidle", timeout=quiet_ms)
        except Exception:  # noqa: BLE001
            logger.debug("page did not reach networkidle in %s ms", quiet_ms)

    def agent_action(self) -> _AgentScope:
        """One agent action at a time (a model may fire several tools at once, and
        typing into three fields of one page in parallel scrambles them); page dialogs
        meanwhile are answered for the agent, not shown to the user."""
        return _AgentScope(self)

    async def navigate(self, url: str, sandbox: Settings | None = None) -> PageView:
        await self._notify({"kind": "agent_active"})
        target = local_target(url, sandbox) or normalize_target(url)
        if not target.startswith(("http://", "https://", "about:blank", "file:")):
            raise ToolError("The browser opens web pages (http/https) and pages inside the workspace.")
        async with self.agent_action():
            page = await self.page()
            tab = self._tabs.get(self._active)
            if tab is not None and page.url in ("", "about:blank"):
                tab.by_agent = True  # an empty tab the agent fills is its own, not the user's page
            await self._goto(page, target)
            return await self.snapshot()

    async def _keep_in_sandbox(self, page: Any, sandbox: Settings | None) -> None:
        """A local page may link to any file on the disk; following such a link is refused."""
        if _local_url_allowed(page.url, sandbox):
            return
        blocked = page.url
        try:
            await page.goto("about:blank", timeout=10000)
        except Exception:  # noqa: BLE001
            logger.debug("could not leave %s", blocked, exc_info=True)
        raise ToolError(f"Blocked: {blocked} is a local file outside the workspace.")

    async def history(self, op: str, sandbox: Settings | None = None) -> PageView:
        async with self.agent_action():
            page = await self.page()
            try:
                if op == "back":
                    await page.go_back(wait_until="domcontentloaded", timeout=20000)
                elif op == "forward":
                    await page.go_forward(wait_until="domcontentloaded", timeout=20000)
                else:
                    await page.reload(wait_until="domcontentloaded", timeout=30000)
            except Exception:  # noqa: BLE001 — no history entry is not an error for the model
                logger.debug("history %s failed", op, exc_info=True)
            await self._settle(page)
            await self._keep_in_sandbox(page, sandbox)
            return await self.snapshot()

    async def locate(self, ref: str) -> Any:
        page = await self.page()
        ref = ref.strip().strip("[]").removeprefix("ref=")
        loc = page.locator(f"aria-ref={ref}")
        try:
            if await loc.count() == 0:
                raise ToolError(f"Element [ref={ref}] is gone — the page changed. Read the page again.")
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Unknown ref '{ref}'. Use a [ref=…] value from the latest page snapshot.") from exc
        return loc

    async def act(self, action: Callable[[Any, Any], Awaitable[None]], ref: str | None = None,
                  *, dialog: str = "", sandbox: Settings | None = None) -> PageView:
        """Runs one interaction and returns the resulting page, with what changed."""
        async with self.agent_action():
            page = await self.page()
            before_url = page.url
            before_seen = self._seen
            before_tabs = set(self._tabs)
            before_downloads = {d["id"] for d in self.downloads.list()}
            self._pending_dialog_choice = dialog
            target = await self.locate(ref) if ref else None
            try:
                await action(page, target)
            except ToolError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ToolError(f"The action failed: {str(exc).splitlines()[0][:300]}") from exc
            finally:
                self._pending_dialog_choice = ""
            await asyncio.sleep(0.3)
            new_tabs = [t for t in self._tabs if t not in before_tabs]
            for tab_id in new_tabs:
                self._tabs[tab_id].by_agent = True
            if new_tabs and self._mode == "chrome":
                page = await self.page()  # followed the link into the new tab
            await self._settle(page)
            await self._keep_in_sandbox(page, sandbox)
            view = await self.snapshot()
            diff_on = sandbox.browser_snapshot_diff if sandbox is not None else get_settings().browser_snapshot_diff
            if (diff_on and before_seen is not None and not new_tabs and self._seen is not None
                    and before_seen[:2] == self._seen[:2]):
                # Same tab, same address: the model has the page from its previous look.
                view.changes = page_changes(before_seen[2], self._seen[2])
            if page.url != before_url:
                view.notes.append(f"navigated from {before_url}")
            if new_tabs:
                view.notes.append(f"a new tab opened: {', '.join(new_tabs)} (now active)")
            fresh = [d for d in self.downloads.list() if d["id"] not in before_downloads]
            for d in fresh:
                view.notes.append(f"download started: {d['name']} — see browser_downloads")
            return view

    async def snapshot(self, *, limit: int = SNAPSHOT_LIMIT) -> PageView:
        page = await self.page()
        try:
            title = await page.title()
        except Exception:  # noqa: BLE001
            title = ""
        try:
            tree = compact_tree(await page.aria_snapshot(mode="ai", timeout=15000))
        except Exception as exc:  # noqa: BLE001
            tree = f"(could not read the page: {str(exc).splitlines()[0][:200]})"
        truncated = len(tree) > limit
        notes = self._dialog_notes[:]
        self._dialog_notes.clear()
        self._seen = (self._active, page.url, tree)
        return PageView(url=page.url, title=title or page.url, tree=tree[:limit], truncated=truncated, notes=notes)

    async def find(self, query: str, limit: int = 12) -> str:
        """Places in the full snapshot that match the query — for long pages.

        Text is split across snapshot lines ("16.7" in a text node, "light-years" in the
        link after it), so a match is a window of three neighbouring lines holding the
        most query words; each hit is shown with that context and its refs.
        """
        page = await self.page()
        tree = await page.aria_snapshot(mode="ai", timeout=15000)
        lines = tree.splitlines()
        words = [w for w in query.lower().split() if w]
        if not words:
            return "Give some words to look for."
        scored: list[tuple[int, int]] = []
        for i in range(len(lines)):
            if not any(w in lines[i].lower() for w in words):
                continue  # a place starts at a line that itself matches
            window = " ".join(lines[i : i + 3]).lower()
            scored.append((sum(1 for w in words if w in window), i))
        if not scored:
            return f"Nothing on the page matches '{query}'."
        best = max(found for found, _ in scored)
        picked: list[int] = []
        for found, i in scored:
            if found == best and all(abs(i - j) > 2 for j in picked):
                picked.append(i)
        blocks = ["\n".join(ln.strip() for ln in lines[i : i + 3]) for i in picked[:limit]]
        note = "" if best == len(words) else f"(no place has all the words; showing places with {best} of {len(words)})\n"
        more = f"\n…and {len(picked) - limit} more places" if len(picked) > limit else ""
        return note + "\n---\n".join(blocks) + more

    async def screenshot_png(self, *, full_page: bool = False) -> bytes:
        page = await self.page()
        try:
            return await page.screenshot(full_page=full_page, timeout=15000)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(
                f"Could not take a screenshot ({str(exc).splitlines()[0][:160]}). "
                "The browser panel may be closed; open it and retry."
            ) from exc

    # --- dialogs ------------------------------------------------------------------

    async def _on_dialog(self, dialog: Any) -> None:
        kind, message = dialog.type, dialog.message
        if self._agent_depth > 0:
            choice = self._pending_dialog_choice or ("accept" if kind in ("alert", "beforeunload") else "dismiss")
            await (dialog.accept() if choice == "accept" else dialog.dismiss())
            verb = "accepted" if choice == "accept" else "dismissed"
            self._dialog_notes.append(
                f"the page showed a {kind} dialog «{message[:200]}» — it was {verb}"
                + ("" if choice == "accept" else "; repeat the action with dialog=accept to confirm it")
            )
            return
        # The user is browsing: ask them in the app, the way the browser itself would.
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        await self._notify({"kind": "dialog", "dialog": kind, "message": message,
                            "default": dialog.default_value, "reply": future})
        try:
            answer = await asyncio.wait_for(future, timeout=120)
        except TimeoutError:
            answer = ""
        if answer is None or answer == "":
            await dialog.dismiss()
        elif kind == "prompt":
            await dialog.accept(str(answer))
        else:
            await dialog.accept()

    # --- chrome mode: panel screencast -------------------------------------------------

    async def set_viewport(self, width: int, height: int) -> bool:
        """Chrome mode: fit the hidden window so the page is exactly the panel's size."""
        width = max(320, min(2560, int(width or 0)))
        height = max(240, min(2560, int(height or 0)))
        if self._mode != "chrome" or (width, height) == self.viewport:
            return False
        page = await self.page()
        try:
            session = await self._ctx.new_cdp_session(page)
            inner = await page.evaluate("[innerWidth, innerHeight, outerWidth, outerHeight]")
            win = await session.send("Browser.getWindowForTarget")
            await session.send("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": {
                "width": width + (inner[2] - inner[0]), "height": height + (inner[3] - inner[1]),
            }})
            await session.detach()
        except Exception:  # noqa: BLE001 — resizing is best effort
            return False
        self.viewport = (width, height)
        return True

    async def start_screencast(self, on_frame: Any) -> None:
        """Chrome mode: Chrome pushes JPEG frames on visual change (no polling)."""
        import base64

        page = await self.page()
        if self._cast and self._cast[1] is page and not page.is_closed():
            self._cast_cb = on_frame
            return
        await self.stop_screencast()
        session = await self._ctx.new_cdp_session(page)
        self._cast = (session, page)
        self._cast_cb = on_frame
        dpr = await page.evaluate("devicePixelRatio")

        def handle(params: dict[str, Any]) -> None:
            callback = self._cast_cb

            async def run() -> None:
                try:
                    if callback:
                        await callback(base64.b64decode(params.get("data", "")))
                finally:
                    try:
                        await session.send("Page.screencastFrameAck", {"sessionId": params.get("sessionId")})
                    except Exception:  # noqa: BLE001
                        logger.debug("frame ack failed", exc_info=True)

            asyncio.ensure_future(run())

        session.on("Page.screencastFrame", handle)
        w, h = self.viewport
        # Frames at the screen's own pixel density: sharp at 1:1, no upscaling.
        await session.send("Page.startScreencast", {
            "format": "jpeg", "quality": 88, "everyNthFrame": 1,
            "maxWidth": int(w * dpr), "maxHeight": int(h * dpr),
        })

    async def stop_screencast(self) -> None:
        cast, self._cast, self._cast_cb = self._cast, None, None
        if cast:
            for step in ("Page.stopScreencast", None):
                try:
                    await (cast[0].send(step) if step else cast[0].detach())
                except Exception:  # noqa: BLE001
                    logger.debug("screencast stop step failed", exc_info=True)

    async def user_input(self, message: dict[str, Any]) -> None:
        """Chrome mode: the user's mouse and keyboard on the panel picture."""
        page = await self.page()
        kind = message.get("kind")
        x, y = float(message.get("x") or 0), float(message.get("y") or 0)
        if kind == "click":
            await page.mouse.click(x, y, button=message.get("button") or "left",
                                   click_count=int(message.get("count") or 1))
        elif kind == "move":
            await page.mouse.move(x, y)
        elif kind == "wheel":
            await page.mouse.move(x, y)
            await page.mouse.wheel(float(message.get("dx") or 0), float(message.get("dy") or 0))
        elif kind == "text":
            await page.keyboard.insert_text(str(message.get("text") or ""))
        elif kind == "key":
            await page.keyboard.press(str(message.get("key") or ""))


class _AgentScope:
    def __init__(self, browser: AgentBrowser) -> None:
        self.browser = browser
        self.owner = False

    async def __aenter__(self) -> None:
        # Re-entrant for the same task (an action that reads the page afterwards).
        task = asyncio.current_task()
        if self.browser._action_owner is not task:
            await self.browser._action_lock.acquire()
            self.browser._action_owner = task
            self.owner = True
        self.browser._agent_depth += 1

    async def __aexit__(self, *exc: object) -> None:
        self.browser._agent_depth -= 1
        if self.owner:
            self.browser._action_owner = None
            self.browser._action_lock.release()


_agent_browser: AgentBrowser | None = None


def get_agent_browser() -> AgentBrowser:
    global _agent_browser
    if _agent_browser is None:
        _agent_browser = AgentBrowser()
    return _agent_browser


async def close_agent_browser() -> None:
    if _agent_browser is not None:
        await _agent_browser.close()
