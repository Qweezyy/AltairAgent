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

### Planned for 0.2.0+
- **Linux support** for the PC agent (replace Windows-only pieces: bundled `.exe` grep with
  system `ripgrep`, ConPTY terminal with a `pty`-based one, packaging as AppImage/deb).
- **macOS support** for the PC agent (Unix path, `.icns` already in place).
- **iOS** for the phone agent — explored via Kotlin Multiplatform (the Android core/tools/LLM
  layers are already Android-independent Kotlin); the open questions are background autonomy
  (iOS limits background work heavily) and distribution (TestFlight / sideloading).

## [0.1.0] — Unreleased (first public release)

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

[Unreleased]: https://github.com/Qweezyy/AltairAgent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Qweezyy/AltairAgent/releases/tag/v0.1.0
