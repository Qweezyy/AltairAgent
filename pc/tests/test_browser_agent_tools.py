"""The browser tools as the agent uses them: JavaScript, text, partial reads, forms, batches,
clicks by coordinates, console and network logs, zoomed screenshots — and the settle logic that
keeps every action fast. A real Chrome/Edge drives real pages; skipped without one."""

from __future__ import annotations

import functools
import http.server
import json
import re
import threading
import time
from pathlib import Path

import pytest

import core.browser_session as bs
from core.browser_session import AgentBrowser
from core.tools.base import ToolContext
from core.tools.builtin import browser_tools as bt

FORM = """<!doctype html><html><head><meta charset="utf-8"><title>Form Page</title></head><body>
<nav><a href="#a">Home</a><a href="#b">Pricing</a></nav>
<main>
  <h1>Order</h1>
  <p id="intro">Fill in the order form below.</p>
  <p>""" + "Terms of the order apply to every purchase. " * 30 + """</p>
  <form id="order" aria-label="Order form" onsubmit="event.preventDefault(); document.getElementById('out').textContent =
      'Sent: ' + [name.value, size.value, gift.checked].join('/')">
    <label>Name <input id="name" name="name"></label>
    <label>Size <select id="size" name="size"><option>Small</option><option>Large</option></select></label>
    <label><input id="gift" type="checkbox"> Gift wrap</label>
    <button type="submit">Send order</button>
  </form>
  <p id="out">Not sent</p>
  <button id="late" onclick="setTimeout(() => document.getElementById('out').textContent = 'Late change', 200)">Late</button>
  <button id="fetcher" onclick="fetch('/slow.json').then(r => r.json()).then(d => document.getElementById('out').textContent = 'Got ' + d.value)">Load data</button>
  <button id="logger" onclick="console.log('hello from page'); console.error('broken thing'); fetch('/missing.json')">Log</button>
  <button id="go" onclick="setTimeout(() => location.hash = 'moved', 300)">Move</button>
  <table id="prices" aria-label="Prices"><tr><td>Small</td><td>10</td></tr><tr><td>Large</td><td>25</td></tr></table>
  <div id="box" style="height:120px; overflow:auto; border:1px solid"><div style="height:2000px">
    <p style="margin-top:1800px">Deep inside the box</p></div></div>
</main>
<footer>Footer text that is not main content</footer>
<script>setInterval(() => fetch('/ping.json?' + Date.now()).catch(() => {}), 300);</script>
</body></html>"""

COVERED = """<!doctype html><meta charset="utf-8"><title>Covered</title>
<button onclick="document.title='clicked'">Hidden target</button>
<div style="position:fixed; inset:0; background:rgba(0,0,0,.5)">Cookie banner</div>"""

CANVAS = """<!doctype html><meta charset="utf-8"><title>Canvas</title><style>body{margin:0}</style>
<canvas id="c" width="400" height="300" style="display:block"></canvas><p id="hit">none</p>
<script>
const c = document.getElementById('c'), g = c.getContext('2d');
g.fillStyle = 'red'; g.fillRect(250, 150, 60, 60);
c.addEventListener('click', e => { document.getElementById('hit').textContent =
  (e.offsetX >= 250 && e.offsetX <= 310 && e.offsetY >= 150 && e.offsetY <= 210) ? 'square' : 'miss'; });
</script>"""


class _Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - the http.server API
        if self.path.startswith("/slow.json"):
            time.sleep(0.6)  # an app waiting for its server: the action must wait for it too
            return self._json({"value": 42})
        if self.path.startswith("/ping.json"):
            return self._json({"ok": True})
        if self.path.startswith("/missing.json"):
            self.send_response(404)
            self.end_headers()
            return None
        return super().do_GET()

    def _json(self, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        return


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("tools_site")
    for name, html in (("form.html", FORM), ("covered.html", COVERED), ("canvas.html", CANVAS)):
        (root / name).write_text(html, encoding="utf-8")
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
    monkeypatch.setattr(bt, "get_agent_browser", lambda: b)
    monkeypatch.setattr(bs, "get_agent_browser", lambda: b)
    try:
        yield b
    finally:
        await b.close()


@pytest.fixture()
def ctx(settings):
    return ToolContext(settings=settings)


def ref_of(tree: str, pattern: str) -> str:
    match = re.search(pattern + r'[^\n]*?\[ref=(\w+)\]', tree)
    assert match, f"no element matching {pattern!r} in:\n{tree[:2000]}"
    return match.group(1)


async def _open(browser, site, page="form.html"):
    return await browser.navigate(f"{site}/{page}")


async def _text(browser, selector: str) -> str:
    return await (await browser.page()).locator(selector).inner_text()


# ---------------------------------------------------------------- settling


async def test_an_action_on_a_page_that_never_goes_quiet_is_fast(browser, site, ctx):
    """The page polls its server forever: waiting for network idle cost 2.5 s per action."""
    view = await _open(browser, site)
    started = time.perf_counter()
    out = await bt.BrowserClickTool().run(bt.ClickArgs(ref=ref_of(view.tree, r'button "Late"')), ctx)
    took = time.perf_counter() - started
    assert "Late change" in out  # a change 200 ms after the click is still waited for
    assert took < 1.6, f"a click took {took:.2f} s"


async def test_an_action_waits_for_the_data_the_page_fetches(browser, site, ctx):
    view = await _open(browser, site)
    out = await bt.BrowserClickTool().run(bt.ClickArgs(ref=ref_of(view.tree, r'button "Load data"')), ctx)
    assert "Got 42" in out


async def test_settle_gives_up_on_a_page_that_keeps_changing(browser, site):
    await _open(browser, site)
    page = await browser.page()
    await page.evaluate("setInterval(() => document.getElementById('out').textContent = Math.random(), 50)")
    started = time.perf_counter()
    assert await browser._settle(page, 1.0) is False
    assert time.perf_counter() - started < 1.6


# ---------------------------------------------------------------- JavaScript


async def test_js_returns_the_last_expression_with_top_level_await(browser, site, ctx):
    await _open(browser, site)
    rows = await bt.BrowserJsTool().run(bt.JsArgs(
        code="[...document.querySelectorAll('#prices tr')].map(r => [...r.cells].map(c => c.innerText))"), ctx)
    assert '[["Small","10"],["Large","25"]]' in rows
    awaited = await bt.BrowserJsTool().run(bt.JsArgs(
        code="const r = await fetch('/slow.json'); const d = await r.json(); d.value * 2"), ctx)
    assert "Result: 84" in awaited
    assert "Result: Form Page" in await bt.BrowserJsTool().run(bt.JsArgs(code="document.title"), ctx)
    assert "Result: undefined" in await bt.BrowserJsTool().run(bt.JsArgs(code="let x = 1;"), ctx)


async def test_js_shows_elements_as_html_and_survives_cycles(browser, site, ctx):
    await _open(browser, site)
    element = await bt.BrowserJsTool().run(bt.JsArgs(code="document.getElementById('intro')"), ctx)
    assert '<p id="intro">Fill in the order form below.</p>' in element
    cyclic = await bt.BrowserJsTool().run(bt.JsArgs(code="const a = {n: 1}; a.self = a; a"), ctx)
    assert '"self":"[circular]"' in cyclic
    mixed = await bt.BrowserJsTool().run(bt.JsArgs(code="({m: new Map([['k', 1]]), s: new Set([2]), f() {}})"), ctx)
    assert '"m":{"k":1}' in mixed and '"s":[2]' in mixed and "[function f]" in mixed


async def test_js_error_is_reported_not_raised_as_a_crash(browser, site, ctx):
    await _open(browser, site)
    result = await bt.BrowserJsTool().invoke({"code": "null.boom"}, ctx.__class__(
        settings=ctx.settings.model_copy(update={"approval_mode": "bypass"})))
    assert not result.ok and "The script threw" in result.content and "TypeError" in result.content


async def test_js_that_changes_the_page_reports_it(browser, site, ctx):
    await _open(browser, site)
    out = await bt.BrowserJsTool().run(bt.JsArgs(code="location.hash = 'x'; confirm('Really?')"), ctx)
    assert "Result: false" in out  # the agent's dialogs are answered for it (dismissed)
    assert "navigated from" in out and "confirm dialog" in out


async def test_js_asks_with_the_code_and_the_page(browser, site):
    await _open(browser, site)
    reason = bt.BrowserJsTool().approval_reason(bt.JsArgs(code="document.cookie"))
    assert "document.cookie" in reason and "/form.html" in reason


# ---------------------------------------------------------------- reading


async def test_text_reads_the_main_content(browser, site, ctx):
    await _open(browser, site)
    out = await bt.BrowserTextTool().run(bt.TextArgs(), ctx)
    assert "Fill in the order form below." in out
    assert "Footer text" not in out and "Pricing" not in out  # outside <main>
    assert "[ref=" not in out


async def test_text_comes_in_parts_and_by_element(browser, site, ctx):
    await _open(browser, site)
    first = await bt.BrowserTextTool().run(bt.TextArgs(max_chars=500), ctx)
    match = re.search(r"next part: start=(\d+)", first)
    assert match
    rest = await bt.BrowserTextTool().run(bt.TextArgs(start=int(match.group(1)), max_chars=5000), ctx)
    assert "Deep inside the box" in rest and "Deep inside the box" not in first
    tree = (await browser.snapshot()).tree
    table = ref_of(tree, r'table "Prices"')
    only = await bt.BrowserTextTool().run(bt.TextArgs(ref=table), ctx)
    assert "Large\t25" in only and "Order" not in only.split("\n\n", 1)[1]


async def test_read_parts_of_a_page(browser, site, ctx):
    view = await _open(browser, site)
    whole = len(view.tree)
    controls = await bt.BrowserReadTool().run(bt.ReadArgs(interactive=True), ctx)
    assert 'button "Send order"' in controls and 'heading "Order"' in controls
    assert "Fill in the order form" not in controls  # plain text is left out
    assert len(controls) < whole
    form = ref_of(view.tree, r'form "Order form"')
    part = await bt.BrowserReadTool().run(bt.ReadArgs(ref=form), ctx)
    assert "Part of the page" in part and 'textbox "Name"' in part and "Load data" not in part
    # A ref from the part is the same element the whole page names.
    assert ref_of(part, r'button "Send order"') == ref_of(view.tree, r'button "Send order"')


async def test_reading_a_part_keeps_the_diff_base(browser, site, ctx):
    view = await _open(browser, site)
    await bt.BrowserReadTool().run(bt.ReadArgs(interactive=True), ctx)
    out = await bt.BrowserClickTool().run(bt.ClickArgs(ref=ref_of(view.tree, r'button "Late"')), ctx)
    assert bs.PAGE_CHANGES_MARK in out and "Late change" in out


# ---------------------------------------------------------------- forms and batches


async def test_fill_sets_a_whole_form_in_one_call(browser, site, ctx):
    view = await _open(browser, site)
    out = await bt.BrowserFillTool().run(bt.FillArgs(fields=[
        bt.FieldValue(ref=ref_of(view.tree, r'textbox "Name"'), value="Ada"),
        bt.FieldValue(ref=ref_of(view.tree, r'combobox "Size"'), value="Large"),
        bt.FieldValue(ref=ref_of(view.tree, r'checkbox "Gift wrap"'), value=True),
    ]), ctx)
    assert "filled 3 field(s)" in out
    page = await browser.page()
    assert await page.evaluate("['name', 'size', 'gift'].map(i => { const e = document.getElementById(i); return e.type === 'checkbox' ? e.checked : e.value; })") == ["Ada", "Large", True]


async def test_batch_fills_sends_and_reads_the_page_once(browser, site, ctx, monkeypatch):
    view = await _open(browser, site)
    reads = 0
    real = browser.snapshot

    async def counting(**kwargs):
        nonlocal reads
        reads += 1
        return await real(**kwargs)

    monkeypatch.setattr(browser, "snapshot", counting)
    args = bt.BatchArgs.model_validate({"steps": [
        {"tool": "type", "args": {"ref": ref_of(view.tree, r'textbox "Name"'), "text": "Grace"}},
        {"tool": "browser_select", "args": {"ref": ref_of(view.tree, r'combobox "Size"'), "values": ["Small"]}},
        {"tool": "click", "args": {"ref": ref_of(view.tree, r'button "Send order"')}},
        {"tool": "wait", "args": {"text": "Sent:"}},
        {"tool": "js", "args": {"code": "document.getElementById('out').textContent"}},
    ]})
    out = await bt.BrowserBatchTool().run(args, ctx)
    assert reads == 1
    assert "Sent: Grace/Small/false" in out
    assert re.search(r"5\. js .* — ok\n\s+Sent: Grace/Small/false", out)
    assert bs.PAGE_CHANGES_MARK in out  # same page: only what changed


async def test_batch_stops_at_the_first_failure_and_shows_the_page(browser, site, ctx):
    view = await _open(browser, site)
    args = bt.BatchArgs.model_validate({"steps": [
        {"tool": "click", "args": {"ref": ref_of(view.tree, r'button "Late"')}},
        {"tool": "click", "args": {"ref": "e99999"}},
        {"tool": "click", "args": {"ref": ref_of(view.tree, r'button "Send order"')}},
    ]})
    out = await bt.BrowserBatchTool().run(args, ctx)
    assert "1. click" in out and "— ok" in out
    assert "2. click ref=e99999 — FAILED" in out and "(steps 3–3 were not run)" in out
    assert "Late change" in out and "Sent:" not in out


async def test_batch_steps_are_checked_before_anything_runs(browser, site, ctx):
    settings = ctx.settings.model_copy(update={"approval_mode": "bypass"})
    bad_tool = await bt.BrowserBatchTool().invoke({"steps": [{"tool": "tabs", "args": {}}]}, ToolContext(settings=settings))
    assert not bad_tool.ok and "cannot be a step" in bad_tool.content
    bad_args = await bt.BrowserBatchTool().invoke(
        {"steps": [{"tool": "click", "args": {}}, {"tool": "type", "args": {"ref": "e1"}}]}, ToolContext(settings=settings))
    assert not bad_args.ok and "step 1" in bad_args.content


async def test_batch_asks_once_with_every_step(browser, site, ctx):
    tool = bt.BrowserBatchTool()
    acting = bt.BatchArgs.model_validate({"steps": [
        {"tool": "type", "args": {"ref": "e3", "text": "secret plan"}}, {"tool": "click", "args": {"ref": "e7"}}]})
    reason = tool.approval_reason(acting)
    assert "1." in reason and "secret plan" in reason and "2." in reason and "e7" in reason
    assert tool.auto_verdict(acting, ctx) == "ask"
    looking = bt.BatchArgs.model_validate({"steps": [
        {"tool": "scroll", "args": {}}, {"tool": "hover", "args": {"ref": "e2"}}, {"tool": "wait", "args": {"seconds": 1}}]})
    assert tool.auto_verdict(looking, ctx) == "allow"


# ---------------------------------------------------------------- pointer and coordinates


async def test_click_by_coordinates_hits_a_canvas(browser, site, ctx):
    await _open(browser, site, "canvas.html")
    await bt.BrowserClickTool().run(bt.ClickArgs(x=280, y=180), ctx)
    assert await _text(browser, "#hit") == "square"
    with pytest.raises(ValueError):
        bt.ClickArgs(x=10)


async def test_a_covered_element_says_what_covers_it_quickly(browser, site, ctx):
    view = await _open(browser, site, "covered.html")
    started = time.perf_counter()
    result = await bt.BrowserClickTool().invoke(
        {"ref": ref_of(view.tree, r'button "Hidden target"')},
        ToolContext(settings=ctx.settings.model_copy(update={"approval_mode": "bypass"})))
    assert not result.ok
    assert "intercepts pointer events" in result.content and "covers the element" in result.content
    assert time.perf_counter() - started < 12


async def test_scroll_moves_the_panel_under_the_point(browser, site, ctx):
    await _open(browser, site)
    page = await browser.page()
    box = await page.locator("#box").bounding_box()
    await bt.BrowserScrollTool().run(bt.ScrollArgs(x=box["x"] + 20, y=box["y"] + 20, amount=5), ctx)
    assert await page.evaluate("document.getElementById('box').scrollTop") > 500
    assert await page.evaluate("scrollY") == 0  # the page itself stayed put


async def test_wait_for_the_address_and_a_clear_timeout(browser, site, ctx):
    view = await _open(browser, site)
    await bt.BrowserClickTool().run(bt.ClickArgs(ref=ref_of(view.tree, r'button "Move"')), ctx)
    out = await bt.BrowserWaitTool().run(bt.WaitArgs(url="#moved"), ctx)
    assert "#moved" in out
    result = await bt.BrowserWaitTool().invoke({"text": "Never appears", "timeout": 1}, ctx)
    assert not result.ok and "Waited 1 s: 'Never appears' did not appear" in result.content


# ---------------------------------------------------------------- console and network


async def test_console_and_requests_are_logged(browser, site, ctx):
    view = await _open(browser, site)
    await bt.BrowserClickTool().run(bt.ClickArgs(ref=ref_of(view.tree, r'button "Log"')), ctx)
    console = await bt.BrowserConsoleTool().run(bt.ConsoleArgs(), ctx)
    assert "[log] hello from page" in console and "[error] broken thing" in console
    errors = await bt.BrowserConsoleTool().run(bt.ConsoleArgs(only_errors=True), ctx)
    assert "broken thing" in errors and "hello from page" not in errors
    failed = await bt.BrowserRequestsTool().run(bt.RequestsArgs(only_failed=True), ctx)
    assert re.search(r"\[r\d+\] GET 404( \([^)]*\))? fetch \d+ms http://127\.0\.0\.1:\d+/missing\.json", failed)
    listed = [ln for ln in failed.splitlines() if ln.startswith("[r")]
    assert len(listed) == 1  # the page and its polling went fine: only the 404 is listed


async def test_console_says_which_step_a_message_came_from(browser, site, ctx):
    """Live: the agent called an error its own form submit caused one "from page load"."""
    view = await _open(browser, site)
    await bt.BrowserClickTool().run(bt.ClickArgs(ref=ref_of(view.tree, r'button "Log"')), ctx)
    out = await bt.BrowserConsoleTool().run(bt.ConsoleArgs(only_errors=True), ctx)
    lines = [ln for ln in out.splitlines() if re.match(r"\d\d:\d\d:\d\d\.\d \[", ln)]
    kinds = [re.search(r"\[(\w+)\]", ln).group(1) for ln in lines]
    caused = next(i for i, ln in enumerate(lines) if "broken thing" in ln)
    assert kinds.index("page") < kinds.index("action") < caused
    assert "opened http://127.0.0.1" in out and "click ref=" in out and out.startswith(
        "[EXTERNAL") and "Now " in out
    assert "hello from page" not in out  # the filter still applies to the messages themselves


async def test_a_request_shows_its_response_body(browser, site, ctx):
    view = await _open(browser, site)
    await bt.BrowserClickTool().run(bt.ClickArgs(ref=ref_of(view.tree, r'button "Load data"')), ctx)
    listing = await bt.BrowserRequestsTool().run(bt.RequestsArgs(url_pattern="slow.json"), ctx)
    rid = re.search(r"\[(r\d+)\] GET 200 fetch", listing).group(1)
    detail = await bt.BrowserRequestsTool().run(bt.RequestsArgs(id=rid), ctx)
    assert "application/json" in detail and '{"value": 42}' in detail


async def test_clear_shows_only_what_comes_next(browser, site, ctx):
    await _open(browser, site)
    page = await browser.page()
    await page.evaluate("console.log('before')")
    await bt.BrowserConsoleTool().run(bt.ConsoleArgs(clear=True), ctx)
    await page.evaluate("console.log('after')")
    out = await bt.BrowserConsoleTool().run(bt.ConsoleArgs(), ctx)
    assert "after" in out and "before" not in out


# ---------------------------------------------------------------- screenshots


async def test_screenshot_is_in_css_pixels_and_zooms(browser, site, ctx):
    await _open(browser, site, "canvas.html")
    page = await browser.page()
    viewport = await page.evaluate("[innerWidth, innerHeight]")
    whole = await browser.screenshot_png()
    assert list(bs.png_size(whole)) == viewport  # a point on it is a click point
    zoom = await browser.screenshot_png(region=(250, 150, 310, 210))
    assert bs.png_size(zoom)[0] >= 4 * 60  # magnified, not a 60-pixel thumbnail
    out = await bt.BrowserScreenshotTool().run(bt.ScreenshotArgs(region=[250, 150, 310, 210]), ctx)
    assert "a zoom (x4.0) of the region 250,150–310,210" in out.content
    with pytest.raises(ValueError):
        bt.ScreenshotArgs(region=[1, 2, 3])


# ---------------------------------------------------------------- wiring


def test_new_tools_are_registered_as_one_family():
    from core.tools.builtin import builtin_tools
    from core.tools.deferred import family

    names = {t.name for t in builtin_tools()}
    new = {"browser_text", "browser_fill", "browser_js", "browser_batch", "browser_console", "browser_requests"}
    assert new <= names
    assert {family(n) for n in new} == {"browser"}


def test_data_from_pages_outlives_page_snapshots():
    """A newer page makes older snapshots stale, never the data the agent took from a page."""
    from core.agent.session import FULL_PAGE_MARK, Session

    s = Session()
    data = "Result: " + "x" * 3000
    for i in range(4):
        s.messages.append({"role": "tool", "name": "browser_navigate", "tool_call_id": f"n{i}",
                           "content": f"URL: u{i}\n\n{FULL_PAGE_MARK}):\n" + "y" * 3000})
        s.messages.append({"role": "tool", "name": "browser_js", "tool_call_id": f"j{i}", "content": data})
    dropped, _ = s.supersede_page_states(keep=2, min_free_chars=0)
    assert dropped == 2
    assert all(m["content"] == data and "_view" not in m for m in s.messages if m["name"] == "browser_js")
