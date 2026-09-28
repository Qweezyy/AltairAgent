# Architecture

This document explains **how the system is built and why**. Read it before any serious
change to the core. Step-by-step recipes live in [EXTENDING.md](EXTENDING.md).

## 1. Layers and dependencies

```
        UI (static/, console)
               |  events / commands
  server/ws.py (a window's socket)  server/app.py
               |
        server/chats.py              <-- chats, apart from the windows showing them
               |
        core/agent/runner.py         <-- agent loop
         /            |         \
core/llm      core/tools      core/agent/session.py
                  |    \
          core/tools/builtin   core/mcp
                  |
       core/security, core/utils, core/settings
```

Dependency rule: **arrows point downward only**.

- `core/*` knows nothing about FastAPI, WebSocket, or HTML.
- `server/*` holds no agent logic — transport only.
- Tools know nothing about the model or the loop; they receive a `ToolContext`.

If you feel the urge to import `server` from `core`, that is a sign the design is wrong.

## 2. The flow of a single task

1. The browser sends to `/ws`: `{"type": "run", "task": "..."}`.
2. `server/ws.py::Connection` is a window's socket: it shows one chat at a time. The chat itself
   (`server/chats.py::ChatState`, kept in the app's `ChatHub`) creates an `AgentRunner` with its
   `Session` and runs it in a separate `asyncio.Task` (so "Stop" works while the socket keeps
   accepting commands). The run, its approvals and questions belong to the chat, not the socket:
   the run goes on when the window shows another chat, reloads or closes, and a chat nobody has
   open can be woken by a reminder (`server/reminders.py` → `ChatHub.dispatch`).
3. `AgentRunner.run()`:
   1. assembles the system prompt (`core/agent/prompt.py`) — the list of tools and skills is
      built from the registry, not hardcoded;
   2. appends the user message to the `Session`;
   3. loops up to `MAX_STEPS`:
      - trims history to fit the context budget;
      - calls `LLMClient.complete(...)` with the tool schemas;
      - text tokens stream to the UI as `text.delta`, reasoning as `reasoning.delta`;
      - if the model asked for tools, it runs them (in parallel, under a semaphore),
        puts the results into history, and repeats the step;
      - if there are no tools, this is the final answer and the loop ends;
   4. if the step limit is exhausted, one more request is made **without tools** so the user
      still gets a meaningful answer.
4. Events (`core/events.py`) go into the connection's queue; a single writer task sends them
   to the socket in order.

## 3. Invariants you must not break

| Invariant | Why |
|---|---|
| Every `role="tool"` has a preceding `assistant` with the same `tool_call_id` | Otherwise the provider returns 400 and the agent "breaks" for no visible reason |
| Trimming removes the assistant+tool pair as a whole (`Session.trim`) | See above |
| The system prompt is exactly one, and first | Otherwise the model loses the rules context |
| `Tool.run()` does not catch its own errors | `Tool.invoke()` catches them and turns them into text for the model |
| `ToolContext` is created once per session | Otherwise `ctx.scratch` would reset every step |
| All disk access goes through `resolve_path()` | The only sandbox guard |
| Blocking code runs only via `asyncio.to_thread` / `run_process` | Otherwise the whole server hangs |
| Events are sent only through the connection's queue | Parallel `send` on one socket causes races |
| App data lives in `app_dir`, not `workspace` | Otherwise switching folders loses chats and skills, and litters someone else's project |
| The project folder passes through `validate_workspace()` | A typo in a path must not create junk directories |

## 4. Tools

A `Tool` (see `core/tools/base.py`) is a class with:

- `name`, `description` — these go into the schema for the model;
- `Args` — a pydantic model of arguments, from which the JSON schema is generated (without
  `$ref`, because small models handle those poorly);
- `dangerous` — whether confirmation is required;
- `timeout`, `max_output_chars` — protection against hangs and context overflow.

`Tool.invoke()` is the "pipeline" around `run()`:

```
raw args (str|dict) -> parse/validate -> approval -> timeout -> run() -> ToolResult -> truncate
                            |               |          |          |
                      ToolInputError  PermissionDenied  ToolTimeout  any exception
                            \_______________\__________\__________/
                                            v
                              ToolResult(ok=False, content="ERROR: ...")
```

The key idea: **a tool error is data for the model, not a program crash**. The model sees the
error text and can correct itself.

## 5. The registry

`ToolRegistry` validates names (`^[A-Za-z0-9_-]{1,64}$`) and forbids duplicates. The registry
is built once in `lifespan` and reused across connections. Tools are stateless, so a shared
registry is safe.

MCP tools go into the same registry under the name `mcp__<server>__<tool>`, so to the agent
loop they are indistinguishable from built-ins.

**Deferred loading** (`core/tools/deferred.py`). Sending every schema costs ~22k tokens per
request and a long tool list hurts selection. Only `CORE_TOOLS` go out up front; the others are
listed by name in `<deferred_tools>` and activated by `tool_search`. The active set is derived
from the history (core + tools already called + tools loaded this run), so it needs no extra
state and only grows — adding a tool changes the tools block once, not on every step.

## 5a. Token economy

Nothing of a conversation is ever deleted to save tokens. `Session.messages` and the UI feed
(`Session.timeline`) keep everything, across restarts, updates and model switches; the model
gets a *view* of it (`Session.view()`, sent via `snapshot()`), shaped by marks stored next to
the original messages (`_view`, `_hidden`, `_drop_calls`) and stripped before sending. The
agent can still reach the folded part with `search_chats`.

The view is kept small in layers, from cheapest to most expensive:

1. **Truncation at the source** — tools page and filter their output (`read_file` ranges,
   `grep_search` output modes, `tool_output_limit` head+tail cut), and the browser returns
   only what changed on a page after an action.
2. **Superseding page states** (`Session.supersede_page_states`) — browser snapshots and tool
   screenshots made stale by newer ones are masked.
3. **Clearing old tool outputs** (`Session.clear_old_tool_results`, `tool_result_clearing`) —
   past half the context budget (at most 100K tokens), old large outputs are masked with a
   one-line note while the calls stay. Done in one batch that frees a meaningful amount,
   because every change to earlier messages invalidates the provider's prompt cache from
   that point.
4. **Summarization** (`context_compaction`) — only when the view still exceeds the budget:
   the oldest messages are folded behind a summary note.

## 6. The LLM layer

`LLMClient` is a narrow interface: a single `complete()` method. This lets us:

- swap the model in tests (`tests/fakes.py::ScriptedLLM`) without a network;
- plug in another provider without touching the agent loop.

`OpenAICompatClient` additionally handles:

- assembling `tool_calls` from stream chunks (providers send them piecemeal);
- retrying with exponential backoff — **only if no token has reached the UI yet**, otherwise
  the user would see duplicated text;
- translating provider errors into clear messages (401 — key, 404 — model, 400 — usually the
  lack of tool-calling support).

## 7. Security

Three independent lines of defense:

1. **Path sandbox** (`core/security/paths.py`) — strict, always on.
2. **Command blacklist** (`core/tools/builtin/shell.py`) — disk formatting, `rm -rf`,
   `shutdown`, and the like are blocked even with confirmation.
3. **Permission modes** (`core/security/permissions.py`). Each tool has a category:
   `read`, `edit`, `execute`, `network`. The mode decides what to do with it —
   run it, ask, or forbid:

   | Mode | read | edit | execute / network |
   |---|---|---|---|
   | `manual` | run | ask | ask |
   | `accept_edits` | run | run | ask |
   | `plan` | run | forbid | forbid |
   | `allowlist` | run | forbid | forbid |
   | `bypass` | run | run | run |

   A tool with `dangerous = True` but no category is automatically treated as
   `edit`: a forgotten category must not silently disable confirmation.

   It is not the core that asks, but the current `Approver`: on the web it is a card
   with four options (`server/chats.py`), in the console a `[y/N]` prompt
   (`core/security/console.py`). "Always allow" answers are stored in
   `PermissionStore` and drop the question ahead of time.

Python code runs in an **external process**, not via `exec()` — a crash or an infinite loop in
generated code does not kill the server.

## 8. Events

`core/events.py` holds typed pydantic models with a `type` field. The core hands them to the
`Emitter`; the UI is just one of the consumers. The frontend ignores unknown types, so the
backend can be updated ahead of the frontend.

## 9. The diff engine (editing code)

`core/patch/` parses and applies patches in two formats: unified diff and V4A (`*** Begin Patch`,
the grammar OpenAI models are trained on). Models edit most reliably in the format they were
trained on — Claude on exact string replacement, GPT on V4A — so the agent offers both paths:
`edit_file` (exact replacement, the default for a localized change) and `apply_patch`
(multi-hunk / multi-file), because:

* only the changed lines go into context, not the whole file;
* untouched code physically cannot be overwritten — only hunks are applied;
* one call edits several files, and application is atomic.

Hunks without line numbers are located by scope anchors (`@@ class B:`, possibly stacked), then
by context, so a repeated block edits the right occurrence.

Structure: `parser.py` (text → structures) and `applier.py` (structures → new text). The split
matters: the parser tolerates messy input, the applier is responsible for correctness.

**The parser is lenient on purpose.** Models send diffs with ```-fences, wrong counters, an
`@@` header without line numbers, and blank context lines missing a leading space. A strict
parser here means an agent that cannot fix its own typo and gets stuck in a loop.

**The applier locates a hunk by context, not by line number** — in four passes:
`exact` → `offset` (nearby) → `whitespace` (ignoring indentation) → `search` (a unique match in
the file). The strategy is reported, and if a hunk landed imprecisely the agent gets a warning
and must re-read the file.

Two details, without which the engine would corrupt code:

* context lines are taken **from the file**, not from the patch — even with broken indentation
  in the patch, untouched lines stay byte-for-byte the same;
* added lines are moved into the file's indentation system (`_shift_indent`).

Line-ending style (CRLF/LF) and the presence of a trailing newline are preserved.

## 10. Code map and checks (`core/codemap`, `core/quality`)

**`core/codemap`** answers "what is even in this project" without reading files in full. For
Python it uses the built-in `ast` — precise parsing with no dependencies. For other languages,
symbols are extracted by patterns (`generic_outline.py`): not a full AST, but enough for
navigation — the agent gets the name, kind, line, and signature, then reads the fragment it
needs.

Tree-sitter is deliberately not wired in: it drags binary grammars for every language, and easy
installation matters more for a local agent than perfect parsing. The extension point is
`outline_file()`: a new backend is added there, and the tools need no changes.

Tools: `code_map` (structure of a file or project), `find_symbol` (where a symbol is defined
and who calls it).

**`core/quality`** serves the `edit -> check -> fix` loop:

* `detect.py` figures out how a project runs tests and linters
  (pytest/unittest, npm test, go test, cargo test; ruff, eslint, mypy, tsc, go vet);
* `report.py` compresses the output to the essentials: which checks failed, with which
  messages, plus the traceback of the last failure.

Compression is not decoration: a full pytest log on a large project is tens of thousands of
characters, of which the model needs twenty lines. A successful run of 125 tests fits in
~150 characters.

Tools: `run_tests`, `run_lint`. System-prompt rule #8 requires running them after every code
change and fixing until green.

## 11. Working folder (workspace) and app folder (app_dir)

These are different things, and must not be confused:

| | `app_dir` | `workspace` |
|---|---|---|
| What it is | the folder where the agent is installed | the user's project folder |
| Changes | never | in every chat |
| Contents | `logs/`, `storage/sessions/`, `skills/`, `mcp_servers.json` | the files the agent works on |
| Set by | `APP_PATH` (defaults to the code folder) | the "Browse" button in the UI, the `workspace` field in a chat |

How it works:

1. The user picks a folder — with the built-in browser (`/api/browse`) or the system dialog
   (`/api/dialog/select-folder`).
2. `Connection._apply_workspace()` validates it via `validate_workspace()` and calls
   `settings.for_workspace(...)` — a copy of the settings with a different working folder.
3. That copy goes into `AgentRunner`, from there into `ToolContext` → the `resolve_path()`
   sandbox starts letting tools into the new folder only.
4. The folder is written to `Session.workspace` and saved with the chat, so returning to an old
   chat restores its directory.

Skills are not lost in the process: `skills_dirs` = global (`app_dir/skills`) + project
(`workspace/.agent/skills`), where a project skill of the same name overrides a global one.

## 11a. Built-in browser and downloads

**Host.** In the desktop shell (`desktop/src-tauri/src/browser.rs`) every browser tab is a native
WebView2 controller in its own child window of the main window — not a Tauri webview, so pages
never see Tauri globals or IPC, and nothing appears on the desktop or taskbar. Tabs share one
environment with a separate profile (`%LOCALAPPDATA%\LocalAIAgent\browser`), SmartScreen on,
`AutomationControlled` off, and a loopback-only remote-debugging port. The web UI
(`static/browser.js`) owns tab layout, the address bar and the tab strip, and relays shell events
to the backend (`server/browser_ws.py`).

**Agent.** `core/browser_session.AgentBrowser` connects to that port with Playwright
`connect_over_cdp`, so the agent and the user act on the very same tabs. Without the shell it
launches the installed Chrome/Edge off-screen as a normal process and streams a CDP screencast to
the panel. Pages are given to the model as `aria_snapshot(mode="ai")` with `[ref=eN]` handles;
agent actions are serialized by one lock, page dialogs during agent actions are answered
automatically. `file://` is allowed only inside the workspace sandbox (the agent tests the sites
it builds), and a click that leads to a local file outside it is refused. Tabs remember who
opened them: the agent closes its own tabs freely, closing the user's asks first.

**Network** (`core/browser_net.py`). The browser goes through a local proxy (a PAC script with a
DIRECT fallback, so pages still load if it is down). With a VPN on, each site is tried directly —
DNS from the physical network and a socket bound to the physical adapter, which also bypasses TUN
VPNs — and replayed through the VPN when it stalls or drops the TLS handshake; per-site rules and
learned blocks live in `browser/network.json`, set from the panel chip or the `browser_network` tool.

**Downloads** (`core/browser_downloads.py`). Every download is saved to a quarantine folder,
never straight into the user's folders. It is then checked: Microsoft Defender scan
(`MpCmdRun`), magic-byte sniffing for disguised executables, and a list of risky types. Threats
are deleted at once; risky/suspicious/unscanned files need the user's confirmation to move out.
A moved file gets the Mark-of-the-Web, so Windows keeps treating it as downloaded. Entries older
than 7 days are purged.

**Server texts.** Everything the backend shows to the user (approval reasons, status messages,
dialog titles) goes through `core/i18n.tr()`; the UI reports its language with
`{"type": "ui_lang"}` on connect and on every switch. Model-facing text stays English.

## 12. Building and updates

`build_app.py` builds the app with PyInstaller into a single folder (not a single file: this
starts faster, and an update comes down to replacing only the changed files).

An important split for the built version:

| What | Where |
|---|---|
| Code and resources | next to the exe, read-only |
| Settings, chats, skills, logs | `%LOCALAPPDATA%\LocalAIAgent` |
| Default working folder | the user's Documents |

Data cannot sit next to the exe: Program Files is read-only, and everything inside the package
is wiped on update. On first launch, `.env` and skills are copied into the data folder.

`core/updater.py` reads a JSON manifest (by URL or from a shared folder), compares the version
with `core/version.py`, downloads a zip, verifies the sha256, and unpacks it alongside. The swap
is done by a temporary bat script: a running program cannot overwrite its own exe. Archives with
paths like `../..` are rejected.

## 13. What is deliberately NOT done

To keep the foundation simple and predictable:

- no multi-user mode and no authentication (the server listens on `127.0.0.1`);
- no persistent history storage (a session lives in the connection's memory);
- no automatic restart of crashed MCP servers (Settings → MCP servers has a manual reconnect;
  diagnostics also in `--check`).

On context overflow the runner collapses the oldest messages into a short summary (one cheap
model call, guarded by the `context_compaction` setting) and falls back to plain trimming if
summarization fails — see `runner.py`.

All of this can be added on top without changing the core.
