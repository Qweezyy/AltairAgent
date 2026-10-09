"""Forms the way real sites build them, where the agent got stuck live (a Kwork offer form took
~100 steps): a rich-text editor whose labelled <textarea> is a hidden copy, a counter that only
updates on key up, editors that take text only as a paste, masked inputs, drop-downs made of
divs, chat boxes where Enter sends, and a button that navigates a moment after the click."""

from __future__ import annotations

import functools
import http.server
import re
import threading
from pathlib import Path

import pytest

import core.browser_session as bs
from core.browser_session import AgentBrowser, compact_tree
from core.tools.base import ToolContext
from core.tools.builtin import browser_tools as bt

# Trumbowyg-like: the <label> points at a 1x1 invisible textarea holding the editor's HTML; the
# visible contenteditable shows a placeholder; the counter and the copy update on input/keyup.
OFFER = """<!doctype html><html><head><meta charset="utf-8"><title>Offer</title><style>
.box { position: relative; border: 1px solid #ccc; margin: 8px 0; }
.box textarea { position: absolute; top: 1px; left: 1px; width: 1px; height: 1px; opacity: 0; border: 0; padding: 0; }
.editor { min-height: 40px; padding: 6px; }
.editor:empty::before { content: attr(placeholder); color: #999; }
.err { color: red; }
</style></head><body><main><h1>Make an offer</h1>
<form id="f" onsubmit="event.preventDefault(); check()">
  <label for="title">Order title</label>
  <div class="box"><div class="editor" id="ed" contenteditable="true" placeholder="Order title"></div>
    <textarea id="title" name="title"><div><br></div></textarea></div>
  <p id="count">0 of 70 characters</p>
  <label>Price <input id="price" inputmode="numeric"></label>
  <div id="term" role="combobox" aria-label="Term" aria-expanded="false" tabindex="0">Choose a term</div>
  <div id="list" role="listbox" hidden><div role="option">1 day</div><div role="option">3 days</div>
    <div role="option">7 days</div></div>
  <button type="submit">Offer</button>
  <p id="result" class="err"></p>
</form></main>
<script>
const ed = document.getElementById('ed'), copy = document.getElementById('title');
let state = '';  // what the form believes the title is
const sync = () => { copy.value = ed.innerHTML; };
ed.addEventListener('input', sync);
ed.addEventListener('keyup', () => { state = ed.innerText.trim();
  document.getElementById('count').textContent = state.length + ' of 70 characters'; });
const price = document.getElementById('price');
price.addEventListener('input', () => { const d = price.value.replace(/\\D/g, '');
  price.value = d.replace(/\\B(?=(\\d{3})+(?!\\d))/g, ' '); });
const term = document.getElementById('term'), list = document.getElementById('list');
term.addEventListener('click', () => { list.hidden = false; term.setAttribute('aria-expanded', 'true'); });
list.addEventListener('click', e => { if (e.target.getAttribute('role') !== 'option') return;
  term.textContent = e.target.textContent; list.hidden = true; term.setAttribute('aria-expanded', 'false'); });
function check() {
  const problems = [];
  if (!state) problems.push('Enter the order title');
  if (term.textContent === 'Choose a term') problems.push('Choose a term');
  document.getElementById('result').textContent = problems.length ? problems.join('; ') : 'Sent: ' + state;
  if (!problems.length) setTimeout(() => location.href = '/done.html', 150);
}
</script></body></html>"""

# Editors with their own ideas about input.
EDITORS = """<!doctype html><html><head><meta charset="utf-8"><title>Editors</title></head><body><main>
<div id="pasteonly" contenteditable="true" aria-label="Paste only" style="min-height:30px;border:1px solid"></div>
<div id="stubborn" contenteditable="true" aria-label="Stubborn" style="min-height:30px;border:1px solid"></div>
<div id="chat" contenteditable="true" aria-label="Message" style="min-height:30px;border:1px solid"></div>
<p id="sent">nothing sent</p>
</main><script>
// Takes no typed input at all, only pastes (it rebuilds its content from the clipboard data).
const p = document.getElementById('pasteonly');
p.addEventListener('beforeinput', e => e.preventDefault());
p.addEventListener('keydown', e => { if (e.key.length === 1) e.preventDefault(); });
p.addEventListener('paste', e => { e.preventDefault(); p.textContent = e.clipboardData.getData('text/plain'); });
// Refuses everything.
const s = document.getElementById('stubborn');
['beforeinput', 'paste'].forEach(t => s.addEventListener(t, e => e.preventDefault()));
s.addEventListener('keydown', e => { if (!e.ctrlKey) e.preventDefault(); });
// A chat box: Enter sends, Shift+Enter breaks the line.
const c = document.getElementById('chat');
c.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault();
  document.getElementById('sent').textContent = 'sent: ' + c.innerText.replace(/\\n/g, ' | '); } });
</script></body></html>"""


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        return


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("editors_site")
    (root / "offer.html").write_text(OFFER, encoding="utf-8")
    (root / "editors.html").write_text(EDITORS, encoding="utf-8")
    (root / "done.html").write_text("<!doctype html><meta charset=utf-8><title>Done</title><h1>Offer sent</h1>",
                                    encoding="utf-8")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(root)))
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
    try:
        yield b
    finally:
        await b.close()


@pytest.fixture()
def ctx(settings):
    return ToolContext(settings=settings)


def ref_of(tree: str, pattern: str) -> str:
    match = re.search(pattern + r'[^\n]*?\[ref=(\w+)\]', tree)
    assert match, f"no element matching {pattern!r} in:\n{tree[:2500]}"
    return match.group(1)


async def _ref_of_copy(browser) -> str:
    raw = await (await browser.page()).aria_snapshot(mode="ai")
    match = re.search(r'textbox \[ref=(\w+)\]: "?<div>', raw)
    assert match, raw
    return match.group(1)


async def _js(browser, code: str):
    return await (await browser.page()).evaluate(code)


async def test_the_tree_shows_the_editor_under_its_label_and_not_its_copy(browser, site):
    view = await browser.navigate(site + "/offer.html")
    assert view.tree.count('textbox "Order title"') == 1
    assert "<div><br>" not in view.tree  # the copy holding the editor's HTML is gone
    page = await browser.page()
    ref = ref_of(view.tree, r'textbox "Order title"')
    assert await page.locator(f"aria-ref={ref}").get_attribute("id") == "ed"  # the visible editor
    # The site's own markup is left alone apart from ARIA: the copy is still a working field.
    assert await _js(browser, "document.getElementById('title').inert") is False


async def test_the_labelled_hidden_copy_leads_to_the_visible_editor(browser, site, ctx):
    view = await browser.navigate(site + "/offer.html")
    assert view.title == "Offer"
    # A ref to the copy itself (an older snapshot, a model that picked it): still lands right.
    title = await _ref_of_copy(browser)
    assert await _js(browser, "getComputedStyle(document.getElementById('title')).opacity") == "0"
    out = await bt.BrowserTypeTool().run(bt.TypeArgs(ref=title, text="Telegram bot with AI"), ctx)
    assert "typed into the visible editor" in out and "WARNING" not in out
    assert await _js(browser, "document.getElementById('ed').innerText") == "Telegram bot with AI"
    assert await _js(browser, "document.getElementById('count').textContent") == "20 of 70 characters"
    assert "<div><br" not in await _js(browser, "document.getElementById('title').value")  # synced, not garbled


async def test_replacing_the_text_of_an_editor(browser, site, ctx):
    view = await browser.navigate(site + "/offer.html")
    title = ref_of(view.tree, r'textbox "Order title"')
    await bt.BrowserTypeTool().run(bt.TypeArgs(ref=title, text="First title"), ctx)
    await bt.BrowserTypeTool().run(bt.TypeArgs(ref=title, text="Second"), ctx)
    assert await _js(browser, "document.getElementById('ed').innerText") == "Second"
    await bt.BrowserTypeTool().run(bt.TypeArgs(ref=title, text=" and more", replace=False), ctx)
    assert await _js(browser, "document.getElementById('ed').innerText") == "Second and more"
    assert await _js(browser, "document.getElementById('count').textContent") == "15 of 70 characters"


async def test_one_batch_fills_and_sends_the_whole_offer(browser, site, ctx):
    """The whole Kwork form in one call: editor, masked price, a div drop-down, submit — and the
    page the site moves to 150 ms after the click."""
    view = await browser.navigate(site + "/offer.html")
    args = bt.BatchArgs.model_validate({"steps": [
        {"tool": "fill", "args": {"fields": [
            {"ref": ref_of(view.tree, r'textbox "Order title"'), "value": "Parser of eBay photos"},
            {"ref": ref_of(view.tree, r'textbox "Price"'), "value": 9000},
            {"ref": ref_of(view.tree, r'combobox "Term"'), "value": "3 days"},
        ]}},
        {"tool": "click", "args": {"ref": ref_of(view.tree, r'button "Offer"')}},
    ]})
    out = await bt.BrowserBatchTool().run(args, ctx)
    assert "WARNING" not in out, out
    assert "FAILED" not in out, out
    assert "Title: Done" in out and "Offer sent" in out  # the page it went to, not the old form


async def test_a_masked_field_is_not_reported_as_wrong(browser, site, ctx):
    view = await browser.navigate(site + "/offer.html")
    out = await bt.BrowserTypeTool().run(bt.TypeArgs(ref=ref_of(view.tree, r'textbox "Price"'), text="9000"), ctx)
    assert "WARNING" not in out
    assert await _js(browser, "document.getElementById('price').value") == "9 000"


async def test_an_editor_that_only_takes_pastes(browser, site, ctx):
    view = await browser.navigate(site + "/editors.html")
    out = await bt.BrowserTypeTool().run(bt.TypeArgs(ref=ref_of(view.tree, r'textbox "Paste only"'),
                                                     text="Pasted words"), ctx)
    assert "as a paste" in out and "WARNING" not in out
    assert await _js(browser, "document.getElementById('pasteonly').innerText") == "Pasted words"


async def test_a_field_that_takes_nothing_is_reported(browser, site, ctx):
    view = await browser.navigate(site + "/editors.html")
    out = await bt.BrowserTypeTool().run(bt.TypeArgs(ref=ref_of(view.tree, r'textbox "Stubborn"'),
                                                     text="Will not stick"), ctx)
    assert "WARNING: the field shows «»" in out


async def test_several_lines_in_a_chat_box_are_not_sent_early(browser, site, ctx):
    view = await browser.navigate(site + "/editors.html")
    box = ref_of(view.tree, r'textbox "Message"')
    await bt.BrowserTypeTool().run(bt.TypeArgs(ref=box, text="Hello!\nThe price is 2500."), ctx)
    assert await _js(browser, "document.getElementById('sent').textContent") == "nothing sent"
    await bt.BrowserTypeTool().run(bt.TypeArgs(ref=box, text="Hello!\nThe price is 2500.", submit=True), ctx)
    assert await _js(browser, "document.getElementById('sent').textContent") == "sent: Hello! | The price is 2500."


def test_icon_font_glyphs_leave_the_tree():
    tree = "\n".join([
        "- generic [active] [ref=f2e1]:",
        "  - generic:    ",       # a toolbar of icons: nothing to read
        "  - text:   ",
        "  - generic [ref=e4] [cursor=pointer]: ",  # an icon button: still clickable
        "  - button \"Open menu\" [ref=f2e11]",
        "  - text: Rated 5  stars",
    ])
    out = compact_tree(tree)
    assert not re.search("[-]", out)
    assert "- text:\n" not in out + "\n" and "generic:\n" not in out + "\n"
    assert "generic [ref=e4] [cursor=pointer]: (icon)" in out
    assert 'button "Open menu" [ref=f2e11]' in out and "text: Rated 5 stars" in out
