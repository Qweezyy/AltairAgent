"""A realistic agent request built from Altair's own pieces (no user data)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

PC = Path(__file__).resolve().parents[3] / "pc"
sys.path.insert(0, str(PC))

from core.tools import build_default_registry  # noqa: E402
from core.tools.deferred import active_tool_names  # noqa: E402

REG = build_default_registry()
CORE = active_tool_names(REG, True, [], {})


def system_prompt() -> str:
    """The real system prompt of a stored chat if there is one, else a built one."""
    from core.agent.prompt import build_system_prompt
    from core.llm.base import CACHE_BREAKPOINT
    from core.settings import get_settings

    text = build_system_prompt(settings=get_settings(), registry=REG)
    return text.replace(CACHE_BREAKPOINT, "\n\n")


def tools(names=None) -> list[dict]:
    return REG.schemas(names or CORE)


FILES = ["core/agent/session.py", "core/updater.py", "core/search_backend.py", "core/browser_net.py",
         "server/chats.py", "core/reminders.py", "core/memory.py", "cli/app.py"]


def history(n_files: int = 6, max_chars: int = 12000) -> list[dict]:
    """User asks to review code; the agent reads files (tool calls + outputs)."""
    msgs = [{"role": "user", "content": "Review the reliability of these modules and tell me the 3 riskiest spots."}]
    for i, rel in enumerate(FILES[:n_files]):
        cid = f"call_{i}"
        msgs.append({"role": "assistant", "content": f"Reading {rel}.", "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"path": rel})}}]})
        body = (PC / rel).read_text(encoding="utf-8")[:max_chars]
        msgs.append({"role": "tool", "tool_call_id": cid, "name": "read_file", "content": body})
    return msgs
