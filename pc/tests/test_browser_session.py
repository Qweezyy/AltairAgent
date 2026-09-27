# ruff: noqa: ASYNC240 — tests read their fixture files synchronously on purpose
"""Hard functional tests for the shared browser (core/browser_session.py).

They start a REAL Chrome/Edge as an ordinary program (the fallback host — the embedded
WebView2 host needs the desktop shell) and attach over DevTools, exactly as the app
does, against pages served by a local HTTP server — no internet. If no browser is
installed the browser tests skip; the pure ones always run.
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import re
import threading
from pathlib import Path

import pytest

import core.browser_session as bs
from core.browser_session import AgentBrowser, looks_like_url, normalize_target
from core.errors import ToolError

# ---------------------------------------------------------------- address bar


def test_looks_like_url_recognizes_addresses():
    for s in ["example.com", "https://example.com/path?q=1", "http://localhost:3000", "localhost",
              "192.168.1.10", "sub.domain.co.uk/page", "about:blank"]:
        assert looks_like_url(s), s


def test_looks_like_url_rejects_search_queries():
    for s in ["погода в москве", "openai", "best pizza near me", "how to center a div", ""]:
        assert not looks_like_url(s), s


def test_normalize_target_maps_queries_to_search_and_urls_to_https():
    assert normalize_target("example.com") == "https://example.com"
    assert normalize_target("localhost:8000") == "https://localhost:8000"
    out = normalize_target("погода в москве")
    assert out.startswith("https://www.google.com/search?q=") and " " not in out


# ---------------------------------------------------------------- fixtures

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Probe Page</title></head>
<body><main>
  <h1>Hello Probe</h1>
  <p id="para">Original paragraph text.</p>
  <label>Name <input id="field" type="text"></label>
  <select id="size" aria-label="Size"><option>Small</option><option>Large</option></select>
  <button onclick="document.getElementById('para').textContent='Clicked OK'">Press Me</button>
  <button onclick="document.getElementById('para').textContent = confirm('Sure?') ? 'Confirmed' : 'Declined'">Delete</button>
  <a href="/dest.html" target="_blank">Open dest</a>
  <a href="/file.csv">Get CSV</a>
  <p>It is located at a distance of 16.7 <a href="#ly">light-years</a> from the Sun.</p>
</main></body></html>"""


class _Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        if self.path.endswith(".csv"):
            self.send_header("Content-Disposition", "attachment; filename=file.csv")
        super().end_headers()

    def log_message(self, *args) -> None:  # keep test output clean
        return


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("site")
    (root / "probe.html").write_text(PAGE, encoding="utf-8")
    (root / "dest.html").write_text("<!doctype html><meta charset=utf-8><title>Dest Page</title><h1>Arrived</h1>", encoding="utf-8")
    (root / "file.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    paragraphs = "".join(f"<p>Paragraph {i} of a long article about browsing agents.</p>" for i in range(300))
    (root / "long.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Long</title><main><button onclick=\"document."
        "getElementById('s').textContent='Saved'\">Save</button><p id=s>Draft</p>" + paragraphs + "</main>",
        encoding="utf-8")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Handler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture()
async def browser(tmp_path: Path, monkeypatch):
    monkeypatch.setitem(bs._EMBEDDED, "dir", tmp_path / "browser")
    monkeypatch.setitem(bs._EMBEDDED, "port", 0)
    b = AgentBrowser()
    try:
        await b.ensure()
    except Exception as exc:  # noqa: BLE001 - no Chrome/Edge on this machine
        pytest.skip(f"No browser available: {str(exc)[:120]}")
    try:
        yield b
    finally:
        await b.close()


def ref_of(tree: str, pattern: str) -> str:
    match = re.search(pattern + r'[^\n]*?\[ref=(\w+)\]', tree)
    assert match, f"no element matching {pattern!r} in:\n{tree[:1500]}"
    return match.group(1)


# ---------------------------------------------------------------- browser


async def test_page_sees_an_ordinary_browser(browser, site):
    """No automation marks: the reason sites do not treat the agent as a bot."""
    await browser.navigate(site + "/probe.html")
    page = await browser.page()
    assert await page.evaluate("navigator.webdriver") is False
    assert "Headless" not in await page.evaluate("navigator.userAgent")


async def test_snapshot_is_an_accessibility_tree_with_refs(browser, site):
    view = await browser.navigate(site + "/probe.html")
    assert view.title == "Probe Page"
    assert 'heading "Hello Probe"' in view.tree
    assert re.search(r'button "Press Me" \[ref=\w+\]', view.tree)
    assert "URL: " in view.render() and "[ref=" in view.render()


async def test_type_select_and_click_by_ref(browser, site):
    view = await browser.navigate(site + "/probe.html")

    async def type_name(page, target):
        await target.fill("probe input")

    await browser.act(type_name, ref_of(view.tree, r'textbox "Name"'))

    async def pick(page, target):
        await target.select_option(label=["Large"])

    await browser.act(pick, ref_of(view.tree, r'combobox "Size"'))

    async def click(page, target):
        await target.click()

    after = await browser.act(click, ref_of(view.tree, r'button "Press Me"'))
    assert "Clicked OK" in after.tree
    page = await browser.page()
    assert await page.evaluate("document.getElementById('field').value") == "probe input"
    assert await page.evaluate("document.getElementById('size').value") == "Large"


async def test_stale_ref_gives_a_clear_error(browser, site):
    await browser.navigate(site + "/probe.html")
    with pytest.raises(ToolError, match="Read the page again|Unknown ref"):
        await browser.act(lambda page, target: asyncio.sleep(0), "e99999")


async def test_confirm_dialog_is_dismissed_by_default_and_accepted_on_request(browser, site):
    view = await browser.navigate(site + "/probe.html")
    ref = ref_of(view.tree, r'button "Delete"')

    async def click(page, target):
        await target.click()

    first = await browser.act(click, ref)
    assert "Declined" in first.tree
    assert any("confirm dialog" in n and "dismissed" in n for n in first.notes)
    second = await browser.act(click, ref, dialog="accept")
    assert "Confirmed" in second.tree


async def test_link_to_new_tab_is_followed_and_tabs_have_ids(browser, site):
    view = await browser.navigate(site + "/probe.html")

    async def click(page, target):
        await target.click()

    after = await browser.act(click, ref_of(view.tree, r'link "Open dest"'))
    assert after.title == "Dest Page"
    assert any("new tab" in n for n in after.notes)
    tabs = await browser.tabs()
    assert len(tabs) == 2 and all(t["id"] for t in tabs)
    first = tabs[0]["id"]
    await browser.select_tab(first)
    assert (await browser.snapshot()).title == "Probe Page"
    await browser.close_tab(tabs[1]["id"])
    assert len(await browser.tabs()) == 1


async def test_agent_owns_the_tabs_it_opens(browser, site, monkeypatch):
    """The agent tidies its own tabs freely; closing a tab the user opened asks first."""
    from core.tools.builtin import browser_tools as bt

    monkeypatch.setattr(bt, "get_agent_browser", lambda: browser)
    await browser.new_tab(site + "/probe.html", by_agent=False)  # the user's tab
    user_tab = next(t["id"] for t in await browser.tabs() if t["active"])
    await browser.new_tab(site + "/dest.html")
    tabs = {t["id"]: t for t in await browser.tabs()}
    mine = next(i for i, t in tabs.items() if t["by_agent"] and t["url"].endswith("/dest.html"))
    assert tabs[user_tab]["by_agent"] is False

    tool = bt.BrowserTabsTool()
    verdict = tool.auto_verdict
    assert verdict(bt.TabsArgs(action="close", tab=mine), None) == "allow"
    assert verdict(bt.TabsArgs(action="close", tab=user_tab), None) == "ask"
    assert verdict(bt.TabsArgs(action="list"), None) == "allow"

    listing = await tool.run(bt.TabsArgs(action="list"), None)
    assert f"[{mine}]" in listing and "(yours)" in listing and "(user's)" in listing

    # A blank tab (the panel opens one) becomes the agent's once the agent loads a page in it.
    await browser.new_tab(by_agent=False)
    blank = next(t["id"] for t in await browser.tabs() if t["active"])
    assert not browser.opened_by_agent(blank)
    await browser.navigate(site + "/probe.html")
    assert browser.opened_by_agent(blank)


async def test_find_matches_text_split_across_lines(browser, site):
    await browser.navigate(site + "/probe.html")
    found = await browser.find("16.7 light-years")
    assert "16.7" in found and "light-years" in found and "no place has all" not in found
    assert "Nothing on the page" in await browser.find("zzqqxx")


@pytest.fixture()
def local_site(settings, monkeypatch):
    """A small static site inside the workspace, the way the agent builds one."""
    import core.security.paths as paths

    monkeypatch.setattr(paths, "get_settings", lambda: settings)
    site_dir = settings.workspace / "site"
    site_dir.mkdir()
    (site_dir / "index.html").write_text(
        "<!doctype html><title>Local Site</title><h1>Built by the agent</h1>"
        '<a href="about.html">About</a><p id="out">idle</p>'
        "<button onclick=\"document.getElementById('out').textContent='clicked'\">Run</button>",
        encoding="utf-8",
    )
    (site_dir / "about.html").write_text("<title>About</title><h1>About page</h1>", encoding="utf-8")
    return settings


async def test_workspace_pages_open_and_work(browser, local_site):
    """The agent tests its own static site straight from disk: open, click, follow links."""
    view = await browser.navigate("site/index.html")
    assert view.url.startswith("file:///") and view.title == "Local Site"
    assert 'heading "Built by the agent"' in view.tree

    async def click(page, target):
        await target.click()

    view = await browser.act(click, ref_of(view.tree, r'button "Run"'))
    assert "clicked" in view.tree
    view = await browser.act(click, ref_of(view.tree, r'link "About"'))
    assert view.title == "About"

    # file:// URL and an absolute path work as well.
    page_path = local_site.workspace / "site" / "index.html"
    assert (await browser.navigate(page_path.as_uri())).title == "Local Site"
    assert (await browser.navigate(str(page_path))).title == "Local Site"


async def test_files_outside_the_workspace_stay_off_limits(browser, local_site, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")
    with pytest.raises(ToolError, match="only inside the workspace"):
        await browser.navigate(secret.as_uri())
    with pytest.raises(ToolError, match="only inside the workspace"):
        await browser.navigate(str(secret))
    with pytest.raises(ToolError, match="only inside the workspace"):
        await browser.new_tab(secret.as_uri())


async def test_a_local_page_cannot_lead_outside_the_workspace(browser, local_site, tmp_path):
    """A workspace page linking to C:\… must not become a way around the sandbox."""
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")
    (local_site.workspace / "site" / "leak.html").write_text(
        f'<title>Leak</title><a href="{secret.as_uri()}">steal</a>', encoding="utf-8"
    )
    view = await browser.navigate("site/leak.html")

    async def click(page, target):
        await target.click()

    with pytest.raises(ToolError, match="outside the workspace"):
        await browser.act(click, ref_of(view.tree, r'link "steal"'))
    page = await browser.page()
    assert "top secret" not in await page.content()


def test_local_target_rules(local_site):
    assert bs.local_target("https://example.com") is None
    assert bs.local_target("weather tomorrow") is None
    assert bs.local_target("site/index.html").startswith("file:///")
    assert bs.local_target("site/index.html#top").endswith("index.html#top")
    assert bs.local_target("http://127.0.0.1:8000/site/index.html") is None
    assert bs.local_target("localhost:3000/index.html") is None
    # A relative page counts as local only when it exists; otherwise it is a web address.
    assert bs.local_target("site/missing.html") is None
    assert bs.local_target("example.com/index.html") is None
    with pytest.raises(ToolError):
        bs.local_target((local_site.workspace / "site" / "missing.html").as_uri())


async def test_parallel_agent_actions_run_one_at_a_time(browser, site):
    view = await browser.navigate(site + "/probe.html")
    ref = ref_of(view.tree, r'button "Press Me"')
    spans: list[tuple[float, float]] = []

    async def slow(page, target):
        loop = asyncio.get_running_loop()
        start = loop.time()
        await asyncio.sleep(0.3)
        spans.append((start, loop.time()))

    await asyncio.gather(browser.act(slow, ref), browser.act(slow, ref))
    (a0, a1), (b0, b1) = sorted(spans)
    assert b0 >= a1 - 0.01, "two actions overlapped on the same page"


async def test_download_lands_in_quarantine_and_is_checked(browser, site):
    await browser.navigate(site + "/probe.html")
    page = await browser.page()
    async with page.expect_download():
        await page.click("text=Get CSV")
    for _ in range(100):
        items = browser.downloads.list()
        if items and items[0]["status"] not in ("downloading", "checking"):
            break
        await asyncio.sleep(0.2)
    item = browser.downloads.list()[0]
    assert item["name"] == "file.csv"
    assert item["status"] in ("clean", "unscanned")
    assert Path(item["path"]).read_text(encoding="utf-8") == "a,b\n1,2\n"
    assert browser.downloads.quarantine in Path(item["path"]).parents


async def test_screencast_pushes_frames_and_stops(browser, site):
    frames: list[bytes] = []

    async def on_frame(data: bytes) -> None:
        frames.append(data)

    await browser.navigate(site + "/probe.html")
    await browser.set_viewport(700, 500)
    await browser.start_screencast(on_frame)
    await browser.navigate(site + "/dest.html")
    for _ in range(40):
        if frames:
            break
        await asyncio.sleep(0.1)
    assert frames and frames[0][:2] == b"\xff\xd8"
    await browser.stop_screencast()
    await browser.stop_screencast()  # idempotent


async def test_screenshot_tool_attaches_the_image_for_the_model(browser, site, settings, monkeypatch):
    import core.tools.builtin.browser_tools as bt
    from core.tools.base import ToolContext

    monkeypatch.setattr(bt, "get_agent_browser", lambda: browser)
    await browser.navigate(site + "/probe.html")
    ctx = ToolContext(settings=settings)
    out = await bt.BrowserScreenshotTool().run(bt.ScreenshotArgs(question="what is here"), ctx)
    assert out.ok and ".screenshots/browser-" in out.content
    pending = ctx.scratch.get("_vision_pending")
    assert pending[0]["parts"][0]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_chat_private_folder_is_the_sandbox(browser, settings, tmp_path, monkeypatch):
    """A chat without a chosen folder works in its own folder: the browser must use the run's
    sandbox, not the global workspace (the agent wrote a page there and could not open it)."""
    from core.tools.base import ToolContext
    from core.tools.builtin import browser_tools as bt

    chat_dir = tmp_path / "chat_files" / "abc"
    (chat_dir / "app").mkdir(parents=True)
    (chat_dir / "app" / "index.html").write_text("<title>Chat Page</title><h1>ok</h1>", encoding="utf-8")
    run_settings = settings.model_copy(update={"workspace_path": chat_dir})

    assert bs.local_target("app/index.html") is None  # not in the global workspace
    assert bs.local_target("app/index.html", run_settings).startswith("file:///")

    monkeypatch.setattr(bt, "get_agent_browser", lambda: browser)
    out = await bt.BrowserNavigateTool().run(bt.NavigateArgs(url="app/index.html"), ToolContext(settings=run_settings))
    assert "Chat Page" in out


# ---------------------------------------------------------------- fewer tokens per page


def test_compact_tree_drops_what_the_model_never_uses():
    from core.browser_session import compact_tree

    tracking = "https://yandex.ru/adfox/406261/clickURL?" + "p=x&" * 300
    tree = "\n".join([
        "- generic [ref=e1]:",
        "  - generic [ref=e2] [cursor=pointer]:",          # a clickable div keeps its mark
        "    - text: Open menu",
        "  - link \"Ad\" [ref=e3] [cursor=pointer]:",
        f"    - /url: {tracking}",
        "  - link \"Docs\" [ref=e4] [cursor=pointer]:",
        "    - /url: /ru/docs?page=2",                      # short: kept as is
        "  - generic [ref=e5]: \"+1\"",                    # a named generic stays
    ])
    out = compact_tree(tree)
    assert "- generic [ref=e1]:" not in out
    assert "generic [ref=e2] [cursor=pointer]" in out
    assert 'link "Ad" [ref=e3]:' in out and "[cursor=pointer]" not in out.split("e3")[1].split("\n")[0]
    assert "/url: https://yandex.ru/adfox/406261/clickURL?…" in out and "p=x" not in out
    assert "/url: /ru/docs?page=2" in out
    assert 'generic [ref=e5]: "+1"' in out
    assert all(f"ref=e{i}" in out for i in (2, 3, 4, 5))
    assert len(out) < len(tree) / 4


def test_page_changes_is_small_or_nothing():
    from core.browser_session import page_changes

    page = "\n".join(f"- paragraph [ref=e{i}]: line {i}" for i in range(200))
    assert page_changes(page, page) == ""
    changed = page.replace("line 100", "Clicked OK")
    diff = page_changes(page, changed)
    assert "+- paragraph [ref=e100]: Clicked OK" in diff and "-- paragraph [ref=e100]: line 100" in diff
    assert len(diff) < len(page) / 20
    assert page_changes(page, "\n".join(f"- other {i}" for i in range(200))) is None  # a new page


def test_session_marks_match_the_browser():
    from core.agent import session as s

    assert (s.FULL_PAGE_MARK, s.PAGE_CHANGES_MARK) == (bs.FULL_PAGE_MARK, bs.PAGE_CHANGES_MARK)


async def test_an_action_on_the_same_page_returns_only_what_changed(browser, site):
    view = await browser.navigate(site + "/probe.html")
    assert view.changes is None and bs.FULL_PAGE_MARK in view.render()  # a new page: whole
    press = ref_of(view.tree, r'button "Press Me"')

    async def click(page, target):
        await target.click()

    after = await browser.act(click, press)
    text = after.render()
    assert bs.PAGE_CHANGES_MARK in text and "Clicked OK" in text
    assert "light-years" not in text and "Get CSV" not in text  # the unchanged page is not sent again
    assert "Clicked OK" in after.tree              # the full tree is still there for callers
    assert len(text) < len(view.render())

    async def hover(page, target):
        await target.hover()

    idle = await browser.act(hover, press)         # the old ref still works
    assert "nothing visible changed" in idle.render()

    read = await browser.snapshot()                # reading the page: always whole
    assert read.changes is None and "Hello Probe" in read.render()


async def test_a_new_tab_or_diffs_off_give_the_whole_page(browser, site, settings):
    view = await browser.navigate(site + "/probe.html")

    async def click(page, target):
        await target.click()

    opened = await browser.act(click, ref_of(view.tree, r'link "Open dest"'))
    assert opened.changes is None and "Arrived" in opened.render()

    await browser.navigate(site + "/probe.html")
    view = await browser.snapshot()
    off = settings.model_copy(update={"browser_snapshot_diff": False})
    whole = await browser.act(click, ref_of(view.tree, r'button "Press Me"'), sandbox=off)
    assert whole.changes is None and "Hello Probe" in whole.render()


async def test_on_a_long_page_an_action_costs_a_fraction(browser, site):
    view = await browser.navigate(site + "/long.html")

    async def click(page, target):
        await target.click()

    after = await browser.act(click, ref_of(view.tree, r'button "Save"'))
    assert "Saved" in after.render()
    assert len(after.render()) < len(view.render()) / 20
