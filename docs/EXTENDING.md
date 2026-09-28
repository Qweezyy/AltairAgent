# Extending the agent

> This file is written for an AI assistant (or a person) adding new capabilities.
> **Follow the steps literally.** If your task is not covered here, first read
> [ARCHITECTURE.md](ARCHITECTURE.md), then work by analogy with the closest recipe.

---

## 0. The rules that matter most

1. **Do not rewrite the core.** `core/agent/runner.py`, `core/tools/base.py`,
   `core/agent/session.py`, `core/llm/base.py` are the foundation. Almost every new
   capability is added **beside** them, not inside them.
2. **After any change, run the tests:**
   ```bash
   python -m pytest -q
   ```
   All tests must be green. A red test is a bug you introduced, not an "outdated test".
   Do not delete or "simplify" tests to make them pass.
3. **Check that it starts:**
   ```bash
   python main.py --check
   ```
4. **New functionality = a new test.** Without a test the task is not done.
5. **Never write blocking code in `async def`.** `time.sleep`, `requests`,
   `subprocess.run`, reading large files — only via `await asyncio.to_thread(...)`
   or `await run_process(...)` from `core/utils/proc.py`.
6. **Never work with paths directly.** Only `resolve_path()` from
   `core/security/paths.py`.
7. **Do not add `os.getenv()`.** A new setting is a new field in `core/settings.py`
   and a line in `.env.example`.
8. **Do not put secrets in code.** Keys live only in `.env`.
9. **Do not swallow exceptions silently.** `except: pass` is forbidden. Either handle it,
   or let it raise, or return `ToolResult.fail("a clear explanation")`.
10. **Write tool descriptions for the model, not for a programmer.** The `description`
    must say *when* to call the tool.

---

## 1. Add a new tool (the most common task)

### Step 1. Create `core/tools/builtin/<topic>.py`

You can append to an existing file if the tool belongs there by meaning
(`files.py` — files, `web.py` — the internet, `search.py` — search, `shell.py` — the console).

```python
"""Tools for working with <topic>."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from core.errors import ToolError
from core.security.paths import resolve_path
from core.tools.base import Tool, ToolContext


class CountLinesArgs(BaseModel):
    path: str = Field(description="Path to the file")
    ignore_empty: bool = Field(default=False, description="Do not count blank lines")


class CountLinesTool(Tool):
    name = "count_lines"                    # latin letters, digits, _ ; unique
    description = (                          # for the model: WHAT and WHEN
        "Counts the number of lines in a text file. "
        "Use when you need the file size before reading it."
    )
    Args = CountLinesArgs
    dangerous = False                        # True if it changes the system
    timeout = 30.0                           # seconds

    async def run(self, args: CountLinesArgs, ctx: ToolContext) -> str:
        path = resolve_path(args.path, settings=ctx.settings, must_exist=True, must_be_file=True)

        def work() -> str:                   # blocking code -> into a thread
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            if args.ignore_empty:
                lines = [line for line in lines if line.strip()]
            if not lines:
                raise ToolError("The file is empty — nothing to count.")
            return f"Lines in {args.path}: {len(lines)}"

        return await asyncio.to_thread(work)
```

### Step 2. Register it

`core/tools/builtin/__init__.py`:

```python
from core.tools.builtin.my_module import CountLinesTool   # add the import

def builtin_tools() -> list[Tool]:
    return [
        ...
        CountLinesTool(),                                  # add to the list
    ]
```

**Nothing** else needs editing: the tool appears on its own in the system prompt, the
registry, `/api/tools`, and the UI.

### Step 3. Write a test in `tests/test_tools.py`

```python
async def test_count_lines(ctx):
    (ctx.settings.workspace / "a.txt").write_text("1\n2\n", encoding="utf-8")
    result = await CountLinesTool().invoke({"path": "a.txt"}, ctx)
    assert result.ok
    assert "2" in result.content


async def test_count_lines_outside_workspace_blocked(ctx):
    result = await CountLinesTool().invoke({"path": "../x.txt"}, ctx)
    assert not result.ok
```

### Step 4. Verify

```bash
python -m pytest -q
python main.py --check          # the tool must appear in the list
```

### Tool checklist

- [ ] `name` is unique, only `[A-Za-z0-9_-]`, up to 64 characters
- [ ] `description` tells the model **when** to call it
- [ ] every `Args` field has a `Field(description=...)`
- [ ] `dangerous = True` if it writes to disk / runs code / sends data outward
- [ ] paths go through `resolve_path()`
- [ ] blocking operations go through `asyncio.to_thread` / `run_process`
- [ ] expected errors use `raise ToolError("what is wrong and what to do")`
- [ ] added to `builtin_tools()`
- [ ] has a test: success + rejection

---

## 2. Add a setting

1. A field in the `Settings` class (`core/settings.py`) — always with a type and a default:
   ```python
   my_feature_limit: int = 10
   ```
2. A line with a comment in `.env.example`:
   ```
   MY_FEATURE_LIMIT=10
   ```
3. Usage: `ctx.settings.my_feature_limit` (in tools) or
   `self.settings.my_feature_limit` (in the core).

No `os.environ` in other files.

---

## 3. Add a skill

A skill is a Markdown instruction the agent reads on demand. Cheap on tokens: only the name
and description go into the prompt.

Create `skills/<name>/SKILL.md`:

```markdown
---
name: git_workflow
description: Git rules for this project. Read before commits and branches.
---

# Working with git

1. One branch per task: `feature/<short>`.
2. Before committing: `python -m pytest -q`.
```

That's all. No restart needed — the skill list is read on every task. The agent can create
skills itself with the `create_skill` tool, and users add ready-made ones in Settings → Skills
(`SKILL.md`, `.zip` or a folder, several at once; `SkillManager.import_path`). Files next to
`SKILL.md` (scripts, references) are listed to the model by `read_skill` with their folder.

---

## 4. Connect an MCP server

Settings → MCP servers (connects live, no restart), or `mcp_servers.json` in the app data folder.
Local servers use `command`/`args`/`env`; remote ones use `url` (+ `transport: "sse"` for the old
transport, `headers` or `token`). `MCPManager` keeps the tool registry in step with the servers.

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "..." },
      "disabled": false,
      "startupTimeout": 30
    }
  }
}
```

Restart the app and check `python main.py --check` — it lists the servers, the number of
tools, and connection errors. Tools appear under names `mcp__github__<tool>`.

Common reasons a server fails to start:
- Node.js is not installed / `npx` is not on PATH;
- wrong command or arguments;
- the server needs environment variables (see its README);
- the server is slow to start — raise `startupTimeout`.

---

## 5. Change the interface

The web UI is themed entirely through CSS variables. A few rules keep a new screen consistent:

* take colors **only** from the tokens in `static/style.css`, or the light theme will break;
* everything machine-generated (paths, commands, output) is set in a monospace font;
* check both themes and on a filled screen — an empty screen hides layout and density issues.

## 6. Add an event for the interface

1. An event model in `core/events.py`:
   ```python
   class ProgressUpdate(BaseEvent):
       type: Literal["progress"] = "progress"
       done: int
       total: int
   ```
   and add the class to the `Event = RunStarted | ... | LogEvent` union at the bottom of the file.
2. Emit from the core: `await self._emit(ProgressUpdate(done=1, total=10))`
   (in a tool — `await ctx.emitter(ProgressUpdate(...))`).
3. Handle in `static/app.js` — a new key in the `HANDLERS` object:
   ```javascript
   progress(msg) {
     logLine(`Progress: ${msg.done}/${msg.total}`);
   },
   ```

The frontend ignores unknown events, so step 3 can be done later.

---

## 7. Switch the model provider

**Option A — another OpenAI-compatible service.** No code needed, just `.env`:

```
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
DEFAULT_MODEL=qwen2.5-coder:14b
```

**Option B — a fundamentally different API.** Create `core/llm/<provider>_client.py` with a
class subclassing `LLMClient`, implement `complete()` (see `core/llm/base.py`), and return it
from `build_llm_client()`. The agent loop needs no changes.

Required: the provider must support tool calling, or the agent is useless.

---

## 8. Change agent behavior (prompt, limits)

- The rules text — `core/agent/prompt.py`, the `BEHAVIOUR_RULES` constant.
- The list of tools and skills is assembled automatically — **do not write them into the prompt by hand**.
- Limits (`MAX_STEPS`, `CONTEXT_TOKEN_BUDGET`, `MAX_PARALLEL_TOOLS`) — in `.env`.
- Per-project rules — an `AGENTS.md` file in the working folder; its contents are added to the
  system prompt automatically.

---

## 9. Common mistakes (do not repeat them)

| Mistake | Why it is bad | The right way |
|---|---|---|
| `subprocess.run(...)` inside `async def` | hangs the whole server: UI stops responding, "Stop" stops working | `await run_process(...)` |
| `open(path)` with a model-supplied path | escapes the sandbox, risks deleting other files | `resolve_path(path, settings=ctx.settings)` |
| `except Exception: pass` | the error vanishes, behavior becomes inexplicable | return `ToolResult.fail(...)` or let it raise |
| Catching errors inside `Tool.run()` | duplicates `invoke()`'s work | just `raise ToolError(...)` |
| Putting a megabyte of output in `content` | context overflow, token bill | limit the output, use pagination |
| Storing state in tool attributes | tools are shared across all sessions | use `ctx.scratch` (lives the whole session) |
| Adding a tool but forgetting `builtin_tools()` | the model will not see it | step 2 of recipe #1 |
| Changing the history message format | the provider returns 400 | edit only through `Session` methods |
| Swallowing `CancelledError` | the "Stop" button breaks | always `raise` it onward |

---

## 10. Final checklist before submitting

- [ ] `python -m pytest -q` — all green
- [ ] `python main.py --check` — no new errors
- [ ] The new code is covered by at least one test
- [ ] New settings are described in `.env.example`
- [ ] No new `os.getenv`, `except: pass`, or blocking calls in `async def`
- [ ] No secrets in code
- [ ] If you changed the WebSocket protocol — both `server/ws.py` and `static/app.js` are updated
- [ ] If you changed the architecture — `docs/ARCHITECTURE.md` is updated

## Research: where things live

Research logic is pulled out of the tools into `core/research/`, so it can be tested without
the agent and the model:

| File | Responsible for |
|---|---|
| `search.py` | search engines, structured results (Tavily → Brave → DuckDuckGo → Qwant) |
| `browser.py` | headless Chromium: one copy for the whole app, closed in lifespan |
| `documents.py` | PDF, XLSX, DOCX, CSV; if a package is missing — a clear request to install it |
| `sources.py` | numbering sources and deduplicating links |
| `pipeline.py` | the pipeline itself: plan → search → read → notes → report |

The tools in `core/tools/builtin/research.py` are thin wrappers: they parse arguments and
format the result.

Adding a document format? Put the extension into `FORMATS` and a `_read_*` function in
`documents.py` — everything else is picked up automatically, including `deep_research`.

Adding a pipeline step? It must report itself via `_progress()`: research takes minutes, and a
silent step looks like a hang.

## The "plus" menu in the composer

Everything the user enables before a run is gathered into a single `RunOptions` object
(`core/agent/run_options.py`). It also decides how each toggle affects the run:

* **attachments** — `core/attachments.py` turns a path into part of the message (photo, video,
  audio) or into a text block (document, folder, archive);
* **web-search mode** — `filter_registry()` physically removes the search tools from the
  registry. A ban only in the prompt can be worked around by the model; a missing tool schema
  cannot;
* **deep research and skills** — `notes()` returns instructions that go into the dialogue as
  system messages tagged `[run options]`. The tag is needed so `Session.clear_run_notes()`
  removes them before the next task: otherwise search, once turned off, would stay off forever.

Adding a new toggle:

1. a field in `RunOptions` and parsing in `from_message()`;
2. its effect — in `filter_registry()` or `notes()`;
3. a menu item in `static/index.html` (the `plus-menu` block) and handling in
   `static/app.js` (`runOptions()` and `renderFlags()`).

The agent core does not change in the process — `runner.run(task, options)` already takes the
whole object.

### About media attachments

There is no separate vision model: photos and video go to the **same** main model as the rest of
the dialogue (`server/ws.py`), so the context stays in one place. Not every model understands them —
the model list marks which ones do. If a text-only model receives media, the provider returns an
unsupported-type error; surface it to the user as such rather than letting it look like a broken agent.

## Formulas in answers

`static/math.js` hides formulas BEFORE the markdown is parsed and restores them after. The order
is mandatory: markdown treats `\[` as an escaped bracket and drops the slash, and turns an `_`
inside a formula into italics — so garbage would reach KaTeX.

Supported: `$…$`, `$$…$$`, `\(…\)`, and `\[…\]`. Nothing is touched inside code blocks and
`` `inline code` ``, and "$100 to $200" is not treated as a formula: a real formula's body does
not start or end with a space, and no digit follows the closing dollar.

The checks live in `tests/math_cases.js` and run from `tests/test_math_js.py` via node (if node
is not installed, the test is skipped). Rewriting the logic in Python for the sake of the test
is not allowed: what runs in the window is what must be tested.

## Analytics: charts and finance

The logic is in `core/analytics/`, the tools are thin wrappers:

| File | Responsible for |
|---|---|
| `charts.py` | assembling a Plotly figure (JSON) and a self-contained HTML with the embedded engine |
| `finance.py` | parsing a statement: detecting columns, categories, a summary |

**About charts.** The Python `plotly` package is NOT used — only the vendored
`static/vendor/plotly/plotly-basic.min.js`, which is embedded in every file. The figure
(`data` + `layout`) is assembled with ordinary dictionaries. A new chart type — add it to
`CHART_TYPES` and `_traces()`.

Two safety rules, both covered by tests:
* data goes inside a `<script>`, so `</` in the JSON is replaced with `<\/` — otherwise a
  "</script>" in a label would close the tag and break (or exploit) the page;
* the title in `<title>` is escaped.

**About chart tests.** Never write `assert X not in html` against the real engine: the minified
megabyte contains both "src=http" and "<script", the assert fails, and pytest hangs trying to
cram it into a traceback. Test the wrapper against a stub engine (the `stub_engine` fixture),
and the real engine only with positive checks.

**About spending categories.** The rules in `finance.py::CATEGORY_RULES` are an open list of
"category → keywords". Order matters: specific before general.

## Undoing edits (checkpoints)

Before every file change, the tools snapshot the previous state, and any edit can be undone with
a button in the artifacts panel.

| File | Responsible for |
|---|---|
| `core/checkpoints.py` | the snapshot store: take, restore, per-file undo stack |
| `core/tools/checkpointing.py` | the shared `snapshot_before_change` helper for tools |

How it is wired: `write_file`, `edit_file`, `apply_patch`, and `delete_path` call
`snapshot_before_change(ctx, path, op)` BEFORE the change. The snapshot is written to the data
folder (not the project), stores bytes (survives binary files), and stacks per file — undo
removes the last edit, then the previous one.

**Adding a new file-modifying tool?** Be sure to call `snapshot_before_change` before writing —
otherwise some edits cannot be undone, and the user will not understand why. A snapshot is taken
only if `ctx.checkpoints` is set (in tests and the CLI it is not — the tools work as before).

Undo from the interface: the "↩ Undo" button on an artifact card sends `restore_checkpoint`,
the server calls `CheckpointStore.restore_latest(path)` and returns a `checkpoint.restored`
event. Large files (>25 MB) are not snapshotted — undo is honestly unavailable for them
(`recoverable=false`), and the button is not shown.

## External content and injections

Anything coming from the internet or someone else's document must pass through
`guard_external(ctx, text, source)` (`core/tools/external_content.py`) before being handed to
the model. It wraps the text in a "this is data" frame, scans for injections
(`core/security/injection.py`), and on suspicion sends a warning to the log and sets a flag in
`ctx.scratch["injection_flags"]`.

On that flag, `Tool.invoke` raises the bar in "Auto" mode: a dangerous outward action
(`execute`/`network`) that would otherwise run by itself asks for confirmation. The flag is
reset at the start of each task.

**Adding a tool that reads external data?** Pass the result through `guard_external` — otherwise
an injection from it reaches the model without a frame or a warning. User files in the working
folder are your call; network and documents are always wrapped.

The pattern list in `injection.py::_PATTERNS` is deliberately narrow (few false positives) but
open to extension — one `(regex, reason)` pair at a time.

## Behavior evals

`evals/` holds golden tasks that check properties of the result. Each task (`EvalCase`) is a
prompt plus a check function that looks at the run's outcome (`CaseRun`: the answer, the tools
called, the confirmation requests, the steps, the tokens).

Two kinds:
* deterministic (`live=False`, a scripted `llm_factory`) — loop and safety invariants; run in
  `tests/test_evals.py`, without a model or a network;
* live (`live=True`) — a real model, a capability check; `python -m evals --live`.

**Adding an important capability or invariant?** Add an `EvalCase` to `evals/cases.py`. Check
PROPERTIES ("the answer contains 9", "tool X was called", "the command asked for confirmation"),
not exact text — the model is non-deterministic. For a self-contained task, use `workspace_files`
(to create files) and `settings_overrides` (e.g. a budget) instead of hitting the network.

## Memory and chat search

Long-term memory is two folders of notes laid out the same way (`core/memory.py`):
global — `<data dir>/memory/` (`MemoryStore`, about the user, all chats) — and project —
`<workspace>/.agent/memory/` (`core/folder_memory.py`, `FolderMemory`). Each holds `MEMORY.md`,
an index with one line per note (`- [Title](name.md) — gist`), and one markdown file per note: a
header (`name`, `title`, one-line `description`, `type`: user / feedback / project / reference,
`created`, `modified`) and the note itself. The store writes the index from the headers and
stamps `modified` on every write, so neither drifts. No embeddings — deliberately: a heavy vector
DB would bloat the local exe, and plain files stay readable and editable by the user.

Only the two indexes go into the system prompt (capped at 200 lines / 25 KB each; near the cap a
write tells the model to tighten the index). The model opens a note with `memory_read` when it
needs the details. The old `memory.json` and `.agent/memory.md` are moved over once on first use
(kept aside as `*.migrated`).

Chat search — `core/chat_search.py`: full-text search over the session JSON (the user/answer
feed), ranked by the number of matched words and recency.

The tools are in `core/tools/builtin/memory_tools.py`: `remember` (a new note), `memory_read`,
`memory_edit`, `memory_delete`, `recall` (search in the notes' text), `suggest_memory`,
`search_chats`. **A note can be edited or deleted only after `memory_read` of it in the same chat,
and only while it is unchanged since** (the user may edit the files by hand).
Transparency — `/api/memory` (GET/DELETE) and the "🧠 Memory" button.

**Rule:** memory is short facts, not a dumping ground. The agent is told in the prompt not to
save the momentary, secrets, or anything easily re-read from the code.

## Composer presets

`core/presets.py` (`PresetStore`) — scripted bundles of settings in `data_dir/presets.json`. A
preset carries: `model`, `approval_mode`, `web_mode`, `deep_research`, `skills`. On first launch
three built-ins are seeded (Coding/Study/Everyday); a broken file falls back to defaults without
crashing. Values are sanitized (`sanitized`) so a foreign or stale preset does not break the UI.

Endpoints `/api/presets` (GET/POST/DELETE). In the UI — the "Preset" chip in the composer:
applying one sets the model, the mode (sending `set_mode`), web search, and skills all at once;
"＋ Save current" builds a preset from the current state.
