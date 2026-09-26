"""Diff-движок: разбор, применение с допуском и инструмент apply_patch.

Особое внимание — «грязным» патчам, которые реально присылают модели:
неверные номера строк, сбитые отступы, ```-заборы, пустые строки контекста
без ведущего пробела. Строгий движок на них ломается, и агент зацикливается.
"""

from __future__ import annotations

import pytest

from core.errors import ToolError
from core.patch import FileAction, apply_hunks, parse_patch, parse_unified_diff
from core.tools.builtin.patch import ApplyPatchTool

BASE = """def greet(name):
    message = "hello"
    print(message, name)
    return message


def main():
    greet("world")
"""


def patch_text(body: str, path: str = "app.py") -> str:
    return f"--- a/{path}\n+++ b/{path}\n{body}"


# ------------------------------------------------------------------ разбор


def test_parse_simple_patch():
    patches = parse_unified_diff(
        patch_text('@@ -1,3 +1,3 @@\n def greet(name):\n-    message = "hello"\n+    message = "hi"\n     print(message, name)\n')
    )
    assert len(patches) == 1
    assert patches[0].path == "app.py"
    assert patches[0].action is FileAction.MODIFY
    assert patches[0].added == 1 and patches[0].removed == 1


def test_parse_multiple_files():
    text = (
        "--- a/one.py\n+++ b/one.py\n@@ -1 +1 @@\n-a\n+b\n"
        "--- a/two.py\n+++ b/two.py\n@@ -1 +1 @@\n-c\n+d\n"
    )
    patches = parse_unified_diff(text)
    assert [p.path for p in patches] == ["one.py", "two.py"]


def test_parse_strips_code_fences_and_chatter():
    text = "Вот патч:\n```diff\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-a\n+b\n```\nГотово!"
    patches = parse_unified_diff(text)
    assert patches[0].path == "app.py"
    assert patches[0].hunks[0].added == 1


def test_parse_git_style_headers():
    text = (
        "diff --git a/src/app.py b/src/app.py\n"
        "index 83db48f..bf269f4 100644\n"
        "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    assert parse_unified_diff(text)[0].path == "src/app.py"


def test_parse_new_and_deleted_file():
    created = parse_unified_diff("--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,2 @@\n+import os\n+print(os)\n")[0]
    assert created.action is FileAction.CREATE

    deleted = parse_unified_diff("--- a/old.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-import os\n-print(os)\n")[0]
    assert deleted.action is FileAction.DELETE


def test_parse_rejects_garbage():
    with pytest.raises(ToolError):
        parse_unified_diff("просто текст без диффа")
    with pytest.raises(ToolError):
        parse_unified_diff("")


def test_parse_hunk_without_file_header_is_explained():
    with pytest.raises(ToolError) as exc:
        parse_unified_diff("@@ -1 +1 @@\n-a\n+b\n")
    assert "--- a/path" in str(exc.value)


# -------------------------------------------------------------- применение


def test_apply_exact():
    patch = parse_unified_diff(
        patch_text('@@ -1,3 +1,3 @@\n def greet(name):\n-    message = "hello"\n+    message = "hi"\n     print(message, name)\n')
    )[0]
    new_text, results = apply_hunks(BASE, patch.hunks)

    assert all(r.applied for r in results)
    assert results[0].strategy == "exact"
    assert 'message = "hi"' in new_text
    assert "def main():" in new_text  # нетронутый код на месте


def test_apply_with_wrong_line_numbers():
    """Модель почти всегда врёт в номерах строк — это не должно ломать патч."""
    patch = parse_unified_diff(
        patch_text('@@ -95,3 +95,3 @@\n def greet(name):\n-    message = "hello"\n+    message = "hi"\n     print(message, name)\n')
    )[0]
    new_text, results = apply_hunks(BASE, patch.hunks)

    assert results[0].applied
    assert results[0].strategy in ("offset", "search")
    assert 'message = "hi"' in new_text


def test_apply_with_wrong_indentation():
    """Отступы в патче сбиты — совпадение ищется без учёта пробелов."""
    patch = parse_unified_diff(
        patch_text('@@ -1,3 +1,3 @@\n def greet(name):\n-message = "hello"\n+message = "hi"\n print(message, name)\n')
    )[0]
    new_text, results = apply_hunks(BASE, patch.hunks)

    assert results[0].applied
    assert results[0].strategy in ("whitespace", "search")
    # отступ восстановлен по файлу, а не взят из кривого патча
    assert '    message = "hi"' in new_text


def test_apply_multiple_hunks_shifts_correctly():
    body = (
        '@@ -1,2 +1,3 @@\n def greet(name):\n+    name = name.strip()\n     message = "hello"\n'
        '@@ -7,2 +8,2 @@\n def main():\n-    greet("world")\n+    greet("мир")\n'
    )
    patch = parse_unified_diff(patch_text(body))[0]
    new_text, results = apply_hunks(BASE, patch.hunks)

    assert all(r.applied for r in results), [r.error for r in results]
    assert "name = name.strip()" in new_text
    assert 'greet("мир")' in new_text


def test_missing_context_is_reported_with_real_file_content():
    patch = parse_unified_diff(
        patch_text('@@ -1,2 +1,2 @@\n def greet(name):\n-    message = "НЕТ ТАКОЙ СТРОКИ"\n+    message = "hi"\n')
    )[0]
    new_text, results = apply_hunks(BASE, patch.hunks)

    assert not results[0].applied
    assert "not found" in results[0].error
    assert "What is actually in the file" in results[0].error
    assert new_text == BASE  # текст не изменился


def test_crlf_file_stays_crlf():
    crlf = BASE.replace("\n", "\r\n")
    patch = parse_unified_diff(
        patch_text('@@ -1,3 +1,3 @@\n def greet(name):\n-    message = "hello"\n+    message = "hi"\n     print(message, name)\n')
    )[0]
    new_text, results = apply_hunks(crlf, patch.hunks)

    assert results[0].applied
    assert "\r\n" in new_text
    assert "\n\n" not in new_text.replace("\r\n", "")


def test_blank_context_line_without_leading_space():
    """Пустая строка контекста часто приходит без пробела — не должно ломаться."""
    body = '@@ -3,4 +3,5 @@\n     print(message, name)\n     return message\n\n\n+# конец\n def main():\n'
    patch = parse_unified_diff(patch_text(body))[0]
    new_text, results = apply_hunks(BASE, patch.hunks)

    assert results[0].applied, results[0].error
    assert "# конец" in new_text


# ------------------------------------------------------------- инструмент


async def test_tool_applies_patch_to_disk(ctx):
    target = ctx.settings.workspace / "app.py"
    target.write_text(BASE, encoding="utf-8")

    result = await ApplyPatchTool().invoke(
        {"patch": patch_text('@@ -1,3 +1,3 @@\n def greet(name):\n-    message = "hello"\n+    message = "hi"\n     print(message, name)\n')},
        ctx,
    )

    assert result.ok, result.content
    assert 'message = "hi"' in target.read_text(encoding="utf-8")
    assert "+1/-1" in result.content


async def test_tool_dry_run_does_not_write(ctx):
    target = ctx.settings.workspace / "app.py"
    target.write_text(BASE, encoding="utf-8")

    result = await ApplyPatchTool().invoke(
        {
            "patch": patch_text('@@ -1,3 +1,3 @@\n def greet(name):\n-    message = "hello"\n+    message = "hi"\n     print(message, name)\n'),
            "dry_run": True,
        },
        ctx,
    )

    assert result.ok
    assert "no files changed" in result.content
    assert target.read_text(encoding="utf-8") == BASE


async def test_tool_is_atomic_across_files(ctx):
    good = ctx.settings.workspace / "good.py"
    bad = ctx.settings.workspace / "bad.py"
    good.write_text(BASE, encoding="utf-8")
    bad.write_text(BASE, encoding="utf-8")

    combined = (
        patch_text('@@ -1,2 +1,2 @@\n def greet(name):\n-    message = "hello"\n+    message = "ok"\n', "good.py")
        + patch_text('@@ -1,2 +1,2 @@\n def greet(name):\n-    ТАКОЙ СТРОКИ НЕТ\n+    x = 1\n', "bad.py")
    )
    result = await ApplyPatchTool().invoke({"patch": combined}, ctx)

    assert not result.ok
    assert "NOT applied" in result.content
    # Ни один файл не тронут, хотя первый патч был корректным.
    assert good.read_text(encoding="utf-8") == BASE
    assert bad.read_text(encoding="utf-8") == BASE


async def test_tool_creates_new_file(ctx):
    result = await ApplyPatchTool().invoke(
        {"patch": "--- /dev/null\n+++ b/pkg/new_module.py\n@@ -0,0 +1,2 @@\n+import os\n+print(os.name)\n"},
        ctx,
    )

    assert result.ok, result.content
    created = ctx.settings.workspace / "pkg" / "new_module.py"
    assert created.read_text(encoding="utf-8") == "import os\nprint(os.name)\n"


async def test_tool_deletes_file(ctx):
    victim = ctx.settings.workspace / "old.py"
    victim.write_text("import os\n", encoding="utf-8")

    result = await ApplyPatchTool().invoke(
        {"patch": "--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-import os\n"}, ctx
    )

    assert result.ok, result.content
    assert not victim.exists()


async def test_tool_blocks_paths_outside_workspace(ctx):
    result = await ApplyPatchTool().invoke(
        {"patch": "--- a/../evil.py\n+++ b/../evil.py\n@@ -1 +1 @@\n-a\n+b\n"}, ctx
    )
    assert not result.ok
    assert "запрещ" in result.content.lower()


async def test_tool_failure_explains_how_to_fix(ctx):
    target = ctx.settings.workspace / "app.py"
    target.write_text(BASE, encoding="utf-8")

    result = await ApplyPatchTool().invoke(
        {"patch": patch_text('@@ -1,2 +1,2 @@\n-    НЕТ ТАКОЙ СТРОКИ\n+    x = 1\n')}, ctx
    )

    assert not result.ok
    assert "read_file" in result.content  # подсказка модели, что делать дальше
    assert target.read_text(encoding="utf-8") == BASE


async def test_tool_rejects_binary_file(ctx):
    binary = ctx.settings.workspace / "image.bin"
    binary.write_bytes(b"\x00\x01\x02binary")

    result = await ApplyPatchTool().invoke(
        {"patch": "--- a/image.bin\n+++ b/image.bin\n@@ -1 +1 @@\n-a\n+b\n"}, ctx
    )
    assert not result.ok
    assert "binary" in result.content


async def test_tool_git_rename_moves_and_edits_file(ctx):
    (ctx.settings.workspace / "old.py").write_text("a\n", encoding="utf-8")
    result = await ApplyPatchTool().invoke(
        {
            "patch": (
                "diff --git a/old.py b/new.py\nsimilarity index 90%\n"
                "rename from old.py\nrename to new.py\n@@ -1 +1 @@\n-a\n+b\n"
            )
        },
        ctx,
    )
    assert result.ok, result.content
    assert not (ctx.settings.workspace / "old.py").exists()
    assert (ctx.settings.workspace / "new.py").read_text(encoding="utf-8") == "b\n"


async def test_tool_rename_of_missing_file_fails_cleanly(ctx):
    result = await ApplyPatchTool().invoke(
        {"patch": "*** Begin Patch\n*** Update File: ghost.py\n*** Move to: real.py\n@@\n-a\n+b\n*** End Patch"},
        ctx,
    )
    assert not result.ok
    assert not (ctx.settings.workspace / "real.py").exists()


# ------------------------------------------ V4A (native apply_patch format of OpenAI models)


V4A_PATCH = """*** Begin Patch
*** Update File: app.py
@@ def greet(name):
-    message = "hello"
+    message = "hi"
     print(message, name)
*** Add File: pkg/util.py
+def helper():
+    return 1
*** End Patch"""


def test_parse_v4a_update_and_add():
    patches = parse_patch(V4A_PATCH)
    assert [(p.path, p.action) for p in patches] == [
        ("app.py", FileAction.MODIFY),
        ("pkg/util.py", FileAction.CREATE),
    ]
    assert patches[0].hunks[0].anchors == ["def greet(name):"]
    assert patches[1].hunks[0].added == 2


def test_parse_v4a_delete_and_stacked_anchors():
    text = (
        "*** Begin Patch\n*** Delete File: gone.py\n"
        "*** Update File: m.py\n@@ class A:\n@@     def run(self):\n-        x = 1\n+        x = 2\n"
        "*** End Patch"
    )
    gone, mod = parse_patch(text)
    assert gone.action is FileAction.DELETE
    assert len(mod.hunks) == 1
    assert mod.hunks[0].anchors == ["class A:", "def run(self):"]


def test_parse_v4a_update_without_changes_is_explained():
    with pytest.raises(ToolError) as exc:
        parse_patch("*** Begin Patch\n*** Update File: a.py\n*** End Patch")
    assert "No changes" in str(exc.value)


REPEATED = """class A:
    def run(self):
        x = 1
        return x


class B:
    def run(self):
        x = 1
        return x
"""


def test_anchor_picks_the_right_occurrence_of_repeated_context():
    """Identical bodies in two classes: only the anchor tells them apart."""
    text = (
        "*** Begin Patch\n*** Update File: m.py\n@@ class B:\n@@     def run(self):\n"
        "-        x = 1\n+        x = 2\n         return x\n*** End Patch"
    )
    new_text, results = apply_hunks(REPEATED, parse_patch(text)[0].hunks)
    assert results[0].applied, results[0].error
    a_part, b_part = new_text.split("class B:")
    assert "x = 1" in a_part  # class A untouched
    assert "x = 2" in b_part


def test_bare_unified_hunk_header_works_as_anchor():
    text = "--- a/m.py\n+++ b/m.py\n@@ class B:\n-        x = 1\n+        x = 3\n"
    new_text, results = apply_hunks(REPEATED, parse_patch(text)[0].hunks)
    assert results[0].applied
    assert new_text.split("class B:")[1].count("x = 3") == 1
    assert "x = 3" not in new_text.split("class B:")[0]


def test_unknown_anchor_falls_back_to_context_search():
    text = "*** Begin Patch\n*** Update File: app.py\n@@ def nonexistent():\n-    return message\n+    return name\n*** End Patch"
    new_text, results = apply_hunks(BASE, parse_patch(text)[0].hunks)
    assert results[0].applied
    assert "    return name" in new_text


async def test_tool_applies_v4a_patch_to_disk(ctx):
    target = ctx.settings.workspace / "app.py"
    target.write_text(BASE, encoding="utf-8")

    result = await ApplyPatchTool().invoke({"patch": V4A_PATCH}, ctx)

    assert result.ok, result.content
    assert 'message = "hi"' in target.read_text(encoding="utf-8")
    assert (ctx.settings.workspace / "pkg" / "util.py").read_text(encoding="utf-8") == (
        "def helper():\n    return 1\n"
    )


async def test_tool_v4a_move_file(ctx):
    (ctx.settings.workspace / "a.py").write_text(BASE, encoding="utf-8")
    patch = (
        "*** Begin Patch\n*** Update File: a.py\n*** Move to: lib/b.py\n"
        '@@\n def main():\n-    greet("world")\n+    greet("you")\n*** End Patch'
    )
    result = await ApplyPatchTool().invoke({"patch": patch}, ctx)

    assert result.ok, result.content
    assert "moved" in result.content
    assert not (ctx.settings.workspace / "a.py").exists()
    assert 'greet("you")' in (ctx.settings.workspace / "lib" / "b.py").read_text(encoding="utf-8")


async def test_tool_v4a_is_atomic(ctx):
    target = ctx.settings.workspace / "app.py"
    target.write_text(BASE, encoding="utf-8")
    patch = (
        "*** Begin Patch\n*** Add File: fresh.py\n+x = 1\n"
        "*** Update File: app.py\n@@\n-    NO SUCH LINE\n+    y = 2\n*** End Patch"
    )
    result = await ApplyPatchTool().invoke({"patch": patch}, ctx)

    assert not result.ok
    assert not (ctx.settings.workspace / "fresh.py").exists()
    assert target.read_text(encoding="utf-8") == BASE
    assert "*** Begin Patch" in result.content  # the fix-it hint shows both formats


# ------------------------------- «@@» без номеров строк (частый случай у моделей)


BARE_PATCH = """--- a/app.py
+++ b/app.py
@@
 def greet(name):
     message = "hello"
+    name = name.strip()
     print(message, name)
"""


def test_parse_bare_hunk_header():
    patch = parse_unified_diff(BARE_PATCH)[0]
    assert len(patch.hunks) == 1
    assert patch.hunks[0].old_start == 0  # позиция неизвестна — ищем по контексту


def test_apply_bare_hunk():
    new_text, results = apply_hunks(BASE, parse_unified_diff(BARE_PATCH)[0].hunks)
    assert results[0].applied, results[0].error
    assert "    name = name.strip()" in new_text


def test_apply_several_bare_hunks_in_order():
    text = (
        "--- a/app.py\n+++ b/app.py\n"
        '@@\n def greet(name):\n-    message = "hello"\n+    message = "hi"\n'
        '@@\n def main():\n-    greet("world")\n+    greet("мир")\n'
    )
    new_text, results = apply_hunks(BASE, parse_unified_diff(text)[0].hunks)
    assert all(r.applied for r in results), [r.error for r in results]
    assert 'message = "hi"' in new_text and 'greet("мир")' in new_text


async def test_tool_accepts_bare_hunk_patch(ctx):
    """Ровно тот патч, на котором раньше падал живой агент."""
    target = ctx.settings.workspace / "app.py"
    target.write_text(BASE, encoding="utf-8")

    result = await ApplyPatchTool().invoke({"patch": BARE_PATCH}, ctx)

    assert result.ok, result.content
    assert "name = name.strip()" in target.read_text(encoding="utf-8")
