# PC agent tools

Catalog of every built-in tool the PC agent exposes to the model. Generated from
the code (`core/tools/builtin/*`), not from memory. Keep it in sync — see the
update rule at the bottom.

- **Total: 116 tools** · last updated: 2026-09-23
- **Category** — how approval is gated: `read` (auto), `edit` / `execute` /
  `network` (may ask in manual mode). `⚠` marks a tool flagged `dangerous`
  (state-changing, asks by default).
- Source column is the module under `core/tools/builtin/` (or `core/tools/…`).
- **Deferred loading** (`TOOL_SEARCH=true`, default): only the core set in
  `core/tools/deferred.py::CORE_TOOLS` (19 tools) is sent with every request;
  the rest are listed by name in the system prompt and loaded with
  `tool_search`. A new tool is deferred unless added to `CORE_TOOLS` — add it
  there only if nearly every task needs it.

## Tool discovery

| Tool | Cat | Description | Source |
|------|-----|-------------|--------|
| `tool_search` | read | Load deferred tools by keywords (BM25-style ranking) or exact names (`select:a,b`). | tool_search |

## Files & search

| Tool | Cat | Description | Source |
|------|-----|-------------|--------|
| `read_file` | read | Read a text file with line numbers; large files come in chunks; `.ipynb` shown as cells. | files |
| `write_file` | edit ⚠ | Create or fully overwrite a file (prefer `edit_file`/`apply_patch` for edits). | files |
| `edit_file` | edit ⚠ | Replace one text fragment in a file — the default for a localized change; falls back to a unique indentation-insensitive match. | files |
| `apply_patch` | edit ⚠ | Apply a multi-hunk / multi-file patch atomically — unified diff or V4A (`*** Begin Patch`), with scope anchors and file moves. | patch |
| `delete_path` | edit ⚠ | Delete a file or directory. | files |
| `list_directory` | read | List files/folders with sizes. | files |
| `find_files` | read | Recursively find files by glob (`*.py`, `src/**/*.ts`); skips `.git`/`node_modules`/venv. | search |
| `grep_search` | read | Text/regex search over file contents with context and path masks. | search |

## Code intelligence

| Tool | Cat | Description | Source |
|------|-----|-------------|--------|
| `code_map` | read | Structure of code: classes/functions/methods with line numbers and signatures. | codemap_tools |
| `find_symbol` | read | Where a symbol is defined and where it is used. | codemap_tools |
| `ast_search` | read | Structural search over the Python AST (decorators, subclasses, call sites). | codemap_tools |
| `code_intel` | read | Semantic navigation via a language server: definition / references / hover. | lsp_nav_tools |
| `type_check` | read | Static type/semantic check of Python with pyright. | lsp_tools |

## Shell, execution & background

| Tool | Cat | Description | Source |
|------|-----|-------------|--------|
| `execute_command` | execute ⚠ | Run a non-interactive shell command; returns exit code, stdout, stderr. | shell |
| `run_python` | execute ⚠ | Run Python in a separate process; no state kept between calls. | python_exec |
| `run_background` | execute ⚠ | Start a command in the background and return immediately (builds, watchers). | background_tools |
| `read_background` | read | New output since last read + status of a background command. | background_tools |
| `stop_background` | execute ⚠ | Stop a background command by name (whole process tree). | background_tools |
| `wait_for` | read | Pause: wait N seconds or until a background task finishes, then resume. | background_tools |
| `watch_background` | read | Non-blocking reminder: notify when time passes or a background task ends. | background_tools |

## Dev servers

| Tool | Cat | Description | Source |
|------|-----|-------------|--------|
| `start_dev_server` | execute ⚠ | Start a long-running dev server (npm run dev, uvicorn --reload, …) in the background. | devserver_tools |
| `read_dev_server` | read | New dev-server log lines since last read; flags build/runtime errors. | devserver_tools |
| `list_dev_servers` | read | List running dev servers: name, command, status, uptime. | devserver_tools |
| `stop_dev_server` | execute ⚠ | Stop a dev server by name. | devserver_tools |

## Git

| Tool | Cat | Description | Source |
|------|-----|-------------|--------|
| `git_status` | read | Repo status: branch and changed/staged/untracked files. | git_tools |
| `git_diff` | read | Unified diff of working tree / index / against a revision. | git_tools |
| `git_log` | read | Commit history (hash, date, author, message), optionally per file. | git_tools |
| `git_blame` | read | Which commit and author last changed each line in a range. | git_tools |
| `git_commit` | execute ⚠ | Stage and commit changes (push is left to the user). | git_tools |
| `git_branch` | execute ⚠ | list / create / switch branches. | git_tools |
| `git_restore` | execute ⚠ | Restore files from git (discard uncommitted changes or a revision). | git_tools |

## Testing & quality

| Tool | Cat | Description | Source |
|------|-----|-------------|--------|
| `run_tests` | execute ⚠ | Run the project test suite (pytest/npm/go/cargo, autodetected); compact report. | quality_tools |
| `run_lint` | execute ⚠ | Run linters/type-checkers (ruff, eslint, mypy, tsc, go vet). | quality_tools |
| `test_coverage` | execute ⚠ | Run tests with coverage and show uncovered lines (Python). | coverage_tools |
| `security_scan` | read | Bandit scan of Python for vulnerabilities (injections, unsafe deserialization, …). | security_tools |
| `scan_secrets` | read | Scan code for leaked keys/tokens/passwords (masked output). | security_tools |
| `differential_check` | execute ⚠ | Run two commands and compare outputs — cross-check for correctness. | verify_tools |
| `review_changes` | read | Critic pass over your git diff (bugs, edge cases, leaks, missing tests). | verify_tools |

## Web & research

| Tool | Cat | Description | Source |
|------|-----|-------------|--------|
| `web_search` | network | Web search; returns titles, links, snippets; time filter. | web |
| `fetch_url` | network | Fetch a page and return cleaned text (chunked). | web |
| `http_request` | network ⚠ | Arbitrary HTTP request to an API (GET/POST/PUT/PATCH/DELETE). | web |
| `download_file` | network ⚠ | Download a file by direct link into the workspace. | web |
| `browse_page` | network | Open a page in a real browser and return text after scripts run (SPA-friendly). | research |
| `deep_research` | network | Multi-source research with sub-queries; returns a report with citations. | research |
| `read_document` | read | Read PDF / xlsx / docx / CSV from the workspace or a URL. | research |
| `find_images` | network | Find relevant images online (Wikimedia / SearXNG), filtered; returns URLs + captions. | image_search_tools |

## Agent browser (built-in, shared with the user)

The panel hosts real WebView2 (Edge/Chromium) tabs inside the app window — no separate window or
taskbar icon, its own profile, not flagged as automation. The agent drives the same tabs the user
sees over CDP (Playwright `connect_over_cdp`). Without the desktop shell a real Chrome/Edge runs
off-screen and the panel shows its live screencast. Pages are described as an accessibility
snapshot with `[ref=eN]` handles; agent actions are serialized. Local pages open only inside the
workspace (for testing the sites/apps the agent builds); links out of it are refused.

| Tool | Cat | Description | Source |
|------|-----|-------------|--------|
| `browser_navigate` | network | Open a URL (or search the words), or a local page inside the workspace (`dist/index.html`, file://), in the active tab; returns the page snapshot with refs. | browser_tools |
| `browser_read` | read | Re-read the active tab: fresh snapshot with `[ref=…]` handles. | browser_tools |
| `browser_find` | read | Find snapshot lines (with refs) containing all the words — cheaper than re-reading a long page. | browser_tools |
| `browser_click` | network ⚠ | Click by ref (double / right button; `dialog=accept` for confirm pages). Reports navigation, new tabs, downloads. | browser_tools |
| `browser_type` | network ⚠ | Type into a field by ref; `replace`, `submit=true` presses Enter. | browser_tools |
| `browser_press` | network | Press a key or combination, optionally on an element. | browser_tools |
| `browser_select` | network | Choose option(s) in a drop-down by ref. | browser_tools |
| `browser_hover` | read | Hover an element (menus, tooltips). | browser_tools |
| `browser_upload` | network | Attach workspace files to a file-upload field. | browser_tools |
| `browser_scroll` | read | Scroll the page or bring an element into view. | browser_tools |
| `browser_wait` | read | Wait for text to appear/disappear or a fixed time. | browser_tools |
| `browser_screenshot` | read | Screenshot the active tab and attach it to the conversation for the model to see. | browser_tools |
| `browser_tabs` | network | Tabs and history: list (marks the agent's vs the user's tabs) / new / select / close (own tabs freely, the user's ask) / back / forward / reload. | browser_tools |
| `browser_handoff` | read | Hand control to the user (captcha, 2FA, sign-in); waits for "Done". | browser_tools |
| `browser_downloads` | read | Downloads land in quarantine and are checked (Defender, disguised executables, risky types); list / move to Downloads or the workspace (approval; risky files always ask) / delete. | browser_tools |

## Data & databases

| Tool | Cat | Description | Source |
|------|-----|-------------|--------|
| `profile_data` | read | Auto EDA of CSV/Parquet/JSON via DuckDB (types, nulls, uniques, stats, issues). | data_tools |
| `query_data` | read | Read-only SQL over a data file via DuckDB (file exposed as table `data`). | data_tools |
| `db_schema` | read | SQLite schema: tables, columns, keys, row counts. | db_tools |
| `db_query` | execute ⚠ | SQL against SQLite (SELECT read-only & unconfirmed; writes need approval). | db_tools |
| `db_diagram` | read | ER diagram of a SQLite DB as Mermaid. | db_tools |
| `analyze_statement` | read | Parse a bank statement (CSV/XLSX): categorize, totals, top spends, per-month. | analytics_tools |
| `create_chart` | edit | Build an interactive chart and save a self-contained HTML. | analytics_tools |

## Vision & media

| Tool | Cat | Description | Source |
|------|-----|-------------|--------|
| `view_image` | read | Describe/answer questions about an image in the workspace via a multimodal model. | vision_tools |
| `show_image` | read | Show a specific image inline in the reply. | vision_tools |
| `screenshot_ui` | network ⚠ | Screenshot a web page / local HTML into the Preview panel (full page, element, sizes). | vision_tools |
| `audit_ui` | network ⚠ | Screenshot a UI and run a multimodal visual audit (layout defects + verdict). | vision_tools |
| `video` | edit ⚠ | ffmpeg ops: info / trim / compress / convert / vertical / extract_audio / frame. | media_tools |

## Canvas & generative UI

| Tool | Cat | Description | Source |
|------|-----|-------------|--------|
| `show_graphic` | read | Render vector graphics (full `<svg>`) inline. | canvas_tools |
| `show_interactive` | read | Render an interactive HTML+CSS+JS widget inline (auto-height, `sendPrompt`). | canvas_tools |
| `show_ui` | read | Compose a clean UI from a strict component library (auto theme, responsive). | canvas_tools |
| `attach_file` | read | Attach any ready file inline (image/video/audio play in chat; docs as chips). | canvas_tools |

## Memory & context

| Tool | Cat | Description | Source |
|------|-----|-------------|--------|
| `remember` | read | Save a durable fact/lesson to global or folder memory. | memory_tools |
| `recall` | read | Search long-term memory about the user/projects. | memory_tools |
| `memory_view` | read | List memory items with numbers (global / folder). | memory_tools |
| `memory_replace` | edit | Replace a memory item (by index, or find/replace substring). | memory_tools |
| `memory_remove` | edit | Remove a memory item (by index or substring). | memory_tools |
| `suggest_memory` | read | Propose a fact for the user to confirm before saving. | memory_tools |
| `search_chats` | read | Search saved chats for what was discussed/answered before. | memory_tools |
| `context_info` | read | Context-window usage breakdown. | context_tools |
| `context_compress` | edit | Replace old history with a self-written summary, keeping the last N messages. | context_tools |
| `context_drop` | edit | Drop heavy items from context (tool results / images) without touching the visible chat. | context_tools |

## Reminders & conditions

| Tool | Cat | Description | Source |
|------|-----|-------------|--------|
| `set_reminder` | edit | Reminder at a relative or absolute time. | reminder_tools |
| `list_reminders` | read | List active reminders/conditions with ids. | reminder_tools |
| `cancel_reminder` | edit | Cancel a reminder/condition by id. | reminder_tools |
| `watch_condition` | edit | One-shot notify when a PC signal meets a condition (network/battery/charging/phone_online). | reminder_tools |

## Planning & skills

| Tool | Cat | Description | Source |
|------|-----|-------------|--------|
| `update_plan` | read | Create/update the interactive step checklist for a task. | plan |
| `write_plan` | edit | Write an implementation_plan.md before a complex change (shown to the user). | spec_tools |
| `ask` | read | Ask the user structured questions with options (single/multiple/ranking). | ask |
| `list_skills` | read | List available skills. | skills_tools |
| `read_skill` | read | Read a skill's full instructions. | skills_tools |
| `create_skill` | edit ⚠ | Create a new reusable skill. | skills_tools |
| `spawn_subagent` | execute ⚠ | Run a nested agent with a fresh context (needs subagents enabled). | subagent_tools |

## Secrets

| Tool | Cat | Description | Source |
|------|-----|-------------|--------|
| `list_secrets` | read | Which secrets are set (names + masked values). | secret_tools |
| `request_secret` | read | Ask the user to enter a secret via a secure form (stored in `.env`, unseen by the model). | secret_tools |

## Phone bridge

| Tool | Cat | Description | Source |
|------|-----|-------------|--------|
| `phone_ask_user` | network | Ask the phone's user a question and wait (bridge only). | bridge_tools |
| `phone_capability` | network | Ask the phone to do what the PC can't (geolocation, sensors, notification, clipboard). | bridge_tools |
| `phone_request_file` | network | Ask the phone's user to send a file (lands in `inbox/`). | bridge_tools |
| `phone_request_photo` | network | Ask the phone's user to take and send a photo (lands in `inbox/`). | bridge_tools |

## Android dev

| Tool | Cat | Description | Source |
|------|-----|-------------|--------|
| `android_diagnose` | read | Check Android SDK / adb / emulator readiness (run first). | android_tools |
| `android_devices` | read | List connected Android devices/emulators via adb. | android_tools |
| `android_avds` | read | List available AVDs. | android_tools |
| `android_start` | execute ⚠ | Start an external Android emulator. | android_tools |
| `android_stop` | execute ⚠ | Stop the emulator started by this app. | android_tools |
| `android_install_apk` | execute ⚠ | Install an APK on the emulator. | android_tools |
| `android_screenshot` | read | PNG screenshot of the emulator into Preview. | android_tools |
| `android_logcat` | read | Last logcat lines for UI errors/crashes. | android_tools |

## STEM & study

| Tool | Cat | Description | Source |
|------|-----|-------------|--------|
| `solve_math` | read | Exact math via SymPy (equations, calculus, simplify, limits, matrices) with LaTeX. | stem_tools |
| `create_anki_deck` | edit | Build an Anki `.apkg` deck from Q/A pairs. | stem_tools |
| `format_bibliography` | read | Format a bibliography per GOST R 7.0.100–2018. | stem_tools |

---

## Update rule

**Whenever a PC agent tool is added or removed, update this file in the same
change** — add/remove its row in the right section and bump the total count and
the "last updated" date. Descriptions here are short English summaries; the
authoritative per-tool description (shown to the model) lives in the tool's
`description` in `core/tools/builtin/`. To re-derive the full list from code:

```bash
python -c "from core.tools.builtin import builtin_tools; [print(t.name, '·', type(t).__module__.split('.')[-1]) for t in builtin_tools()]"
```
