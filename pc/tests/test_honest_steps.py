"""Honesty of what the user sees around an action.

* The approval card names the concrete action ("Create the chart file x.html"), never the
  blanket "will change your system".
* A step waiting for approval is not captioned as already done, and a denied step says so.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.i18n import CATALOG, set_ui_language
from core.tools.base import Tool, args_summary
from core.tools.builtin import builtin_tools

STATIC = Path(__file__).resolve().parent.parent / "static"

#: Minimal valid arguments for tools whose Args have required fields.
SAMPLES = {
    "create_chart": {"kind": "line", "title": "Prime gaps", "series": [{"name": "g", "y": [1, 2]}]},
    "write_plan": {"title": "Refactor", "goal": "g", "approach": "a", "files": [], "steps": ["s"], "verification": "v"},
}


def _sample_args(tool: Tool):
    schema = tool.Args.model_json_schema()
    data = dict(SAMPLES.get(tool.name, {}))
    for field in schema.get("required", []):
        if field in data:
            continue
        prop = schema["properties"].get(field, {})
        kind = prop.get("type")
        if "enum" in prop:
            data[field] = prop["enum"][0]
        elif kind == "integer":
            data[field] = 1
        elif kind == "number":
            data[field] = 1.0
        elif kind == "array":
            data[field] = ["x"]
        elif kind == "boolean":
            data[field] = False
        else:
            data[field] = "example.txt"
    return tool.parse_args(data)


@pytest.mark.parametrize("lang", ["en", "ru"])
def test_every_action_is_described_concretely(lang):
    set_ui_language(lang)
    for tool in builtin_tools():
        if tool.category == "read":
            continue
        assert type(tool).approval_reason is not Tool.approval_reason, f"{tool.name} has no own approval text"
        try:
            args = _sample_args(tool)
        except Exception:  # noqa: BLE001 - exotic schemas: the override itself is what we check
            continue
        text = tool.approval_reason(args)
        assert text and "will change your system" not in text and "изменит систему" not in text, tool.name


def test_chart_approval_names_the_file():
    from core.tools.builtin.analytics_tools import CreateChartTool

    tool = CreateChartTool()
    text = tool.approval_reason(tool.parse_args({**SAMPLES["create_chart"], "path": "prime_gaps.html"}))
    assert text == "Create the chart file 'prime_gaps.html'"


def test_fallback_names_the_kind_of_effect():
    class Custom(Tool):
        name = "custom_tool"
        description = "x"
        category = "network"

        async def run(self, args, ctx):  # pragma: no cover
            return ""

    text = Custom().approval_reason(Custom().parse_args({}))
    assert text.startswith("'custom_tool' will go online")
    long = args_summary({"text": "y" * 500, "items": list(range(20)), "empty": ""})
    assert "items: [20]" in long and "empty" not in long and len(long) <= 240


def test_approval_texts_exist_in_both_languages():
    for key, entry in CATALOG.items():
        if key.startswith("appr."):
            assert entry.get("en") and entry.get("ru"), key


def _js_keys(lang: str) -> set[str]:
    text = (STATIC / "i18n.js").read_text(encoding="utf-8")
    marker = {"en": text.index('"tool.cmd"'), "ru": text.rindex('"tool.cmd"')}[lang]
    block = text[marker - 20000 if marker > 20000 else 0 : marker + 20000]
    return set(re.findall(r'"(tool\.[A-Za-z.]+)"\s*:', block))


def test_every_step_label_has_running_and_failed_forms():
    """stepLabel() asks for key+".run" while waiting/working and key+".fail" after a denial."""
    js = (STATIC / "redesign.js").read_text(encoding="utf-8")
    body = js[js.index("function stepLabel("): js.index("// ---------------------------------------------- инлайн-медиа")]
    used = set(re.findall(r't\("(tool\.[A-Za-z]+)"', body))
    assert "tool.wrote" in used
    i18n = (STATIC / "i18n.js").read_text(encoding="utf-8")
    for key in used:
        for suffix in (".run", ".fail"):
            assert i18n.count(json.dumps(key + suffix)) == 2, f"{key}{suffix} missing in EN or RU"
    assert '"tool.wrote.run": "Creating {f}"' in i18n and '"tool.wrote.fail": "Did not create {f}"' in i18n
    # live rows start in the running phase, finished rows switch by the real result
    assert 'stepLabel(name, args, "run")' in js and 'stepLabel(name, args, ok ? "ok" : "fail")' in js
