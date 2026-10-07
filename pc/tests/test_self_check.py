"""self_check (0.3.0 stage 4): the agent looks at itself and names what is wrong now — a recent
rollback is a problem, an old one is history."""

from __future__ import annotations

import json
import time

from core.tools.base import ToolContext
from core.tools.builtin.self_check_tools import SelfCheckTool


async def test_it_reports_the_body_and_only_recent_trouble(settings):
    folder = settings.app_dir / "guardian"
    folder.mkdir(parents=True)
    now = time.time()
    events = [{"ts": now - 3 * 86400, "kind": "update.rolled_back", "to": "old"},
              {"ts": now - 600, "kind": "change.rolled_back", "title": "deny root"},
              {"ts": now - 60, "kind": "update.confirmed", "release": "r2"}]
    (folder / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    logs = settings.logs_dir
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "agent.log").write_text("2026-10-07 [INFO] x: fine\n2026-10-07 [ERROR] agent: the provider failed\n",
                                    encoding="utf-8")
    out = await SelfCheckTool().invoke({}, ToolContext(settings=settings))
    assert out.ok
    text = out.content
    assert text.startswith("Altair ") and "Disk:" in text and "memory:" in text
    assert "72 h ago · update.rolled_back" in text and "0 h ago · update.confirmed" in text
    problems = text.split("Problems: ", 1)[1]
    assert "change.rolled_back 0 h ago" in problems and "update.rolled_back" not in problems   # old = history
    assert "the provider failed" in text


async def test_a_quiet_body_has_no_problems(settings):
    out = await SelfCheckTool().invoke({}, ToolContext(settings=settings))
    assert out.ok and out.content.rstrip().endswith("Problems: none found.") or "low disk" in out.content
