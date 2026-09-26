"""edit_file: exact replacement plus a unique whitespace-tolerant fallback.

A whitespace slip in old_text is the most common reason exact-replace edits fail
(and models then loop re-reading the file). A unique loose match is unambiguous,
so it is applied; an ambiguous one is refused rather than guessed.
"""

from __future__ import annotations

from core.tools.builtin.files import EditFileTool

SRC = """class A:
    def run(self):
        value = compute()
        return value
"""


async def test_exact_replacement(ctx):
    target = ctx.settings.workspace / "m.py"
    target.write_text(SRC, encoding="utf-8")

    result = await EditFileTool().invoke(
        {"path": "m.py", "old_text": "        value = compute()", "new_text": "        value = compute(2)"}, ctx
    )

    assert result.ok, result.content
    assert "value = compute(2)" in target.read_text(encoding="utf-8")


async def test_wrong_indentation_is_matched_and_reindented(ctx):
    target = ctx.settings.workspace / "m.py"
    target.write_text(SRC, encoding="utf-8")

    # The model dropped the indentation of both old and new text.
    result = await EditFileTool().invoke(
        {
            "path": "m.py",
            "old_text": "value = compute()\nreturn value",
            "new_text": "value = compute()\nvalue += 1\nreturn value",
        },
        ctx,
    )

    assert result.ok, result.content
    assert "ignoring indentation" in result.content
    text = target.read_text(encoding="utf-8")
    assert "        value += 1\n        return value\n" in text  # file indentation restored
    assert text.startswith("class A:\n    def run(self):\n")


async def test_ambiguous_loose_match_is_refused(ctx):
    target = ctx.settings.workspace / "m.py"
    original = "if a:\n    x = 1\nif b:\n        x = 1\n"
    target.write_text(original, encoding="utf-8")

    result = await EditFileTool().invoke({"path": "m.py", "old_text": "x = 1", "new_text": "x = 2"}, ctx)

    # "x = 1" is a substring of both lines, so the exact path sees 2 occurrences.
    assert not result.ok
    assert target.read_text(encoding="utf-8") == original


async def test_ambiguous_loose_block_is_refused(ctx):
    target = ctx.settings.workspace / "m.py"
    original = "def a():\n    return 1\n\ndef b():\n        return 1\n"
    target.write_text(original, encoding="utf-8")

    result = await EditFileTool().invoke(
        {"path": "m.py", "old_text": "\treturn 1\n", "new_text": "\treturn 2\n"}, ctx
    )

    # Neither exact nor unique loosely (two lines strip to "return 1") — nothing changes.
    assert not result.ok
    assert "read_file" in result.content
    assert target.read_text(encoding="utf-8") == original


async def test_missing_text_explains_next_step(ctx):
    target = ctx.settings.workspace / "m.py"
    target.write_text(SRC, encoding="utf-8")

    result = await EditFileTool().invoke({"path": "m.py", "old_text": "nope()", "new_text": "x"}, ctx)

    assert not result.ok
    assert "read_file" in result.content
    assert target.read_text(encoding="utf-8") == SRC


async def test_multiple_occurrences_need_replace_all(ctx):
    target = ctx.settings.workspace / "m.py"
    target.write_text("x = 1\nx = 1\n", encoding="utf-8")

    refused = await EditFileTool().invoke({"path": "m.py", "old_text": "x = 1", "new_text": "x = 2"}, ctx)
    assert not refused.ok and "replace_all" in refused.content

    done = await EditFileTool().invoke(
        {"path": "m.py", "old_text": "x = 1", "new_text": "x = 2", "replace_all": True}, ctx
    )
    assert done.ok
    assert target.read_text(encoding="utf-8") == "x = 2\nx = 2\n"
