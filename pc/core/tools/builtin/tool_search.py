"""tool_search — loads deferred tools on demand (see core/tools/deferred.py)."""

from __future__ import annotations

import math
import re
from collections import Counter

from pydantic import BaseModel, Field

from core.tools.base import Tool, ToolContext, ToolResult
from core.tools.deferred import mark_loaded

_WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.I)


def _words(text: str) -> list[str]:
    # Tool names are snake_case: "browser_click" must match "browser" and "click".
    return [w.lower() for w in _WORD_RE.findall(text.replace("_", " "))]


def rank_tools(tools: list[Tool], query: str, limit: int) -> list[Tool]:
    """BM25-style ranking over name + description; a hit in the name weighs triple."""
    terms = _words(query)
    if not terms:
        return []
    docs = {t.name: _words(t.name) * 3 + _words(t.description) for t in tools}
    avg_len = sum(len(d) for d in docs.values()) / max(1, len(docs))
    df: Counter[str] = Counter()
    for doc in docs.values():
        df.update(set(doc))

    def score(tool: Tool) -> float:
        doc = docs[tool.name]
        counts = Counter(doc)
        total = 0.0
        for term in terms:
            tf = counts[term]
            if len(term) >= 4:  # a prefix hit: "screenshot" finds "screenshots"
                tf += sum(c for w, c in counts.items() if w != term and w.startswith(term))
            if not tf:
                continue
            idf = math.log(1 + (len(docs) - df[term] + 0.5) / (df[term] + 0.5))
            total += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * len(doc) / avg_len))
        return total

    scored = [(score(t), t.name, t) for t in tools]
    return [t for s, _, t in sorted(scored, key=lambda x: (-x[0], x[1])) if s > 0][:limit]


class ToolSearchArgs(BaseModel):
    query: str = Field(
        description="Keywords describing the capability you need (e.g. 'browser click', "
        "'git commit', 'sqlite query'), or 'select:name1,name2' to load tools by exact name"
    )
    max_results: int = Field(default=5, ge=1, le=15, description="How many tools to load")


class ToolSearchTool(Tool):
    name = "tool_search"
    description = (
        "Loads tools that are not active yet. The system prompt lists them by name under "
        "<deferred_tools>; search by keywords or load exact names with 'select:a,b'. Loaded "
        "tools become callable from your next step."
    )
    Args = ToolSearchArgs
    category = "read"
    timeout = 10.0

    async def run(self, args: ToolSearchArgs, ctx: ToolContext) -> ToolResult:
        registry = ctx.registry
        if registry is None:
            return ToolResult.fail("Tool search is unavailable outside an agent run.")
        candidates = [t for t in registry.all() if t.name != self.name]

        query = args.query.strip()
        if query.lower().startswith("select:"):
            wanted = [n.strip() for n in query[len("select:") :].split(",") if n.strip()]
            found = [registry.get(n) for n in wanted if registry.get(n) is not None]
            unknown = [n for n in wanted if registry.get(n) is None]
        else:
            found = rank_tools(candidates, query, args.max_results)
            unknown = []

        if not found:
            hint = f" Unknown: {', '.join(unknown)}." if unknown else ""
            return ToolResult.fail(
                f"No tools match '{query}'.{hint} Try other keywords, or pick a name from <deferred_tools>."
            )

        mark_loaded(ctx.scratch, [t.name for t in found])
        lines = [f"Loaded {len(found)} tool(s); they are callable from your next step:"]
        for tool in found:
            # schema(), not Args: MCP tools carry the server's JSON schema, their Args accept anything.
            schema = tool.schema().get("function", {}).get("parameters", {})
            params = ", ".join(schema.get("properties", {})) or "no parameters"
            lines.append(f"- {tool.name}({params}): {tool.description}")
        if unknown:
            lines.append(f"Not found: {', '.join(unknown)}.")
        return ToolResult(content="\n".join(lines))
