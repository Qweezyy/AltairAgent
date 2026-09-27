"""browser_upload fills file fields without the OS file dialog, on a real headless Chromium.

The embedded browser is driven over CDP, where the "file chooser opened" event never arrives
(the agent timed out six times on habr.com and handed every upload to the user). These pages
reproduce the shapes sites use: a hidden input behind a styled button, and an input the page
creates on click and never inserts.
"""

from __future__ import annotations

import pytest

from core.tools.builtin.browser_tools import upload_files

playwright = pytest.importorskip("playwright.async_api")

PAGE = """
<label for="avatar" id="styled" class="btn">Upload avatar</label>
<input type="file" id="avatar" style="display:none">

<div class="card"><button id="near">Choose</button><input type="file" id="near-input" hidden></div>

<button id="dynamic">Add image</button>

<input type="file" id="plain">

<script>
  window.got = {};
  const note = (id) => (e) => { window.got[id] = [...e.target.files].map((f) => f.name); };
  document.getElementById("avatar").addEventListener("change", note("avatar"));
  document.getElementById("near-input").addEventListener("change", note("near"));
  document.getElementById("plain").addEventListener("change", note("plain"));
  // Like editors do: a throwaway input, clicked, never put in the page.
  document.getElementById("dynamic").addEventListener("click", () => {
    const input = document.createElement("input");
    input.type = "file";
    input.onchange = () => { window.got.dynamic = [...input.files].map((f) => f.name); };
    input.click();
  });
</script>
"""


@pytest.fixture()
async def page():
    try:
        async with playwright.async_playwright() as p:
            browser = await p.chromium.launch()
            pg = await browser.new_page()
            await pg.set_content(PAGE)
            yield pg
            await browser.close()
    except Exception as exc:  # noqa: BLE001 - no browser on this machine
        pytest.skip(f"no headless Chromium: {exc}")


@pytest.fixture()
def files(tmp_path):
    a = tmp_path / "avatar.png"
    a.write_bytes(b"\x89PNG\r\n")
    return [str(a)]


@pytest.mark.parametrize(("selector", "key", "how"), [
    ("#styled", "avatar", "input"),       # a label for a hidden input
    ("#near", "near", "input"),           # a button next to a hidden input
    ("#plain", "plain", "input"),         # the input itself
    ("#dynamic", "dynamic", "caught"),    # an input created on click, never in the page
])
async def test_files_reach_the_page_without_a_dialog(page, files, selector, key, how):
    assert await upload_files(page, page.locator(selector), files) == how
    assert await page.evaluate(f"window.got[{key!r}]") == ["avatar.png"]


async def test_click_hook_is_removed_afterwards(page, files):
    await upload_files(page, page.locator("#dynamic"), files)
    assert await page.evaluate("window.__altairUpload === undefined")
    # The page's own file inputs behave normally again (the user may upload by hand).
    assert await page.evaluate("HTMLInputElement.prototype.click.toString().includes('native code')")


async def test_no_upload_field_is_a_clear_error(page, files):
    from core.errors import ToolError

    await page.set_content("<button id='x'>Nothing here</button>")
    with pytest.raises(ToolError, match="No file field"):
        await upload_files(page, page.locator("#x"), files)
