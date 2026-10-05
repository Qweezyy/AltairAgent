# Changelog

All notable changes to **Altair** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is below
`1.0.0`, minor releases may include breaking changes as the project is still in early development.

> This is the single **product** version for the whole project (PC + Android), kept in the
> root `VERSION` file. The PC app, its updater, the desktop shell and the Android app all read
> or mirror it.

## [Unreleased]

_Changes landing on `main` but not yet part of a tagged release go here._

### Added
- **`altair` became a full terminal agent.** The conversation flows into the terminal's scrollback and
  the input stays at the bottom with a status line (model, approval mode, context, cost) and a live
  indicator of what the agent is doing. Answers are Markdown with highlighted code, an edit shows its
  diff with the file's line numbers, a command its last lines of output, the plan a checklist.
  Approvals and the agent's questions are picked with the arrow keys, with the change shown in the
  picker. `/` completes commands, `@` files of the folder; history, multi-line input, Esc stops the
  agent, Shift+Tab switches the approval mode. New commands: `/resume` (a picker), `/model`, `/mode`,
  `/reasoning`, `/context`, `/cost`, `/diff`, `/undo`, `/init`, `/memory`, `/skills`, `/mcp`,
  `/secret`, `/out`, `/history`, `/rename`, and the quick commands from the settings. Pipes, `-p`
  and `ALTAIR_PLAIN=1` keep the plain line mode.

### Fixed
- Installing an update showed "downloading started" and then nothing: the dialog now shows each
  stage — the download in MB and percent with its speed, the signature check, unpacking — and the
  reason if it fails.
- An update download whose connection froze never finished (seen: stuck at 16 of 148 MB). A
  download that sends nothing for 20 s is continued from where it stopped on a new connection.
- Web links in the desktop window (the release notes' "English · Русский", links in answers)
  could do nothing: they now open in the system browser.

## [0.2.1] — 2026-10-04

Steadier and cheaper answers: a set reasoning level, honest failures and backup models — and an Android
app that keeps up with the PC: reliable model calls, video and audio to the model, chats that remember.

### Added
- **The reasoning level is set, not left to the provider.** Settings → Agent → Reasoning level:
  Adaptive (default: low, and high for the step right after a failing test or tool error), Low,
  Medium, High, or the provider's default. Sent in each provider's own dialect (OpenRouter
  `reasoning.effort`, OpenAI `reasoning_effort`, Z.ai-style `thinking`). In tests on hard tasks with
  hidden tests the adaptive level solved as many as High (17 of 18) at about a third of the cost of
  the provider's default and many times faster; without a level the model sometimes thought for
  14 minutes and 33K tokens in one step. Chat titles, routing and history summaries ask for the least.
- **Backup models.** Settings → Agent → Backup models (comma-separated). When the main provider
  fails, or sends its first byte later than 40 s twice in a row, the next steps go to the backup for
  10 minutes, then the main one is tried again. With a backup set the main model gets two attempts
  instead of five, so a step moves on instead of waiting out retries.
- **Fewer requests in flight when a provider asks for it.** When a provider refuses with "too many
  concurrent requests", the app holds fewer requests to it at once and climbs back by itself
  (or set a fixed number in Settings → Agent).
- `tool_output`: the exact output of an earlier tool call that was cleared from the context. Cleared
  outputs now say how to get them back instead of "run the tool again", and one step before old
  outputs are cleared the agent is told to note what it still needs.
- The answer's footer shows how much of the prompt came from the provider's cache.
- Android: model calls are as reliable as on the PC — no cut-off of long healthy answers (silence and
  first-byte timeouts instead), gateway error texts and empty or runaway answers retried instead of shown,
  a "Reasoning effort" setting (adaptive by default: cheaper and faster), fallback models that take over
  while the main one is slow or down, and a per-provider limit on parallel requests. The system prompt
  stays the same within a chat, so providers serve the history from cache.
- Android: the context window is set when adding a model (128K, 1M, …); the context ring around the send
  key follows the key's shape and fills against that window; the chat menu shows context use and cache share.
- Android: formulas in answers ($…$, $$…$$, \(…\), \[…\], ```math) drawn with KaTeX, ```mermaid
  diagrams, and rare markdown: strikethrough, ==highlight==, sub/superscript, inline HTML tags, task lists,
  nested quotes, GitHub alerts, footnotes, <details>, autolinks and escapes.
- Android: a long press on your message opens a ChatGPT-style menu (time, copy, select, edit, share,
  branch, take back); under each answer: copy, read aloud, share and ⋮ for the rest. A long press on an
  answer selects text, with the app's actions (simpler, translate, quote, board, memory) in the toolbar.
- Android: top bar as in ChatGPT — history on the left, new chat and ⋮ chat actions on the right; a swipe
  to the right opens the history, which is ordered by each chat's newest message.
- Android: attach as many photos, videos, audio and files as you like to one message (up to 100, mixed),
  pick several at once, remove any of them before sending or while editing.
- Android: a dropped connection no longer restarts the answer — the model picks it up exactly where it
  stopped, in the same message. If every retry fails, the written part stays, the reason is shown under it,
  and "Continue the answer" finishes it later. A stream that just ends without the model finishing now
  counts as a break instead of passing for a complete answer.
- Android: pinch, pan and double-tap zoom for photos and videos in the full-screen viewer (up to 6x).
- Android: long user messages fold after about eight lines, with "Show more" / "Show less". The model's
  answers are always shown in full.
- Android: answers are set airier, and Settings → Appearance has an "Answer line spacing" slider with a
  live sample.
- Android: answer markup understands rules (`---` of any length, `***`, `___`), all six heading levels
  (with closing hashes), `===` underlined titles, `+` bullets and nested lists; a bare `###` no longer shows
  as hashes.
- Android: editing a sent message can remove or replace its photo, video or file before sending it again.
- Android: Settings → Appearance → Text size (70–200 %) for the whole app.

### Changed
- `read_file` numbers lines as `12|text` instead of `    12 | text`: 14% fewer tokens for the most
  used tool's output, same editing accuracy.
- Deferred tools load by family: one browser, dev-server, Android or code-quality tool brings its
  family, so the provider's prompt cache is reset once instead of once per tool.

### Fixed
- Android: after a restart or switching to an older chat, the model got none of the conversation — only
  the new message. The chat history is now loaded into the model before the next answer.
- Android: switching between answer versions (‹ 1/3 ›) now changes what the model continues from; before,
  it kept answering after the last generated version.
- Android: editing a message works as in ChatGPT — the message goes back into the input (with its
  attachments) under an "Edit message" bar, its answer steps aside, and ✕ or Back restores everything.
- Android: the context window of a saved model can be changed (pencil next to it); every chat picks it up
  on its next answer, and switching to a model with the same window keeps the cached prompt. The model
  list shows each model's provider host.
- Android: the last English-only labels (endpoint field, code block, alert and details titles) are
  translated.
- Android: video, audio and PDFs reach the model itself when its profile accepts them (Video / Audio /
  Files), sent as "file" parts — the form GateYourWay passes on (it refuses media in image_url and drops
  video_url/audio_url). Before, only photos went in and the model got a bare file path, so Gemini could
  not watch a video at all. Up to ~14 MB per message; anything larger is pointed to with the reason.
- The agent no longer reports a task as done when the provider did not really answer: a gateway
  error sent as the reply ("The request could not be completed…"), an empty stream, a stream cut
  at the loop guard or a reply to a prompt the provider did not process is retried like a dropped
  connection (and never shown in the chat). In live runs these made about 15% of the "finished"
  tasks. A model that loops twice in a row now ends with an honest error.
- An attempt whose first byte does not come within 90 s is given up and retried; a stream that is
  flowing is never cut by a total time limit.
- History summaries of reasoning models came back empty (the reasoning used the 600-token limit) and
  the oldest part of a chat was dropped instead of folded.
- With Anthropic models a note in the middle of a chat ("the user stopped this task") stays where it
  happened instead of moving into the system prompt for good.
- Android: answers from providers that put `null` into stream fields ("usage": null, "reasoning": null,
  a null delta) no longer fail with "JsonNull is not a JsonObject" after every retry.
- Android: rewinding to your own message takes it back — it leaves the chat with the AI's reaction on it,
  and its text and attachments return to the input. Regenerating or editing a message also drops the
  reaction the replaced answer put on it.
- Android: stopping an answer removes it whole (and cleans the half-made step out of the conversation);
  the unanswered message then offers "Regenerate" in its menu.
- Android: saved memory notes are framed as background facts in the prompt — the system rules and the
  conversation take precedence over them, and a contradicting note gets corrected.
- Android: full-screen pages (settings, plugins, library, the vault, the canvas) are drawn in the app window
  instead of Dialog windows, so they cover the whole display on every phone.
- Android: the PC chip turns "online" again — a successful check was read as a timeout. Tapping the chip
  re-checks at once.
- Android: rewinding, editing or branching keeps attachments: photos go to the model again, files move with
  a branched chat, and photos are stored in the chat folder instead of the cache.
- Android: Alti next to the running status is sized to the screen.
- Android: an answer is no longer printed twice when the connection drops mid-stream and the request is
  retried.
- Android: quick replies are recognised in the forms models really send and no longer carry over into a
  new chat.
- Android: full-screen pages no longer leave a strip above the navigation bar on some phones.
- The Windows update swap now copies every file of the new version even when robocopy would call
  it unchanged (the same size and time, or only another NTFS change time). Real updates were not
  affected — unpacked files always carry a new time — but nothing is left to chance now.

## [0.2.0] — 2026-10-01

Everywhere: the desktop app for Windows, Linux and macOS, and the same agent in a terminal.

### Added
- **A premium look.** Larger type and controls, surfaces set like plates in a tray, thin icons,
  text optically centred in every control, spring motion, and the accent used on purpose: primary
  actions, focus, the selected chat and settings section, what is running. Nothing blurs over the
  moving sky any more. Settings → Appearance → Interface style → Classic brings back the look from before.
- **A resizable chat rail.** Drag its right edge like the panes on the right; the width is
  remembered, a double click brings back the default.
- **Alti, the mascot, around the app.** In empty lists and settings pages (asleep), next to the
  running status (thinking), on errors (sad) and on cards that need you (asking), with a blink,
  a breath and a small hop; still when the system asks for reduced motion.
- **Themes and fonts.** Five themes (Black, Dark, Graphite, Light, White) or the system's, and a choice
  of bundled fonts (Geist by default, Inter, Onest, Wix Madefor, Golos Text, Manrope or the system font;
  code in JetBrains Mono) in
  Settings → Appearance.
- **Your profile.** The row of buttons at the bottom of the chat list is one button with your avatar and
  name; it opens the settings (theme and updates moved there, "Recently deleted" to the ⋯ menu). Set any
  name and picture; the agent calls you by that name.
- **Android: the premium look of the PC app.** Geist and JetBrains Mono bundled, the PC themes
  (Black, Dark, Graphite, Light, Snow), plates with a hairline and an outer tray for the composer,
  cards and settings, a neutral bubble for your messages, a gradient send key, code blocks with a
  language header, and the menu laid out like the PC chat rail with your profile at the bottom.
  The welcome screen gets starters that fill the input, and Alti lives around the app: asleep in
  empty places, thinking next to the running status, asking on approval cards, blinking when idle.
- A round of tools that ran the same step several times says it once: "Ran a command 3 times".
- The built-in quick commands follow the interface language (`/tests` and `/тесты` both work).
- **Terminal: `altair`.** The same agent and chats in a terminal: an interactive mode, one-shot tasks for
  scripts and CI (`-p`, the task from stdin, `--output-format json|stream-json`, exit codes 0/1/2),
  continuing the latest chat (`-c`) or any chat by id or title (`-r`), also one started in the window.
  When the desktop app runs, `altair` joins it (a chat shows up live in both); otherwise it starts the
  backend in the background. `install.ps1` puts it on PATH.
- **Long chats open fast.** A chat opens at its end and draws earlier parts as you scroll up to them.
- **Old file writes stop filling the context.** With the old tool outputs, the text of old
  `write_file` / `edit_file` / `apply_patch` calls leaves the model's view (the file holds it; the chat
  keeps everything).
- **Linux and macOS builds** from CI (`Build desktop` workflow): the backend, the Tauri window and the
  terminal command in one zip per system (`…-linux-x64.zip`, `…-macos-arm64.zip`). On Linux the whole
  test suite passes and gates the build. There the terminal panel uses a POSIX pty, search uses the
  system's ripgrep (or the built-in search), and the agent's browser is Chrome or Chromium over CDP
  streamed into the panel (the native browser tabs are WebView2, Windows only). The macOS build is
  not signed by Apple: open it with right click → Open the first time.
- **Chats work in the background.** A task keeps running when you open another chat, reload the
  window or minimize the app; the chat list marks the chats at work, and opening one shows its run
  live. Approvals and questions of a chat nobody has open wait for you, with a notice naming the chat.
- **Reminders and waits wake the agent wherever the chat is.** A reminder, a condition, the end of
  a background job or a watch now gives the agent a turn in its own chat and it carries on — also
  when the chat is not on screen. What came due while the app was closed arrives on the next start
  (saying how late), and ones still ahead keep waiting. A `wait_for` cut off by closing or updating
  the app ends after the restart and wakes the chat. New: repeating reminders (stop after 7 days at
  most), "only show it" reminders, `run_background(notify=true)` instead of polling a build. A busy
  chat gets the notice at its next step; the taskbar button flashes when the app is in the background.
- **Self-update from GitHub releases.** The app checks the project's latest release by default and
  installs it by itself only when the release's checksums carry a valid signature of the project's
  key; otherwise it offers the release page. The swap waits for the app to close, keeps your own
  files in the app folder and restarts it — on Windows, Linux and macOS, each taking its own
  package. `UPDATE_URL=off` turns the checks off. 0.2.0 is the first version that updates itself.

- **Memory as notes with an index.** Each thing the agent remembers is a small markdown note with a
  header (title, one-line gist, kind, created/modified dates) in a memory folder — global (about you)
  and per project (`.agent/memory/`) — plus `MEMORY.md`, one line per note. Only the indexes are in
  the agent's context; it opens a note for the details, updates an existing note instead of adding a
  twin, and may change or delete a note only after reading it (and re-reads it if you edited the file).
  Existing memory is moved over on first start.

### Fixed
- Android: the app no longer crashes on start when the network changes while the PC bridge is
  set up (the presence check ran before its channel existed).
- In the desktop app `execute_command` hung before running anything (even `Write-Output`): the
  backend's stdin is now the shell's pipe, and on Windows a child inheriting it hung. Children now
  get their own empty stdin.
- The stop button ended only a command's PowerShell: what the command had started kept running.
  Now the whole process tree stops, and tool rows still spinning when a run stops are marked stopped.
- The agent's browser screenshots of a tab opened while the browser panel was closed came back as a
  single pixel (the tab had no size); hidden tabs now keep a real size.
- Switching chats while the agent worked wrote the rest of its run into the chat on screen, and
  its own chat lost it.
- Closing the app killed the backend on the spot: the last seconds of a running task were lost and
  it looked like a crash. Now the backend stops the runs as "the app closed", stores every chat and
  offers to continue the task on the next start.
- A chat stored in the middle of a tool call (after a crash) was rejected by the model provider on
  the next message; the unanswered call now gets a "no result" answer.
- Stopping the agent keeps the history whole (finished tool results, the part of the answer already
  shown), and the context ring updates live during a run.
- Typing in long chats is instant again.
- On Linux and macOS a backend whose window had died could live on forever, holding the port: a
  dead window process not yet reaped still looked alive. It now also checks the process state and
  being re-parented.
- On Linux the search could look through the bundled Windows `tgrep.exe`/`rg.exe` (under WSL they
  even start) and find nothing; elsewhere only native binaries are used, and the Windows ones are no
  longer packed into the Linux and macOS builds.

### Planned next
- **The Journal**: one read-only record of everything the agent did, in one event format for the
  window, the terminal and the server (moved from 0.2.0 to 0.3.0).
- **Native packages** for Linux and macOS (AppImage/deb, dmg) instead of a zip.
- **A server agent** ("second master"): Altair on a headless server that the desktop and the phone
  connect to, so long tasks keep running while the PC is off.
- **iOS** for the phone agent — explored via Kotlin Multiplatform (the Android core/tools/LLM
  layers are already Android-independent Kotlin); the open questions are background autonomy
  (iOS limits background work heavily) and distribution (TestFlight / sideloading).

## [0.1.3] — 2026-09-28

Nothing of a conversation is lost any more, the pairing QR code works, and the approval mode
belongs to each chat.

### Changed
- **Nothing of a conversation is lost any more.** The chat feed kept only its last 400 entries,
  and trimming old tool outputs, dropping stale page states and folding the start of a long chat
  edited the stored messages. Now the chat file keeps every message word for word; the model gets
  a shortened view of it, and the agent can search the folded part.
- **The approval mode belongs to the chat.** A mode you pick stays with that chat across chat
  switches, restarts and updates; new chats start in the default (manual), so turning off
  confirmations in one chat never turns them off everywhere.
- Your own messages render as Markdown (code, lists, tables, links, math); in the input,
  Ctrl+B / Ctrl+I / Ctrl+E wrap the selection in bold, italic or inline code.
- Chats named after their first line by older versions get a proper title from the model the
  next time you write in them (titles you set yourself are kept).

### Fixed
- Logins imported from Firefox were lost on every restart: recent Firefox stores cookie expiry in
  milliseconds, which made every imported cookie a session cookie. Import the sites once more
  after updating. The fallback Chrome is also closed cleanly now, so its latest cookies are saved.
- A thinking block opened while the model was thinking could not be closed again; thinking and
  finished rounds of tools now fold into a one-line summary.
- The log went silent for the rest of a session when Windows refused to rotate it (another copy
  of the app had it open).
- **The phone pairing QR code can be scanned again.** The page cropped it to a corner, so no
  scanner could read it; it is now shown whole, larger and sharper, and less dense.

## [0.1.2] — 2026-09-27

A lighter, more honest context window and a browser that costs far fewer tokens.

### Changed
- **Browser actions return only what changed.** After a click, typing or a key press on the same
  page the model gets the changed lines instead of the whole page again; the full page still comes
  on navigation, a new tab or a large change (`BROWSER_SNAPSHOT_DIFF=false` turns it off). Page
  snapshots are also leaner. On a real 98-step browsing chat, page states went from 355K to 151K
  tokens (−58%).
- **Context ring shows the provider's own count** of the latest request (system prompt and tool
  schemas included), e.g. "412K of 1M", instead of a character estimate.
- Superseded page snapshots and tool screenshots are dropped from the history (the newest two
  stay), and old tool outputs are trimmed past 100K tokens instead of past half the window.
- The built-in browser opens once per task; if you close it, the agent keeps browsing in the
  background until you open it again.

### Added
- `browser_upload` fills file fields directly, without the OS file dialog (uploads on sites like
  habr.com used to time out and fall back to the user).

### Fixed
- `CONTEXT_TOKEN_BUDGET=1M` in `.env` stopped the app from starting, and "1,000,000" was read as 1
  in Settings. Values like `1M`, `200K`, `1 000 000`, `1 млн` now work on both sides.
- Plain-path links in answers (`dir/file.md`, `D:\x.md`) navigated the whole window to a blank JSON
  404 page. They now open in the Files tab, and the window never navigates away.
- Markdown files in the Files tab render as documents (with a page/code switch) instead of source.
- Two tree refreshes at once listed every file twice in the Files tab.
- State files (chats, memory, presets, permissions, providers, settings and others) no longer lose a
  write when two threads save the same file at once — the same class of race as the rollback fix in
  0.1.1. All of them now save through one shared atomic-write helper with a lock per file.
- `version_sync.py` also stamps `Cargo.lock` and `package-lock.json`, so a version bump never leaves
  the lock files behind.

## [0.1.1] — 2026-09-27

The first update after the public release: honest progress captions, chat titles and search, and a
browser that copes with always-on VPNs.

### Added
- **Chat titles:** the model names each chat from the conversation; chats can be renamed, and search
  jumps straight to the matching message.
- **Per-model API keys:** every model can have its own key and provider.
- **Browser networking:** the built-in browser picks a direct connection or the VPN per site — sites
  blocked directly go through the VPN, while an always-on VPN no longer breaks sites that need a
  direct connection.
- **Commands outside the working folder:** the agent can run a command in another folder when a task
  needs it. Every such command asks for approval — even read-only ones and in automatic modes — the
  approval card marks it as outside the working folder, and it is not covered by rollback. File tools
  stay inside the sandbox.

### Fixed
- **Whole-run rollback could skip a file.** When the agent edited several files in parallel, two
  snapshots saved at the same moment could overwrite each other in the manifest, and "roll back the
  run" then silently left one of the files changed. Snapshot saves are now serialized, with a
  regression test that reproduces the race deterministically.
- Step captions no longer claim an action happened before it was approved (a pending write read
  "Created hello.txt" while still waiting for confirmation).
- Approval cards describe the concrete action instead of a generic "will change your system".
- Chat titles and the new-chat row in the sidebar now work in the packaged app.

## [0.1.0] — 2026-09-26 (first public release)

The first public, **early-stage** release. Expect rough edges — see the disclaimer in the README.

### PC agent (Python, Windows)
- Agent loop with validated tools: files, search, shell, Python, web, deep research, code map / LSP,
  dev server, charts, `solve_math`, and an eval harness.
- **Safe autonomy ("leave it alone"):** per-edit snapshots and a "shadow git" for one-click undo,
  full-run rollback with a change audit, a health-gate that runs your checks before finishing
  (with auto-rollback), a time budget, resume of an interrupted run, and an independent step
  verifier with risk tiers.
- **Models:** any OpenAI-compatible provider or a native Anthropic client (BYOK); fallback models
  that take over when the main one fails; routing that splits a task between a cheap and a strong
  model.
- **Token economy:** tools load on demand instead of all schemas up front; old tool outputs are
  trimmed; long dialogues are summarized instead of dropped.
- **Built-in browser:** native WebView2 tabs in the app window, shared with the agent (driven over
  CDP), with a download quarantine.
- **Desktop app:** native Tauri shell, a Files tab (workspace explorer), a terminal, Mermaid and
  math in answers, attachment thumbnails, soft-deleted chats with a 30-day trash.
- **Extensibility:** MCP servers — local or remote by URL — connect live from Settings; skills
  import from a `SKILL.md`, a `.zip` or a folder.
- **Security:** path sandbox and permission modes; prompt-injection framing of external content;
  in LAN mode every remote request (REST, files, terminal) requires the bridge token.
- English-first UI with a language switch, a first-run onboarding wizard and a mascot.
- One-command Windows installer (`install.ps1`); works without Python installed.

### Android app (Kotlin / Compose)
- Chat with streaming, models, memory, and a bridge to the PC (QR pairing on the local network).
- Skills and MCP servers on par with the PC agent (create/import/delete, paste JSON, one-way
  sync from the PC).
- Security hardening: secrets encrypted with Android Keystore, a network policy, secrets used only
  with permission.
- English system prompt with on-demand tools; full UI translation with a language picker.
- Space-themed design with an animated star background and a launcher-icon picker; full/lite builds.

### Project
- English README with an honest status, Apache-2.0 license, `ARCHITECTURE` / `EXTENDING` / tool
  catalog docs.
- CI on every push (PC tests + evals on Windows, Android build, repo hygiene) and an independent
  release acceptance suite (`release_tests/`).

### Known issues
- The PC agent is **Windows-only** for now (Linux and macOS are planned).
- The Windows build is **not code-signed** — SmartScreen may warn on first launch.
- Some server-side messages and descriptions of on-demand tools are still in Russian.
- Phone ↔ PC works only on the same local network (e.g. one Wi-Fi); a shared Tailscale network works
  as a workaround.
- Scanning the pairing QR code from the phone does not work yet — pair by entering the PC address and
  bridge token manually. A fix is in progress.
- Phone ↔ PC chat sync is not there yet (specified, planned after 0.1.0).
- Auto-update is off until an update feed is published.
- Python on PATH is needed for the agent's own code checks (tests/linters) in your projects.

[Unreleased]: https://github.com/Qweezyy/AltairAgent/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/Qweezyy/AltairAgent/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Qweezyy/AltairAgent/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/Qweezyy/AltairAgent/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/Qweezyy/AltairAgent/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Qweezyy/AltairAgent/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Qweezyy/AltairAgent/releases/tag/v0.1.0
